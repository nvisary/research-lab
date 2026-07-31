"""Shared signal-layer + portfolio engine for pump_dump_v2 notebooks.

Single source of truth so notebooks don't re-paste (and drift). 1:1 with the
etalon nb00 / pump_dump_combined nb_best. Train-only: reads the rebuilt signal
caches, never OOS/holdout.

    from _engine import load_signals, build_book, run_dca, metrics, xirr, CST
    PUMP, DUMP = load_signals()
    BOOK = build_book(PUMP, DUMP)
    R = run_dca(BOOK, f_pump=0.05, f_dump=0.03)
"""
from __future__ import annotations
import numpy as np, pandas as pd, heapq
from sklearn.ensemble import HistGradientBoostingRegressor
from scipy.optimize import brentq

# ── сигнальный слой ──────────────────────────────────────────────────────────
GIVEBACKS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.08, np.inf]
PUMP_G = DUMP_G = 6                       # hold-to-horizon = эталон
PF = ["r1","r3","r5","r15","r30","accel5","surge15","surge1","rvol60","rng5","dist_hi240","dist_lo60"]
DF = ["r1","r3","r5","r15","r30","r60","accel","surge15","surge1","volreg","rng15","hi_d","lo_d","dstreak"]
CST = {"pump": 0.30, "dump": 0.20}        # катастроф-стоп по ноге (для детекта стопа: pnl <= -CST)


def naive(s):
    s = pd.to_datetime(s)
    return s.dt.tz_localize(None) if s.dt.tz is not None else s


def load_signals(root="_out"):
    PUMP = pd.read_parquet(f"{root}/pump_signals.parquet")
    DUMP = pd.read_parquet(f"{root}/dump_signals.parquet")
    for D in (PUMP, DUMP):
        D["entry"] = naive(D["entry"])
        for i in range(len(GIVEBACKS)):
            D[f"exit{i}"] = naive(D[f"exit{i}"])
    return PUMP, DUMP


def wf_filter(df, feats, label):
    """Walk-forward классификатор: экспандинг-окна 40/55/70/85/100%, HGBR по
    причинным фичам, торгуем pred>0; первые 40% истории без фильтра (pred=NaN)."""
    pred = np.full(len(df), np.nan)
    cuts = [int(len(df)*q) for q in (0.40, 0.55, 0.70, 0.85, 1.0)]; prev = cuts[0]
    for cut in cuts[1:]:
        tr = df.iloc[:prev]
        m = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=300,
            l2_regularization=1.0, min_samples_leaf=50, random_state=0).fit(tr[feats], tr[label])
        pred[prev:cut] = m.predict(df.iloc[prev:cut][feats]); prev = cut
    out = df.copy(); out["pred"] = pred
    return out[(pred > 0) | np.isnan(pred)]


def build_book(PUMP, DUMP, filtered=True):
    """Единая книга сделок. filtered=True → с walk-forward классификатором (эталон)."""
    pa = PUMP.assign(pnl=PUMP[f"pnl{PUMP_G}"], exit_ts=PUMP[f"exit{PUMP_G}"])
    da = DUMP.assign(pnl=DUMP[f"pnl{DUMP_G}"], exit_ts=DUMP[f"exit{DUMP_G}"])
    if filtered:
        pa = wf_filter(pa, PF, "pnl"); da = wf_filter(da, DF, "pnl")
    cols = ["sym", "entry", "exit_ts", "pnl", "liq", "stream"]
    return pd.concat([pa[cols], da[cols]]).sort_values("entry").reset_index(drop=True)


# ── портфельный движок run_dca (1:1 nb00/nb07/nb15_2) ────────────────────────
FILL_MIN = 30; PART_CAP = 0.10; SPREAD = 0.0010; IMPACT = 0.10


