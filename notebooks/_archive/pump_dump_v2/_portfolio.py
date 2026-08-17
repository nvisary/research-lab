"""Portfolio layer, v2 — `run_dca` plus the things the paper bot showed we need.

`_engine.run_dca` is arithmetically sound (nb07 checks it against closed-form answers:
compounding, unit accounting, the MAX/reserve limits and the capacity cap all pass), but
it is blind to three things this line has now been bitten by:

  * CONCENTRATION. In the bot's 25 live days ESPORTSUSDT alone took 103 of 459 trades and
    lost $57 — more than the whole book's $5 loss. `run_dca` has a global slot limit and
    no per-symbol limit at all, so one cascading coin can own the book.
  * CASCADES. A symbol that just hit the catastrophe stop is the single most likely place
    for the next stop (nb01: LAB took 31 stops in 25 days). Nothing stops us re-entering.
  * SILENT SKIPS. `run_dca` lumps every rejection into one `skipped` counter, so a config
    that trades nothing looks identical to one that trades everything at a loss. The
    `no > 1` gate in particular makes small-deployment configs report `mult 1.0x` while
    placing zero trades — that is an artifact, and it must be visible, not inferred.

Also reported here: the NAV curve under BOTH attribution conventions. `run_dca` books a
trade's PnL entirely at its exit, so a drawdown that is being carried by open positions
only appears as they unwind — Sharpe and maxDD come out optimistic. There is no
mark-to-market path in the signal cache to fix this properly, but computing the curve a
second time with PnL attributed at ENTRY brackets the error: when the two agree the
convention is not driving the answer, and when they disagree neither number is safe.

`run_book` reduces EXACTLY to `run_dca` when the new limits are off — `check_parity()`
asserts it, so the extra features cannot silently change the baseline.
"""
from __future__ import annotations
import heapq
import numpy as np
import pandas as pd

FILL_MIN = 30; PART_CAP = 0.10; SPREAD = 0.0010; IMPACT = 0.10
SKIP_REASONS = ["reserve", "slots", "cash", "min_notional", "sym_gross", "sym_slots", "cooldown", "zero_size"]

# honest time split — declared before any schedule was scored, and not moved since
TRAIN = ("2024-01-02", "2025-06-30")
VALID = ("2025-07-01", "2026-01-31")
TEST = ("2026-02-01", "2026-07-31")       # holds the bot's live window; touch once


def load_v4(leg, root="notebooks/pump_dump_v2/_out"):
    D = pd.read_parquet(f"{root}/{leg}_signals_v4.parquet")
    D["entry"] = pd.to_datetime(D.entry)
    if D.entry.dt.tz is not None:
        D["entry"] = D.entry.dt.tz_localize(None)
    for c in [c for c in D.columns if c.startswith("exit_")]:
        D[c] = pd.to_datetime(D[c])
        if D[c].dt.tz is not None:
            D[c] = D[c].dt.tz_localize(None)
    return D


def window(D, w, col="entry"):
    return D[(D[col] >= w[0]) & (D[col] <= w[1] + " 23:59:59")]


def build_book_v4(PUMP, DUMP, sp, sd, filtered=True, feats=True):
    """One book, a different fill schedule per leg. The walk-forward classifier is
    trained on THAT schedule's pnl — filtering on a pnl you will not earn is meaningless."""
    from _engine import wf_filter, PF, DF
    out = []
    for D, s, F in [(PUMP, sp, PF), (DUMP, sd, DF)]:
        d = D.assign(pnl=D[f"pnl_{s}"], exit_ts=D[f"exit_{s}"],
                     frac=D[f"frac_{s}"], kused=D[f"kused_{s}"], stopped=D[f"stop_{s}"])
        d = d[d.exit_ts > d.entry]
        if filtered and feats:
            d = wf_filter(d, F, "pnl")
        out.append(d[["sym", "entry", "exit_ts", "pnl", "liq", "stream",
                      "frac", "kused", "stopped", "k"]])
    return pd.concat(out).sort_values("entry").reset_index(drop=True)


def ffrac(book, w_pump, w_dump, honest=True):
    base = np.where((book.stream == "pump").values, w_pump, w_dump)
    return base * (book.frac.values if honest else 1.0)


