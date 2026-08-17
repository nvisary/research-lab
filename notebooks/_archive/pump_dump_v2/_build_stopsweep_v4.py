"""Stop-level sweep on top of the v4 fill schedules.

WHY. nb07 §2 splits the dump book by k (how many -2% price steps the move made) and the
loss is entirely in the cascade tail:

    dump TRAIN  k=1..3 (95% of events): `first` +1.7/+3.0/+3.4%   k=4-6: -7.1%   k=7+: -17.0%
    dump VALID  k=1..3 (89% of events): `first` +1.4/+2.4/+1.0%   k=4-6: -9.8%   k=7+: -23.2%

k>=4 means the price is ~8% below the entry ((1-0.02)^4 = -7.8%), so the cascade is
already visible long before the -20% catastrophe stop is reached. That stop level was
chosen for an entry that AVERAGES DOWN, where a tight stop is hit constantly and does
hurt (dump/09, config.py). It has never been re-chosen for a flat-size entry that does
not average, and on that entry it lets a detectable cascade run to -20%+.

This sweeps the stop for each leg across the leading schedules. Everything else — the
detector, the fill schedules, the running-average stop mechanics, the costs — is imported
from `_build_signals_v4`, so the only thing that varies is the stop.

    uv run python notebooks/pump_dump_v2/_build_stopsweep_v4.py [--symbols N]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

from _build_signals import (clusters, fills_time, fills_price, pump_feats, dump_feats,
                            PF, DF, H, COOLDOWN, DAY, THR_P, THR_D, VOL_MULT)
from _build_signals_v4 import (schedule_fills, replay, dump_fills_faithful, END_DEFAULT)

SCHED = ["first", "b05", "rn03", "et_rs", "e20", "e30"]
STOPS = {"pump": [0.10, 0.15, 0.20, 0.30, np.inf],
         "dump": [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, np.inf]}


def tag(s, cs):
    return f"{s}__{'inf' if not np.isfinite(cs) else f'{int(round(cs*100)):02d}'}"


def build_leg(side, symbols, end):
    leg = "pump" if side > 0 else "dump"
    thr = THR_P if side > 0 else THR_D
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
                s15 = v[st - 14:st + 1].sum() / (bv * 15) if bv and bv > 0 else np.nan
                if not (s15 >= VOL_MULT):
                    continue
                posf = fills_time(c, st, last, cond, n)
            else:
                posf = dump_fills_faithful(c, st, last, n)
            if not posf:
                continue
            tb = last + COOLDOWN + 1
            clock = lambda m, _st=st: _st + m
            if tb >= ex or clock(120) >= ex:
                continue
            bars_f = np.array([p + 1 for p in posf], dtype=int)
            pr_f = c[bars_f]
            feats = pump_feats(c, v, hi, lo, st) if gate else dump_feats(c, v, hi, lo, st)
            d = dict(sym=sym, entry=ts[st + 1], liq=np.median(dv[max(0, st - DAY):st]),
                     k=len(posf), stream=leg)
            d.update({names[i]: feats[i] for i in range(len(names))})
            for s in SCHED:
                fb, fp, sz = schedule_fills(s, bars_f, pr_f, tb, float(c[tb]), clock, c)
                for cs in STOPS[leg]:
                    pnl, frac, used, avg, exi = replay(c, hi, lo, fb, fp, sz, ex, side,
                                                       cs if np.isfinite(cs) else 1e9)
                    t = tag(s, cs)
                    d[f"pnl_{t}"] = pnl; d[f"frac_{t}"] = frac
                    d[f"stop_{t}"] = bool(exi < ex); d[f"exit_{t}"] = ts[exi]
            rows.append(d)
        if loaded % 100 == 0:
            print(f"  [{leg}] {loaded} symbols, {len(rows)} events", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--end", default=END_DEFAULT)
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    print(f"stop sweep: {len(syms)} symbols, schedules {SCHED}", flush=True)
    for side, leg in ((+1, "pump"), (-1, "dump")):
        t0 = time.time()
        X = build_leg(side, syms, a.end)
        suf = f"_{a.symbols}" if a.symbols else ""
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{leg}_stopsweep{suf}.parquet")
        print(f"\n{leg}: {len(X)} events in {time.time()-t0:.0f}s", flush=True)
        tr = X[X.entry < "2025-07-01"]                       # TRAIN only in the build log
        print(f"  TRAIN n={len(tr)} — mtu % (money per unit of target notional)")
        hdr = "  ".join(f"{('none' if not np.isfinite(cs) else f'{cs:.0%}'):>6s}" for cs in STOPS[leg])
        print(f"  {'sched':7s} {hdr}")
        for s in SCHED:
            cells = []
            for cs in STOPS[leg]:
                t = tag(s, cs)
                cells.append(f"{(tr[f'frac_{t}']*tr[f'pnl_{t}']).mean()*100:+6.3f}")
            print(f"  {s:7s} " + "  ".join(cells), flush=True)