def run_dca(df, ffrac=None, f_pump=0.05, f_dump=0.05, start=1000., monthly=200.,
            reserve=0.10, MAX=18, cap=True, slipmodel=True):
    """ffrac: опциональный массив per-trade долей (для soft-сайзинга / kill-switch);
    иначе доля по ноге f_pump/f_dump."""
    en = df.entry.values.astype("datetime64[ns]"); ex = df.exit_ts.values.astype("datetime64[ns]")
    bpnl = df.pnl.values; liq = df.liq.values; stream = df.stream.values
    cap_no = PART_CAP * liq * FILL_MIN
    months = pd.date_range(pd.Timestamp(en.min()).normalize().replace(day=1),
                           pd.Timestamp(ex.max()), freq="MS")
    cash = start; contrib = start; dep = 0.; units = start; openh = []; mi = 0
    nav_t = [pd.Timestamp(en.min())]; nav_v = [1.0]; cf = [(-start, pd.Timestamp(en.min()))]
    taken = 0; skipped = 0; capped = 0; slip = 0.; trec = []
    def snap(t):
        eq = cash + dep; nav_t.append(pd.Timestamp(t)); nav_v.append(eq/units)
    for i in range(len(bpnl)):
        now = pd.Timestamp(en[i])
        while mi < len(months) and months[mi] <= now:
            eq = cash + dep; nav = eq/units; units += monthly/nav; cash += monthly
            contrib += monthly; cf.append((-monthly, months[mi])); snap(months[mi]); mi += 1
        while openh and openh[0][0] <= en[i]:
            _, no, pn, st = heapq.heappop(openh); cash += no*(1+pn); dep -= no; snap(_)
        eq = cash + dep
        f = ffrac[i] if ffrac is not None else (f_pump if stream[i] == "pump" else f_dump)
        desired = f*eq
        no = min(desired, cap_no[i]) if cap else desired
        if cap and no < desired - 1e-9: capped += 1
        if f > 0 and dep + no <= (1-reserve)*eq and len(openh) < MAX and cash >= no and no > 1:
            part = no/(liq[i]*FILL_MIN); sl = (SPREAD + IMPACT*part) if slipmodel else 0.0
            slip += no*sl; cash -= no; dep += no
            heapq.heappush(openh, (ex[i], no, bpnl[i]-sl, stream[i])); taken += 1
            trec.append((bpnl[i]-sl, no, stream[i], pd.Timestamp(en[i]))); snap(en[i])
        else:
            skipped += 1
    while mi < len(months):
        eq = cash + dep; nav = eq/units; units += monthly/nav; cash += monthly
        contrib += monthly; cf.append((-monthly, months[mi])); mi += 1
    for _, no, pn, st in sorted(openh): cash += no*(1+pn); dep -= no; snap(_)
    final = cash + dep; cf.append((final, pd.Timestamp(ex.max())))
    nav = pd.Series(nav_v, index=pd.to_datetime(nav_t)).sort_index()
    nav = nav[~nav.index.duplicated(keep="last")].resample("1D").last().ffill()
    tk = pd.DataFrame(trec, columns=["pnl", "notional", "stream", "entry"]); tk["usd"] = tk.notional*tk.pnl
    return dict(final=final, contrib=contrib, nav=nav, taken=taken, skipped=skipped,
                capped=capped, slip=slip, tk=tk, cf=cf)


def xirr(cf):
    amts = np.array([a for a, _ in cf]); ds = pd.to_datetime([d for _, d in cf])
    t = np.array([(d-ds[0]).days/365.25 for d in ds])
    try: return brentq(lambda r: np.sum(amts/(1+r)**t), -0.999, 100)
    except Exception: return np.nan


def metrics(R, tag):
    nav = R["nav"]; span = (nav.index[-1]-nav.index[0]).days/365.25
    rr = nav.pct_change().dropna(); sh = rr.mean()/rr.std()*np.sqrt(365)
    sor = rr.mean()/rr[rr < 0].std()*np.sqrt(365)
    dd = (nav.values/np.maximum.accumulate(nav.values)-1).min()
    cg = (nav.iloc[-1]/nav.iloc[0])**(1/span)-1; tk = R["tk"]
    worst_day = nav.pct_change().min()*100
    return dict(config=tag, final=f"${R['final']:,.0f}", mult=f"{R['final']/R['contrib']:.1f}x",
                CAGR=f"{cg*100:+.0f}%", Sharpe=f"{sh:.2f}", Sortino=f"{sor:.2f}", maxDD=f"{dd:.1%}",
                worstday=f"{worst_day:.1f}%", worst=f"{tk.pnl.min()*100:.0f}%", taken=R["taken"])
