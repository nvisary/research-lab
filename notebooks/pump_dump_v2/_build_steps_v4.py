"""Every fill treated as an INDEPENDENT fixed-size trade, instead of one averaged position.

WHY (nb07 §7, the last live idea). The etalon's edge is its rising weights 1..k: the later,
deeper fills carry the most weight, and they are exactly the ones the bounce pays on. To
reproduce that with a total of exactly `target` you must know k in advance — and nb07 §G1
shows k is NOT predictable for the dump (walk-forward R2 -0.017, AUC 0.515 on 'is deep').
So that door is shut.

But there is a second reading of the same fact. If the deep fills are individually good
entries, they do not need to be part of one averaged position at all: take each price step
as its own fixed-size trade. Exposure then grows LINEARLY with cluster depth instead of
quadratically, no normalisation is required, and nothing has to be predicted.

This builder emits one row per FILL rather than one row per cluster:
    step   1-based index of the fill inside its cluster
    k      the cluster's eventual fill count (for analysis only — never used to size)
    pnl    return of this single entry, own catastrophe stop measured from its OWN price,
           exit at the cluster's shared 240m horizon (the bot's rule: 240m from the last
           trigger), or at the stop if it comes first
    pnl_own240  the same trade held 240m from ITS OWN entry instead — separates 'the deep
           entry is good' from 'the shared horizon happens to suit it'

    uv run python notebooks/pump_dump_v2/_build_steps_v4.py [--symbols N]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

from _build_signals import (clusters, fills_time, pump_feats, dump_feats, PF, DF,
                            H, COOLDOWN, DAY, FEE, THR_P, THR_D, VOL_MULT,
                            CSTOP_P, CSTOP_D, SLIP)
from _build_signals_v4 import dump_fills_faithful, END_DEFAULT


def one_trade(c, h, l, entry_bar, ex, side, cstop):
    """A single fixed-size entry at c[entry_bar], stop from its own price, exit at `ex`."""
    p = c[entry_bar]
    lvl = p * (1 + cstop) if side > 0 else p * (1 - cstop)
    a, b = entry_bar + 1, ex
    if a <= b:
        seg = h[a:b + 1] >= lvl if side > 0 else l[a:b + 1] <= lvl
        if seg.any():
            t = a + int(np.argmax(seg))
            raw = (min(-cstop, -(c[t] / p - 1)) if side > 0 else min(-cstop, (c[t] / p - 1)))
            return raw - SLIP - 2 * FEE, t, True
    raw = c[ex] / p - 1
    return (-raw if side > 0 else raw) - 2 * FEE, ex, False


def build_leg(side, symbols, end):
    leg = "pump" if side > 0 else "dump"
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
                s15 = v[st - 14:st + 1].sum() / (bv * 15) if bv and bv > 0 else np.nan
                if not (s15 >= VOL_MULT):
                    continue
                pos = fills_time(c, st, last, cond, n)
            else:
                pos = dump_fills_faithful(c, st, last, n)
            if not pos:
                continue
            feats = pump_feats(c, v, hi, lo, st) if gate else dump_feats(c, v, hi, lo, st)
            liq = np.median(dv[max(0, st - DAY):st])
            for i, p in enumerate(pos, start=1):
                eb = p + 1
                if eb >= ex:
                    break
                pnl, exi, stopped = one_trade(c, hi, lo, eb, ex, side, cstop)
                own = min(eb + H, n - 1)
                pnl_own, _, _ = one_trade(c, hi, lo, eb, own, side, cstop)
                d = dict(sym=sym, entry=ts[eb], liq=liq, step=i, k=len(pos),
                         stream=leg, pnl=pnl, pnl_own240=pnl_own,
                         exit_ts=ts[exi], stopped=stopped,
                         drop=c[eb] / c[st + 1] - 1)          # how far below the trigger
                d.update({names[j]: feats[j] for j in range(len(names))})
                rows.append(d)
        if loaded % 100 == 0:
            print(f"  [{leg}] {loaded} symbols, {len(rows)} step-trades", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--end", default=END_DEFAULT)
    ap.add_argument("--legs", default="dump")
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    for leg in a.legs.split(","):
        side = +1 if leg == "pump" else -1
        t0 = time.time()
        X = build_leg(side, syms, a.end)
        suf = f"_{a.symbols}" if a.symbols else ""
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{leg}_steps{suf}.parquet")
        print(f"\n{leg}: {len(X)} step-trades in {time.time()-t0:.0f}s "
              f"({X.sym.nunique()} symbols, {X.entry.min()}..{X.entry.max()})")
        tr = X[X.entry < "2025-07-01"]
        print(f"  TRAIN n={len(tr)} — return of a single fixed-size entry, by step index")
        g = tr.groupby(tr.step.clip(upper=8)).agg(
            n=("pnl", "size"), mean=("pnl", "mean"), med=("pnl", "median"),
            own=("pnl_own240", "mean"), stop=("stopped", "mean"), drop=("drop", "mean"))
        for cc in ["mean", "med", "own", "stop", "drop"]:
            g[cc] *= 100
        print(g.round(3).to_string(), flush=True)
