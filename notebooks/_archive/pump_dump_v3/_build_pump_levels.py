"""nb01 builder — pump PATH anatomy in the friends' coordinate system.

For every pump event of the fresh book (same detector as _build_events.py) we
record the FORWARD PATH from the first-trigger entry:

  runup    max(high)/entry - 1 within PATHWIN minutes  -> the pump's "final size"
  t_peak   minute of that max (from entry)
  for each level L in LEVELS (+5% .. +25%):
      tL       first minute the HIGH crosses entry*(1+L)   (-1 = never)
      retrL    deepest retrace between entry and that crossing (low/runmax-1)
      surgL    volume in the 5 min before the crossing / trigger-day median
  fade returns if you SHORT at the close of the bar that crossed L
      (the friends' "enter while it runs" fade), exits at +30/+60/+240 min
      with the 30% catastrophe stop from that entry:
      fadeL_30, fadeL_60, fadeL_240

GROUPS are assigned later in the notebook from `runup` (G1 10-15% .. G4 >25%).
The grouping is HINDSIGHT — analysis only, never an entry signal. Everything a
STRATEGY could use (tL, retrL, surgL, features at trigger) is causal at its bar.

    uv run python notebooks/pump_dump_v3/_build_pump_levels.py [--symbols N]
writes _out/pump_levels.parquet
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v3")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd
from _build_events import (clusters, WIN, THR_P, VOL_MULT, COOLDOWN, DAY,
                           FEE, SLIP, CSTOP_P, START, END, CUT)

PATHWIN = 1440                      # minutes of forward path we study
LEVELS = [0.05, 0.10, 0.15, 0.20, 0.25]
FADE_HZ = [30, 60, 240]

def fade_from(c, h, n, t0, hz):
    """Short at close[t0], exit close[t0+hz] or 30% catastrophe stop (intrabar)."""
    e = c[t0]
    xt = t0 + hz
    if xt >= n:
        return np.nan
    for t in range(t0 + 1, xt + 1):
        if h[t] / e - 1 >= CSTOP_P:
            return min(-CSTOP_P, -(c[t] / e - 1)) - SLIP - 2 * FEE
    return -(c[xt] / e - 1) - 2 * FEE

def main(nsym=None):
    syms = list_symbols()
    if nsym:
        syms = syms[:nsym]
    rows = []; loaded = 0; t0 = time.time()
    for sym in syms:
        try:
            df = ohlcv(sym, START, END, "1min")
        except Exception:
            continue
        if len(df) < DAY + 100:
            continue
        df = df[df.index <= CUT]
        if len(df) < DAY + 100:
            continue
        loaded += 1
        c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
        h = df["high"].to_numpy("float64"); l = df["low"].to_numpy("float64")
        ts = df.index; n = len(c)
        for st, last, k in clusters(c, THR_P, +1):
            if st < DAY or st + 2 >= n:
                continue
            base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
            surge15 = v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan
            if not (surge15 >= VOL_MULT):
                continue
            en = st + 1                      # entry bar (fill at its close)
            e = c[en]
            hi = min(en + PATHWIN, n - 1)
            hh = h[en + 1:hi + 1]
            if len(hh) < 30:
                continue
            runup = hh.max() / e - 1
            t_peak = int(hh.argmax()) + 1
            d = dict(sym=sym, entry=ts[en], k=k, clen=last - st,
                     surge15=surge15, runup=runup, t_peak=t_peak,
                     r15=c[st] / c[st - 15] - 1,
                     liq=np.median((c * v)[max(0, st - DAY):st]))
            runmax = e
            for L in LEVELS:
                lvl = e * (1 + L)
                tL = -1
                retr = 0.0
                for t in range(en + 1, hi + 1):
                    if h[t] > runmax:
                        runmax = h[t]
                    retr = min(retr, l[t] / runmax - 1)
                    if h[t] >= lvl:
                        tL = t - en
                        break
                key = f"{int(L*100):02d}"
                d[f"t{key}"] = tL
                if tL > 0:
                    tc = en + tL
                    d[f"retr{key}"] = retr
                    d[f"surg{key}"] = (v[tc - 4:tc + 1].sum() / (base * 5)
                                       if base and base > 0 else np.nan)
                    for hz in FADE_HZ:
                        d[f"fade{key}_{hz}"] = fade_from(c, h, n, tc, hz)
                else:
                    d[f"retr{key}"] = np.nan
                    d[f"surg{key}"] = np.nan
                    for hz in FADE_HZ:
                        d[f"fade{key}_{hz}"] = np.nan
            # baseline fade at entry itself (level 0)
            for hz in FADE_HZ:
                d[f"fade00_{hz}"] = fade_from(c, h, n, en, hz)
            rows.append(d)
        if loaded % 40 == 0:
            print(f"  {loaded} symbols, {len(rows)} pumps, {time.time()-t0:.0f}s", flush=True)
    X = pd.DataFrame(rows).sort_values("entry").reset_index(drop=True)
    out = "notebooks/pump_dump_v3/_out/pump_levels.parquet"
    X.to_parquet(out)
    print(f"{len(X)} pumps, {X.sym.nunique()} symbols; runup median "
          f"{X.runup.median()*100:.1f}% p90 {X.runup.quantile(.9)*100:.1f}%; "
          f"wrote {out} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None)
    main(ap.parse_args().symbols)
