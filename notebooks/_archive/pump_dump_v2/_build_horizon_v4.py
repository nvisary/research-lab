"""Exit-horizon sweep — the one dial nb07 never moved.

Every schedule in `_build_signals_v4.py` exits 240 minutes after the last cluster trigger,
inherited from combined/13-14 and never re-chosen. It is also the dial that decides whether
a dump long is still alive when the bounce arrives, and nb07 §1.1 shows the executable
schedules die precisely on the cascades — where the bounce is furthest away.

The stop sweep already showed that WIDENING the stop helps monotonically on both legs, and
that removing it helps most; so horizon and stop are swept together. `first` matters most
here: it is the only fully-executable schedule with a completely flat size profile
(spread 1.0, no depth-martingale at all), and its whole problem is surviving the cascade.

    uv run python notebooks/pump_dump_v2/_build_horizon_v4.py [--symbols N]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

from _build_signals import (clusters, fills_time, pump_feats, dump_feats, PF, DF,
                            COOLDOWN, DAY, THR_P, THR_D, VOL_MULT, CSTOP_P, CSTOP_D)
from _build_signals_v4 import (schedule_fills, replay, dump_fills_faithful, END_DEFAULT)

SCHED = ["first", "et_rs", "rn03", "eq03", "b05"]
# The two legs want OPPOSITE holds and the bot gives both the same 240m. The first sweep
# showed the dump improving out to 720-1440m while the pump improved monotonically towards
# the short end without ever turning over — so the pump's optimum was outside the grid and
# every "pump does not work" number in nb07 was measured at the wrong horizon. The grid now
# runs down to 15 minutes.
HORIZ = [15, 30, 45, 60, 90, 120, 180, 240, 480, 720, 1440, 2880]
STOPS = {"pump": [0.30, np.inf], "dump": [0.20, np.inf]}


def tag(s, h, cs):
    return f"{s}_h{h}_{'x' if not np.isfinite(cs) else int(round(cs*100))}"


def build_leg(side, symbols, end):
    leg = "pump" if side > 0 else "dump"
    thr = THR_P if side > 0 else THR_D
    gate = side > 0
    names = PF if gate else DF
    rows = []; loaded = 0
    HMAX = max(HORIZ)
    for sym in symbols:
        try:
            df = ohlcv(sym, "2024-01-01", end, "1min")
        except Exception:
            continue
        c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
        hi = df["high"].to_numpy("float64"); lo = df["low"].to_numpy("float64")
        ts = df.index; n = len(c); dv = c * v; loaded += 1
        for (st, last, cond) in clusters(c, thr, side):
            if st < DAY or st + 1 >= n or last + 1 + HMAX >= n:
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
            bars_f = np.array([p + 1 for p in posf], dtype=int)
            pr_f = c[bars_f]
            feats = pump_feats(c, v, hi, lo, st) if gate else dump_feats(c, v, hi, lo, st)
            d = dict(sym=sym, entry=ts[st + 1], liq=np.median(dv[max(0, st - DAY):st]),
                     k=len(posf), stream=leg)
            d.update({names[i]: feats[i] for i in range(len(names))})
            for s in SCHED:
                fb, fp, sz = schedule_fills(s, bars_f, pr_f, tb, float(c[tb]), clock, c)
                for h in HORIZ:
                    ex = last + 1 + h
                    for cs in STOPS[leg]:
                        pnl, frac, used, avg, exi = replay(
                            c, hi, lo, fb, fp, sz, ex, side,
                            cs if np.isfinite(cs) else 1e9)
                        t = tag(s, h, cs)
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
    ap.add_argument("--legs", default="dump,pump")
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    print(f"horizon sweep {HORIZ} x stops {STOPS} x {SCHED}", flush=True)
    for leg in a.legs.split(","):
        side = +1 if leg == "pump" else -1
        t0 = time.time()
        X = build_leg(side, syms, a.end)
        suf = f"_{a.symbols}" if a.symbols else ""
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{leg}_horizon{suf}.parquet")
        print(f"\n{leg}: {len(X)} events in {time.time()-t0:.0f}s", flush=True)
        tr = X[X.entry < "2025-07-01"]
        for cs in STOPS[leg]:
            lab = "no stop" if not np.isfinite(cs) else f"stop {cs:.0%}"
            print(f"  TRAIN n={len(tr)} — mtu % , {lab}")
            print("  " + f"{'sched':7s}" + "".join(f"{h:>9d}m" for h in HORIZ))
            for s in SCHED:
                cells = "".join(
                    f"{(tr[f'frac_{tag(s,h,cs)}']*tr[f'pnl_{tag(s,h,cs)}']).mean()*100:+10.3f}"
                    for h in HORIZ)
                print(f"  {s:7s}" + cells, flush=True)