def run_book(df, ffrac=None, f_pump=0.05, f_dump=0.05, start=1000., monthly=200.,
             reserve=0.10, MAX=18, cap=True, slipmodel=True, min_notional=1.0,
             sym_gross=None, sym_slots=None, cooldown_min=0.0, stopcol=None,
             fixed_equity=False):
    """Replay a trade book as an account.

    df       : sym, entry, exit_ts, pnl, liq, stream  (sorted by entry)
    ffrac    : per-trade fraction of equity to commit; else f_pump/f_dump by leg
    sym_gross: max open notional on ONE symbol, as a fraction of equity (None = off)
    sym_slots: max concurrent positions on one symbol (None = off)
    cooldown_min: after a STOPPED trade on a symbol, block that symbol for this many
               minutes. Causal: keyed off the stop's exit time. Needs `stopcol`.
    stopcol  : name of a bool column marking stop-outs (for cooldown)
    fixed_equity: size every trade off the STARTING equity instead of the running one.
               Compounding and the DCA drip both inflate a terminal multiple and bend the
               risk numbers; with this on, the return series measures the edge alone and
               Sharpe/maxDD are directly comparable across configs. Use it as the primary
               read and let compounding be the secondary one.
    """
    en = df.entry.values.astype("datetime64[ns]"); ex = df.exit_ts.values.astype("datetime64[ns]")
    bpnl = df.pnl.values; liq = df.liq.values; stream = df.stream.values; syms = df.sym.values
    stopped = df[stopcol].values if stopcol else np.zeros(len(df), bool)
    cap_no = PART_CAP * liq * FILL_MIN
    months = pd.date_range(pd.Timestamp(en.min()).normalize().replace(day=1),
                           pd.Timestamp(ex.max()), freq="MS")

    cash = start; contrib = start; dep = 0.; units = start; openh = []; mi = 0
    nav_t = [pd.Timestamp(en.min())]; nav_v = [1.0]; cf = [(-start, pd.Timestamp(en.min()))]
    taken = 0; capped = 0; slip = 0.; trec = []
    skips = dict.fromkeys(SKIP_REASONS, 0)
    sym_open: dict = {}                       # sym -> [count, notional]
    blocked_until: dict = {}                  # sym -> np.datetime64

    def snap(t):
        nav_t.append(pd.Timestamp(t)); nav_v.append((cash + dep) / units)

    for i in range(len(bpnl)):
        now = pd.Timestamp(en[i])
        while mi < len(months) and months[mi] <= now:
            nav = (cash + dep) / units; units += monthly / nav; cash += monthly
            contrib += monthly; cf.append((-monthly, months[mi])); snap(months[mi]); mi += 1
        while openh and openh[0][0] <= en[i]:
            xt, no, pn, s, was_stop = heapq.heappop(openh)
            cash += no * (1 + pn); dep -= no
            rec = sym_open.get(s)
            if rec:
                rec[0] -= 1; rec[1] -= no
                if rec[0] <= 0:
                    sym_open.pop(s, None)
            if was_stop and cooldown_min > 0:
                blocked_until[s] = xt + np.timedelta64(int(cooldown_min * 60), "s")
            snap(xt)

        eq = cash + dep
        f = ffrac[i] if ffrac is not None else (f_pump if stream[i] == "pump" else f_dump)
        s = syms[i]
        desired = f * (start if fixed_equity else eq)
        no = min(desired, cap_no[i]) if cap else desired
        if cap and no < desired - 1e-9:
            capped += 1
        cnt, gross = sym_open.get(s, (0, 0.0))
        if sym_gross is not None:
            no = min(no, max(0.0, sym_gross * eq - gross))

        if f <= 0 or no <= 0:                                       reason = "zero_size"
        elif no <= min_notional:                                    reason = "min_notional"
        elif s in blocked_until and en[i] < blocked_until[s]:       reason = "cooldown"
        elif sym_slots is not None and cnt >= sym_slots:            reason = "sym_slots"
        elif sym_gross is not None and gross >= sym_gross * eq:     reason = "sym_gross"
        elif len(openh) >= MAX:                                     reason = "slots"
        elif dep + no > (1 - reserve) * eq:                         reason = "reserve"
        elif cash < no:                                             reason = "cash"
        else:                                                       reason = None

        if reason is None:
            part = no / (liq[i] * FILL_MIN); sl = (SPREAD + IMPACT * part) if slipmodel else 0.0
            slip += no * sl; cash -= no; dep += no
            heapq.heappush(openh, (ex[i], no, bpnl[i] - sl, s, bool(stopped[i])))
            rec = sym_open.setdefault(s, [0, 0.0]); rec[0] += 1; rec[1] += no
            taken += 1
            trec.append((bpnl[i] - sl, no, stream[i], pd.Timestamp(en[i]), pd.Timestamp(ex[i]), s))
            snap(en[i])
        else:
            skips[reason] += 1

    while mi < len(months):
        nav = (cash + dep) / units; units += monthly / nav; cash += monthly
        contrib += monthly; cf.append((-monthly, months[mi])); mi += 1
    for xt, no, pn, s, _ in sorted(openh):
        cash += no * (1 + pn); dep -= no; snap(xt)
    final = cash + dep; cf.append((final, pd.Timestamp(ex.max())))

    nav = pd.Series(nav_v, index=pd.to_datetime(nav_t)).sort_index()
    nav = nav[~nav.index.duplicated(keep="last")].resample("1D").last().ffill()
    tk = pd.DataFrame(trec, columns=["pnl", "notional", "stream", "entry", "exit", "sym"])
    tk["usd"] = tk.notional * tk.pnl
    return dict(final=final, contrib=contrib, nav=nav, taken=taken, capped=capped,
                skipped=sum(skips.values()), skips=skips, slip=slip, tk=tk, cf=cf,
                nav_entry=_nav_attributed(tk, start, monthly, contrib))


