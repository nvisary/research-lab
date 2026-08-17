"""One-time heavy build: wide price panels for the statarb universe.

Reads 1m Bybit perp parquet for every symbol with complete coverage over the
research window, resamples to 1h, and writes wide panels (rows = timestamps,
cols = symbols) to ``_out/``:

    panel_close_1h.parquet      last close in the hour
    panel_high_1h.parquet       max high  (needed later for stop simulation)
    panel_low_1h.parquet        min low
    panel_dollarvol_1h.parquet  sum(volume) * close  ~ traded USD in the hour

Run from this directory::

    uv run python _build_panel.py

Idempotent: skips symbols already present unless --rebuild is passed. Coarser
timeframes (4h/1d) are derived from the 1h panel in the notebooks, not rebuilt.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lab import FULL, OUT, panel_path, universe, loader  # noqa: E402

TF = "1h"


def main(rebuild: bool = False, tf: str | None = None) -> None:
    global TF
    TF = tf or TF
    syms = universe()
    print(f"universe: {len(syms)} symbols with complete {FULL[0]}..{FULL[1]} coverage")

    kinds = ("close", "high", "low", "dollarvol")
    if not rebuild and all(panel_path(k, TF).exists() for k in kinds):
        print("all panels already built — pass --rebuild to force")
        return

    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in kinds}
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        df = loader.load(s, FULL[0], FULL[1], TF)
        if df.empty:
            print(f"  !! {s}: empty, skipped")
            continue
        cols["close"][s] = df["close"]
        cols["high"][s] = df["high"]
        cols["low"][s] = df["low"]
        cols["dollarvol"][s] = df["volume"] * df["close"]
        if i % 20 == 0 or i == len(syms):
            print(f"  {i:>3}/{len(syms)}  {s:<14} {time.time() - t0:6.1f}s")

    for k in kinds:
        panel = pd.DataFrame(cols[k]).sort_index()
        panel.index.name = "timestamp"
        p = panel_path(k, TF)
        panel.to_parquet(p)
        mb = p.stat().st_size / 1e6
        print(f"[written] {p.name}  shape={panel.shape}  {mb:.1f} MB")

    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    tf = None
    for a in sys.argv[1:]:
        if a.startswith("--tf="):
            tf = a.split("=", 1)[1]
    main(rebuild="--rebuild" in sys.argv, tf=tf)
