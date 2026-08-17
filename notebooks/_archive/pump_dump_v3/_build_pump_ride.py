"""nb04 builder — LONG continuation after the pump cluster ends, honestly.

nb03 found the sign flip: shorting the confirmed cluster end loses on all three
windows => the long side wins there. But that was "-fade": no stop on the way
down, no funding, no tail accounting. This builder models the long properly:

  entry   decide at bar last+COOLDOWN (cluster confirmed dead), fill next close.
  exits   horizon grid {60,240,720} minutes from entry;
          a low-side stop can fire first (intrabar), levels {5,10,20}% and none.
          Stop fills at the stop price (or the close if the bar gapped through),
          minus SLIP; all pnl net of 2*FEE.
  funding sum of funding rates whose stamp falls inside (entry, entry+hz];
          longs PAY positive rates => pnl_funding = -sum. Recorded per horizon
          regardless of an early stop (conservative for the long: overstates
          the funding cost of stopped trades).
  control same-symbol long entered 48h BEFORE the trigger with the same
          horizons and no stop — the "is it just alt-season beta?" yardstick.

Causality note for the breed filter: the +5% crossing (where nb02's prediction
lives) can happen AFTER the cluster end. We record t05 (minutes from first-
trigger entry to the crossing) and cend_lag; the notebook may use the breed
prediction ONLY where t05 <= cend_lag.

    uv run python notebooks/pump_dump_v3/_build_pump_ride.py [--symbols N]
writes _out/pump_ride.parquet
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v3")
from _lab import ohlcv, funding, list_symbols
import numpy as np, pandas as pd
from _build_events import (clusters, WIN, THR_P, VOL_MULT, COOLDOWN, DAY,
                           FEE, SLIP, START, END, CUT)

HORIZONS = [60, 240, 720]
STOPS = [0.05, 0.10, 0.20, None]
LVL = 0.05
CTRL_SHIFT = 2880          # control entry: 48h before the trigger

def long_pnl(c, l, n, t0, hz, stop):
    """Long at close[t0], exit close[t0+hz]; low-side stop (intrabar) first."""
    e = c[t0]
    xt = t0 + hz
    if xt >= n:
        return np.nan
    if stop is not None:
        for t in range(t0 + 1, xt + 1):
            if l[t] / e - 1 <= -stop:
                return min(-stop, c[t] / e - 1) - SLIP - 2 * FEE
    return c[xt] / e - 1 - 2 * FEE

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
        try:
            fr = funding(sym, START, END)["rate"]
        except Exception:
            fr = pd.Series(dtype=float)
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
            en = st + 1
            e0 = c[en]
            t_end = last + COOLDOWN                 # cluster confirmed dead here
            t_ent = t_end + 1                       # fill at next close
            if t_ent + HORIZONS[0] >= n:
                continue
            # +5% crossing time (for the causality guard on breed preds)
            lvl = e0 * (1 + LVL)
            t05 = -1
            for t in range(en + 1, min(en + 1440, n - 1) + 1):
                if h[t] >= lvl:
                    t05 = t - en
                    break
            d = dict(sym=sym, entry=ts[en], k=k, cend_lag=t_ent - en, t05=t05,
                     runup_at_entry=c[t_ent] / e0 - 1,
                     liq=np.median((c * v)[max(0, st - DAY):st]))
            for hz in HORIZONS:
                for stp in STOPS:
                    tag = f"s{int(stp*100):02d}" if stp else "s00"
                    d[f"ride{hz}_{tag}"] = long_pnl(c, l, n, t_ent, hz, stp)
                # funding paid by the long inside (entry, entry+hz]
                if len(fr):
                    w = fr[(fr.index > ts[t_ent]) & (fr.index <= ts[min(t_ent + hz, n - 1)])]
                    d[f"fund{hz}"] = -w.sum()
                else:
                    d[f"fund{hz}"] = np.nan
                # control: same symbol, 48h before the trigger, no stop
                tctl = en - CTRL_SHIFT
                d[f"ctrl{hz}"] = (long_pnl(c, l, n, tctl, hz, None)
                                  if tctl > 0 else np.nan)
            rows.append(d)
        if loaded % 80 == 0:
            print(f"  {loaded} symbols, {len(rows)} events, {time.time()-t0:.0f}s", flush=True)
    X = pd.DataFrame(rows).sort_values("entry").reset_index(drop=True)
    out = "notebooks/pump_dump_v3/_out/pump_ride.parquet"
    X.to_parquet(out)
    print(f"{len(X)} events; wrote {out} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None)
    main(ap.parse_args().symbols)
