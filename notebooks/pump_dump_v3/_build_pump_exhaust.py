"""nb03 builder — EXHAUSTION entries for the pump fade.

nb02 verdict: breed prediction sorts risk but fading at the +5% crossing is
mid-run — everything still runs on average. Three independent sources (old pump
line 09_3, the practitioners' entries, nb02) point at the same fix: enter after
the run STOPS, not while it runs. Two causal exhaustion definitions:

  stallN   after the +5% crossing, the first bar with no new running high for
           N consecutive minutes (N in 10/15/30). Decide there, fill next close.
  cend     cluster confirmed dead: COOLDOWN quiet bars after the last trigger.
           Decide at bar last+COOLDOWN, fill at the next close (v4 convention).

For each pump event that crossed +5% (same detector/book as _build_events.py):
  entry ts/k + per entry-kind: minutes from first trigger (lag), retrace from
  the running peak at entry (retr), and fade pnl at +30/60/240m with the 30%
  catastrophe stop from the entry price (net 2*FEE, SLIP on stops).
Joinable with _out/breed_preds.parquet (sym+entry) — the +5% crossing always
precedes both entries, so filtering on nb02's prediction is causal.

    uv run python notebooks/pump_dump_v3/_build_pump_exhaust.py [--symbols N]
writes _out/pump_exhaust.parquet
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v3")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd
from _build_events import (clusters, WIN, THR_P, VOL_MULT, COOLDOWN, DAY,
                           FEE, SLIP, CSTOP_P, START, END, CUT)
from _build_pump_levels import fade_from, PATHWIN

STALLS = [10, 15, 30]
FADE_HZ = [30, 60, 240]
LVL = 0.05                      # exhaustion is armed only after +5% is crossed
MAXWAIT = 720                   # give up if no stall within 12h of the crossing

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
        h = df["high"].to_numpy("float64")
        ts = df.index; n = len(c)
        for st, last, k in clusters(c, THR_P, +1):
            if st < DAY or st + 2 >= n:
                continue
            base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
            surge15 = v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan
            if not (surge15 >= VOL_MULT):
                continue
            en = st + 1
            e = c[en]
            hi = min(en + PATHWIN, n - 1)
            # find the +5% crossing
            lvl = e * (1 + LVL)
            tc = -1
            runmax = e; last_hi_bar = en
            for t in range(en + 1, hi + 1):
                if h[t] > runmax:
                    runmax = h[t]; last_hi_bar = t
                if tc < 0 and h[t] >= lvl:
                    tc = t
                    break
            if tc < 0:
                continue                      # never crossed +5%
            d = dict(sym=sym, entry=ts[en], k=k)
            # --- stallN entries: first bar (after tc) with N minutes since last new high
            lim = min(tc + MAXWAIT, hi)
            for N in STALLS:
                t_ent = -1
                runmax2 = runmax; last_hi2 = last_hi_bar
                for t in range(tc + 1, lim + 1):
                    if h[t] > runmax2:
                        runmax2 = h[t]; last_hi2 = t
                    elif t - last_hi2 >= N:
                        t_ent = t
                        break
                key = f"stall{N}"
                if t_ent > 0 and t_ent + 1 < n:
                    d[f"{key}_lag"] = t_ent - en
                    d[f"{key}_retr"] = c[t_ent] / runmax2 - 1
                    for hz in FADE_HZ:
                        d[f"{key}_{hz}"] = fade_from(c, h, n, t_ent, hz)
                else:
                    d[f"{key}_lag"] = -1
                    d[f"{key}_retr"] = np.nan
                    for hz in FADE_HZ:
                        d[f"{key}_{hz}"] = np.nan
            # --- cluster-end entry (defined for every event; fill next close)
            t_ent = last + COOLDOWN
            if t_ent + 1 < n:
                # running max up to t_ent for retr
                rm = max(e, h[en + 1:t_ent + 1].max()) if t_ent > en else e
                d["cend_lag"] = t_ent - en
                d["cend_retr"] = c[t_ent] / rm - 1
                for hz in FADE_HZ:
                    d[f"cend_{hz}"] = fade_from(c, h, n, t_ent, hz)
            else:
                d["cend_lag"] = -1
                d["cend_retr"] = np.nan
                for hz in FADE_HZ:
                    d[f"cend_{hz}"] = np.nan
            rows.append(d)
        if loaded % 80 == 0:
            print(f"  {loaded} symbols, {len(rows)} events, {time.time()-t0:.0f}s", flush=True)
    X = pd.DataFrame(rows).sort_values("entry").reset_index(drop=True)
    out = "notebooks/pump_dump_v3/_out/pump_exhaust.parquet"
    X.to_parquet(out)
    got = {f"stall{N}": int((X[f"stall{N}_lag"] > 0).sum()) for N in STALLS}
    print(f"{len(X)} events crossed +5%; stall entries found: {got}; "
          f"wrote {out} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None)
    main(ap.parse_args().symbols)
