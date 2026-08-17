"""v4 signal builder — causal fill SCHEDULES whose total size does not depend on
how long the cluster runs, plus an honest running-average stop.

WHY (measured, not assumed — see nb07). Splitting the v3 book by TRUE cluster length k
shows both legs die in exactly the same way, and it is not a missing edge:

    pump  k=16-30 : bot ramp (base .05) -5.18% / 14.3% stops   etalon +0.48% /  5.4% stops
    pump  k=30+   :                     -24.63% / 58.0% stops          -4.08% / 18.8% stops
    dump  k=9-15  :                     -17.61% / 77.3% stops          +26.93% / 21.8% stops

The bot spends its whole budget in the first ~6 tranches of a cluster that is not over.
Two damages compound: (a) the average entry is stuck near the START of the move, and
(b) the catastrophe stop, which sits a fixed % from that average, is left directly in the
path of the continuing move. The etalon avoids both only because it averages over ALL
fills with rising weights — i.e. its average sits near the cluster's END. That is not a
sizing trick we can execute, but the PRICE PROFILE it implies is reachable causally:
put the weight at the end of the cluster instead of the beginning.

SCHEDULES (`frac` = fraction of the target actually deployed when the trade closes).
Two orthogonal dials: the SHAPE of the fill schedule, and an optional cluster-length ABORT.

    et      etalon, v3-exact: all fills, weights 1..k, frac=1, stop scanned from `last+1`
            off the FINAL average. Kept bit-identical to v3 as a validation anchor.
    et_rs   same weights, but the honest running stop — isolates the stop model alone.
    first   the whole target at the first trigger (control; known to lose on pump).
    b01 b05 the live bot: add_i = min(base*i*T, T - notional), base 0.01 / 0.05. frac<=1.
    endc    no ramp: the whole target at the CONFIRMED end of the cluster.
    e30     no ramp: the whole target at a fixed clock offset, bar st+30.
    rn03    ramp with the etalon's rising weights (1..N)/sum(1..N) over the first N fills
    rn06    and then STOP. Total is bounded by T and never grows with k; short clusters
    rn12    stay deliberately under-deployed (frac<1), which is what the pump wants —
            its mean rises with k up to ~15 before collapsing.
    rc06    same as rn, but any unspent remainder is deployed at the confirmed cluster
    rc12    end, so frac -> 1 for every survivor regardless of k.
    ab15    the bot's b05 ramp plus an ABORT: if the cluster is still alive at bar st+M
    ab30    the move is not fading, so close there instead of waiting for the -30%/-20%
            catastrophe stop to be hit from a bad average.

CAUSALITY. A cluster is confirmed dead once `COOLDOWN` (=10) quiet bars pass with no
trigger. So at bar `last+COOLDOWN` we KNOW it ended, and — keeping the builder's
"decide on a bar, fill at the next close" convention — the fill lands at `c[last+11]`.
`e30` and the aborts key off a fixed clock from the first trigger, which needs no
detection at all. Nothing here reads a bar the decision did not already have. The 240m
exit clock runs from `last`, so a fill at +11 still leaves 229 minutes of holding.

HONEST STOP (new in v4, was flagged unmodelled in v3 §3.5). The stop level tracks the
RUNNING average entry and is checked on every non-fill bar from the first fill onward,
which is what `core.py:Position.stop_price` (a property of `avg_entry`) plus
`strategy.py:_manage_*` actually do. v3 only scanned from the cluster's end off the final
average, which flatters any schedule that is fully loaded early — precisely the bot's.
Consequence: if the stop fires mid-cluster the trade closes with only the tranches placed
so far, so `frac` is the deployment AT EXIT, not the plan.

DUMP FILL WINDOW. The bot keeps adding price-step tranches for as long as the cluster is
alive, i.e. up to 10 minutes past the last trigger; v3 truncated fills at `last`. v4 uses
the faithful window `st .. last+COOLDOWN` for every schedule except `et`, which stays on
the v3 window so it can still be reconciled bit-for-bit.

    uv run python notebooks/pump_dump_v2/_build_signals_v4.py [--symbols N] [--end YYYY-MM-DD]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

from _build_signals import (clusters, fills_time, fills_price, pump_feats, dump_feats,
                            PF, DF, H, COOLDOWN, DAY, FEE,
                            THR_P, THR_D, VOL_MULT, DELTA, CSTOP_P, CSTOP_D, SLIP)

END_DEFAULT = "2026-08-01"
SCHEDULES = ["et", "et_rs", "first", "b01", "b05", "endc",
             "e10", "e20", "e30", "e40", "e45", "e60", "e90", "e120",   # fixed clock, bar st+NN
             "rn03", "rn06", "rn12",                   # ramp 1..N, no top-up (frac<1)
             "eq02", "eq03", "eq04", "eq06", "eq09",   # EQUAL tranches 1/N, capped at N
             "sq04", "sq06",                           # tranches ∝ sqrt(i), capped at N
             "dc04", "dc06", "dc09", "dc16",           # tranches ∝ 1/i  — flattest frac vs k
             "rc06", "rc12",                           # ramp 1..N + remainder at cluster end
             "rt03", "rt06",                           # ramp 1..N + remainder at st+30
             "un01",                                   # UNCAPPED ramp ∝ i (frac may exceed 1)
             "ab08", "ab15", "ab30",                   # bot ramp (0.05) + abort at st+M
             "a8b01", "a15b01", "a30b01", "a8b02"]     # slower base + abort
# name -> (scalein base, abort bar offset from the first trigger)
ABORT_AT = {"ab08": (0.05, 8), "ab15": (0.05, 15), "ab30": (0.05, 30),
            "a8b01": (0.01, 8), "a15b01": (0.01, 15), "a30b01": (0.01, 30), "a8b02": (0.02, 8)}
RT_BAR = 30                                            # fixed-clock top-up bar for rtNN
UN_UNIT = 0.01                                         # tranche i of un01 is i*UN_UNIT of target


# ── tranche-size schedules ───────────────────────────────────────────────────
def ramp_sizes(nfills, base):
    """Bot ramp: add_i = min(base*i, 1-cum). Returns sizes (fractions of target)."""
    ws = []; cum = 0.0
    for i in range(1, nfills + 1):
        if cum >= 1.0 - 1e-12:
            break
        add = min(base * i, 1.0 - cum)
        ws.append(add); cum += add
    return np.array(ws)


def _with_topup(bars, prices, sizes, tb, tp):
    """Append the unspent remainder at bar `tb`, dropping any ramp fill that would land
    at or after it — the tranche list must stay in bar order for the replay."""
    keep = bars < tb
    b = list(bars[keep]); p = list(prices[keep]); s = list(sizes[keep])
    rest = 1.0 - float(np.sum(s))
    if rest > 1e-12:
        b.append(tb); p.append(tp); s.append(rest)
    return np.array(b, dtype=int), np.array(p, dtype=float), np.array(s, dtype=float)


def schedule_fills(name, pos_bars, pos_prices, endc_bar, endc_price, clock, c):
    """(fill_bars, fill_prices, sizes) for a schedule. Sizes are fractions of target
    and are what the trade WOULD place if it survives; the replay may stop early.
    `clock(m)` maps a minute offset from the first trigger to an absolute bar index."""
    nf = len(pos_prices)
    if name in ("et", "et_rs"):
        w = np.arange(1, nf + 1, dtype=float)
        return pos_bars, pos_prices, w / w.sum()
    if name == "first":
        return pos_bars[:1], pos_prices[:1], np.array([1.0])
    if name == "endc":
        return np.array([endc_bar]), np.array([endc_price]), np.array([1.0])
    if name.startswith("e") and name[1:].isdigit():                 # eNN: fixed clock entry
        b = clock(int(name[1:]))
        return np.array([b]), np.array([c[b]]), np.array([1.0])
    if name == "un01":                          # uncapped: total grows as i*(i+1)/2 * UN_UNIT
        s = UN_UNIT * np.arange(1, nf + 1, dtype=float)
        return pos_bars, pos_prices, s
    if name in ABORT_AT:
        s = ramp_sizes(nf, ABORT_AT[name][0])
        return pos_bars[:len(s)], pos_prices[:len(s)], s
    if name in ("b01", "b05"):
        s = ramp_sizes(nf, 0.01 if name == "b01" else 0.05)
        return pos_bars[:len(s)], pos_prices[:len(s)], s
    if name[:2] in ("rn", "rc", "rt", "eq", "sq", "dc"):
        N = int(name[2:])
        # tranche SHAPE — the dial that sets how fast the position grows with cluster depth.
        # The etalon's ∝ i spans 21x of size between k=1 and k=6 while the return only
        # spans ~4x (nb07 §1.1), and it concentrates capital in deep clusters, which all
        # occur on the same crash days. `dc` (∝ 1/i) is the flattest: it still averages
        # down, but frac barely moves with k, so capital stays spread across events —
        # which is the property that makes the (unreachable) etalon work.
        i = np.arange(1, N + 1, dtype=float)
        w = ({"rn": i, "rc": i, "rt": i, "eq": np.ones(N),
              "sq": np.sqrt(i), "dc": 1.0 / i}[name[:2]])
        w = w / w.sum()
        m = min(N, nf)
        b, p, s = pos_bars[:m], pos_prices[:m], w[:m]
        if name.startswith("rc"):
            return _with_topup(b, p, s, endc_bar, endc_price)
        if name.startswith("rt"):
            tb = clock(RT_BAR)
            return _with_topup(b, p, s, tb, c[tb])
        return np.array(b, dtype=int), np.array(p, dtype=float), np.array(s, dtype=float)
    raise ValueError(name)


# ── replay with a running-average stop ───────────────────────────────────────
def replay(c, h, l, fill_bars, fill_prices, sizes, ex, side, cstop):
    """Walk the position forward. The stop level follows the running average entry and
    is checked on the bars BETWEEN fills (a fill bar is a trigger bar: the bot adds and
    returns without testing the stop) and on every bar after the last fill.

    Returns (pnl_on_deployed, frac_deployed_at_exit, n_tranches_placed, avg_entry, exit_idx).
    """
    cum_n = 0.0; cum_pn = 0.0; avg = 0.0; prev = -1; used = 0

    def close_at(t, avg):
        if side > 0:
            raw = -(c[t] / avg - 1)
        else:
            raw = c[t] / avg - 1
        return raw - 2 * FEE

    def stopped_at(t, avg):
        if side > 0:
            return min(-cstop, -(c[t] / avg - 1)) - SLIP - 2 * FEE
        return min(-cstop, (c[t] / avg - 1)) - SLIP - 2 * FEE

    for fb, fp, sz in zip(fill_bars, fill_prices, sizes):
        if cum_n > 0 and fb > prev + 1:                       # quiet bars before this fill
            a, b = prev + 1, min(fb - 1, ex)
            if a <= b:
                lvl = avg * (1 + cstop) if side > 0 else avg * (1 - cstop)
                seg = h[a:b + 1] >= lvl if side > 0 else l[a:b + 1] <= lvl
                if seg.any():
                    t = a + int(np.argmax(seg))
                    return stopped_at(t, avg), cum_n, used, avg, t
        cum_n += sz; cum_pn += sz * fp; avg = cum_pn / cum_n; prev = int(fb); used += 1
    if cum_n <= 0:
        return np.nan, 0.0, 0, np.nan, ex
    a = prev + 1
    if a <= ex:
        lvl = avg * (1 + cstop) if side > 0 else avg * (1 - cstop)
        seg = h[a:ex + 1] >= lvl if side > 0 else l[a:ex + 1] <= lvl
        if seg.any():
            t = a + int(np.argmax(seg))
            return stopped_at(t, avg), cum_n, used, avg, t
    return close_at(ex, avg), cum_n, used, avg, ex


def v3_exit(c, h, l, avg, en, ex, side, cstop):
    """v3/v2 convention: final average, stop scanned from `last+1`. Validation anchor."""
    for t in range(en + 1, ex + 1):
        if side > 0:
            if h[t] / avg - 1 >= cstop:
                return t, min(-cstop, -(c[t] / avg - 1)) - SLIP
        else:
            if l[t] / avg - 1 <= -cstop:
                return t, min(-cstop, (c[t] / avg - 1)) - SLIP
    raw = c[ex] / avg - 1
    return ex, (-raw if side > 0 else raw)


def dump_fills_faithful(c, st, last, n):
    """Price-step fills over the whole LIVE cluster (bot keeps adding for COOLDOWN
    minutes past the last trigger); v3 truncated at `last`."""
    end = min(last + COOLDOWN, n - 2)
    pos = [st]; lc = c[st]; t = st + 1
    while t <= end:
        if c[t] <= lc * (1 - DELTA):
            pos.append(t); lc = c[t]
        t += 1
    return [p for p in pos if p + 1 < n]


def build_leg(side, symbols, end):
    thr = THR_P if side > 0 else THR_D
    cstop = CSTOP_P if side > 0 else CSTOP_D
    gate = side > 0
    names = PF if gate else DF
    rows = []; loaded = 0
    for sym in symbols:
        try:
            df = ohlcv(sym, "2024-01-01", end, "1min")
        except Exception:
            continue
        c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
        hi = df["high"].to_numpy("float64"); lo = df["low"].to_numpy("float64")
        ts = df.index; n = len(c); dv = c * v; loaded += 1
        for (st, last, cond) in clusters(c, thr, side):
            ex = last + 1 + H
            if st < DAY or st + 1 >= n or ex >= n:
                continue
            if gate:
                bv = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
                surge15 = v[st - 14:st + 1].sum() / (bv * 15) if bv and bv > 0 else np.nan
                if not (surge15 >= VOL_MULT):
                    continue
                pos3 = fills_time(c, st, last, cond, n)
                posf = pos3                                   # pump: fills are trigger bars
            else:
                pos3 = fills_price(c, st, last, n)
                posf = dump_fills_faithful(c, st, last, n)
            if not pos3 or not posf:
                continue
            tb = last + COOLDOWN + 1                           # confirmed cluster end (causal)
            clock = lambda m, _st=st: _st + m                  # fixed-clock bar, causal by construction
            if tb >= ex or clock(120) >= ex:
                continue
            pr3 = np.array([c[p + 1] for p in pos3], dtype=float)
            bars_f = np.array([p + 1 for p in posf], dtype=int)
            pr_f = c[bars_f]
            feats = pump_feats(c, v, hi, lo, st) if gate else dump_feats(c, v, hi, lo, st)
            d = dict(sym=sym, entry=ts[st + 1], liq=np.median(dv[max(0, st - DAY):st]),
                     k=len(pos3), kfull=len(posf), clen=int(last - st + 1),
                     stream=("pump" if side > 0 else "dump"))
            d.update({names[i]: feats[i] for i in range(len(names))})
            for name in SCHEDULES:
                exe = ex
                if name == "et":                               # v3-exact anchor
                    w = np.arange(1, len(pr3) + 1, dtype=float); w /= w.sum()
                    avg = float((w * pr3).sum())
                    exi, raw = v3_exit(c, hi, lo, avg, last, ex, side, cstop)
                    pnl, frac, used = raw - 2 * FEE, 1.0, len(pr3)
                else:
                    fb, fp, sz = schedule_fills(name, bars_f, pr_f, tb, float(c[tb]), clock, c)
                    if name in ABORT_AT:                       # close early if still clustering
                        m = st + ABORT_AT[name][1]
                        if m <= last + COOLDOWN and m < ex:
                            exe = m                            # the first fill is at st+1 <= m,
                        keep = fb <= exe                       # so `keep` is never all-False
                        fb, fp, sz = fb[keep], fp[keep], sz[keep]
                    pnl, frac, used, avg, exi = replay(c, hi, lo, fb, fp, sz, exe, side, cstop)
                d[f"pnl_{name}"] = pnl
                d[f"frac_{name}"] = frac
                d[f"kused_{name}"] = used
                d[f"avg_{name}"] = avg
                d[f"exit_{name}"] = ts[exi]
                d[f"stop_{name}"] = bool(exi < exe)            # exit before its own horizon
            rows.append(d)
        if loaded % 100 == 0:
            print(f"  [{'pump' if side>0 else 'dump'}] {loaded} symbols, {len(rows)} events", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--end", default=END_DEFAULT)
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    print(f"v4 build: {len(syms)} symbols, .. {a.end}, schedules {SCHEDULES}", flush=True)
    for side, name in ((+1, "pump"), (-1, "dump")):
        t0 = time.time()
        X = build_leg(side, syms, a.end)
        suf = f"_{a.symbols}" if a.symbols else ""
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{name}_signals_v4{suf}.parquet")
        print(f"\n{name}: {len(X)} events in {time.time()-t0:.0f}s -> _out/{name}_signals_v4{suf}.parquet")
        print(f"  {X.entry.min()} .. {X.entry.max()}   k med {X.k.median():.0f} kfull med {X.kfull.median():.0f}")
        print(f"  {'schedule':7s} {'frac%':>7s} {'kused':>6s} {'stop%':>6s} {'mean%':>8s} {'med%':>7s} {'capwt%':>8s}")
        for s in SCHEDULES:
            p = X[f"pnl_{s}"]; f = X[f"frac_{s}"]
            print(f"  {s:7s} {f.mean()*100:7.1f} {X[f'kused_{s}'].median():6.0f} "
                  f"{X[f'stop_{s}'].mean()*100:6.2f} {p.mean()*100:+8.3f} {p.median()*100:+7.3f} "
                  f"{(f*p).sum()/f.sum()*100:+8.3f}", flush=True)
