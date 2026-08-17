"""Extract the realized scale-in tranche count `k` per event — cheap pass.

WHY. `_build_signals.py` computes the list of scale-in fill minutes (`pos`), uses it
for the ramp-weighted average entry price `avg`, and then throws it away. The engine
(`_engine.py:run_dca`) sizes every trade at the FULL `f*eq` regardless of how many
tranches actually filled. The live bot cannot do that: it ramps
`target*0.05*(1+2+..+k)`, so a k=1 event only ever deploys 5% of target
(`pump_fade_bot/strategy.py:_next_add`). That single multiplier is the whole
etalon-vs-bot sizing divergence — see nb05.

This script re-runs ONLY the detector + fill-list part of `build_leg` (no features,
no exit_rollover, no peak_diag — those are the expensive bits) and writes
`(sym, entry, stream, k)` so nb05 can join it onto the existing signal parquets and
re-size with `run_dca(ffrac=...)` — without rebuilding signals or touching the engine.

Faithful by construction: `clusters` / `fills_time` / `fills_price` and every gate are
IMPORTED from `_build_signals`, not re-implemented.

    uv run python notebooks/pump_dump_v2/_extract_k.py [--symbols N]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")

import numpy as np, pandas as pd
from _lab import ohlcv, list_symbols
from _build_signals import (clusters, fills_time, fills_price,
                            H, DAY, VOL_MULT, THR_P, THR_D)


def extract_leg(side, symbols):
    """Mirror of build_leg's event loop, keeping only (sym, entry, k)."""
    thr = THR_P if side > 0 else THR_D
    gate = side > 0
    rows = []
    for si, sym in enumerate(symbols, 1):
        try:
            df = ohlcv(sym, "2024-01-01", "2026-07-01", "1min")
        except Exception:
            continue
        c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
        ts = df.index; n = len(c)
        for (st, last, cond) in clusters(c, thr, side):
            ex = last + 1 + H
            if st < DAY or st + 1 >= n or ex >= n:
                continue
            if gate:
                base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
                surge15 = v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan
                if not (surge15 >= VOL_MULT):
                    continue
                pos = fills_time(c, st, last, cond, n)
            else:
                pos = fills_price(c, st, last, n)
            if not pos:
                continue
            rows.append((sym, ts[st + 1], "pump" if side > 0 else "dump", len(pos)))
        if si % 20 == 0:
            print(f"  [{'pump' if side>0 else 'dump'}] {si}/{len(symbols)} symbols, "
                  f"{len(rows)} events", flush=True)
    return pd.DataFrame(rows, columns=["sym", "entry", "stream", "k"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0, help="limit symbols (timing probe)")
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    print(f"extracting k over {len(syms)} symbols ...", flush=True)
    out = []
    for side, name in ((+1, "pump"), (-1, "dump")):
        t0 = time.time()
        X = extract_leg(side, syms)
        print(f"{name}: {len(X)} events in {time.time()-t0:.0f}s", flush=True)
        out.append(X)
    K = pd.concat(out).sort_values("entry").reset_index(drop=True)
    suffix = f"_{a.symbols}" if a.symbols else ""
    path = f"notebooks/pump_dump_v2/_out/tranche_k{suffix}.parquet"
    K.to_parquet(path)
    print(f"cached -> {path}  ({len(K)} rows)", flush=True)
    print(K.groupby("stream").k.describe().round(2).to_string())