def _nav_attributed(tk, start, monthly, contrib):
    """Same book, PnL attributed at ENTRY instead of exit. Not a mark-to-market curve —
    a bracket on how much the exit convention flatters the risk numbers."""
    if not len(tk):
        return pd.Series(dtype=float)
    d = tk.groupby(tk.entry.dt.normalize()).usd.sum()
    idx = pd.date_range(d.index.min(), tk["exit"].max().normalize(), freq="1D")
    pnl = d.reindex(idx, fill_value=0.0)
    months = pd.Series(0.0, index=idx)
    for m in pd.date_range(idx[0], idx[-1], freq="MS"):
        if m in months.index:
            months[m] = monthly
    eq = start; units = start; out = []
    for t in idx:
        if months[t]:
            units += months[t] / (eq / units); eq += months[t]
        eq += pnl[t]
        out.append(eq / units)
    return pd.Series(out, index=idx)


def stats(R, tag="", nav=None):
    nav = R["nav"] if nav is None else nav
    span = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    rr = nav.pct_change().dropna()
    sh = rr.mean() / rr.std() * np.sqrt(365) if rr.std() > 0 else np.nan
    neg = rr[rr < 0]
    sor = rr.mean() / neg.std() * np.sqrt(365) if len(neg) > 1 and neg.std() > 0 else np.nan
    dd = (nav.values / np.maximum.accumulate(nav.values) - 1).min()
    tk = R["tk"]
    return dict(config=tag, final=R["final"], mult=R["final"] / R["contrib"],
                CAGR=(nav.iloc[-1] / nav.iloc[0]) ** (1 / span) - 1, Sharpe=sh, Sortino=sor,
                maxDD=dd, worstday=rr.min() if len(rr) else np.nan,
                worst=tk.pnl.min() if len(tk) else np.nan, taken=R["taken"],
                money=tk.usd.sum() if len(tk) else 0.0)


def fmt(s):
    return (f"{s['config']:<26s} mult {s['mult']:6.2f}x  Sh {s['Sharpe']:6.2f}  "
            f"Sort {s['Sortino']:7.2f}  DD {s['maxDD']:7.1%}  wday {s['worstday']:6.1%}  "
            f"n {s['taken']:6d}  ${s['money']:+10,.0f}")


def check_parity(df, **kw):
    """run_book with every new limit off must reproduce run_dca exactly."""
    from _engine import run_dca
    a = run_dca(df, **kw)
    b = run_book(df, **kw)
    same = (abs(a["final"] - b["final"]) < 1e-9 and a["taken"] == b["taken"]
            and a["skipped"] == b["skipped"] and abs(a["slip"] - b["slip"]) < 1e-9)
    print(f"  parity vs run_dca: final {a['final']:,.6f} / {b['final']:,.6f}  "
          f"taken {a['taken']}/{b['taken']}  skipped {a['skipped']}/{b['skipped']}  "
          f"{'PASS' if same else '**FAIL**'}")
    return same
