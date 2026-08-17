"""v3 nb00 event-book builder — clean rebuild, no inherited caches.

WHAT IT BUILDS. One row per pump/dump EVENT (cluster of triggers), with the only
entry scheme v2 proved executable: the FULL target size at the FIRST trigger
(decide on the trigger bar, fill at the next bar's close). No averaging down,
no fill schedules, no knowledge of how long the cluster will run. `frac` is 1
by construction — the sizing-lookahead class of bugs (v2 nb07) cannot exist here.

Exits: a grid of fixed horizons from ENTRY (v2 ran the clock from cluster end,
which needs knowing the end; we don't). A catastrophe stop (pump 30% / dump 20%
from the entry price, intrabar) can fire first. pnl columns are net of
2*FEE (taker round trip); stop exits also pay SLIP.

Detector = v2 starting point, per the v3 charter (README: direction D owns
changing it): dump close/close[-15] <= -7%, pump >= +5% AND vol surge15 >= 3x;
cluster = consecutive triggers glued until 10 quiet minutes; k = number of
trigger bars in the cluster (recorded for ANALYSIS ONLY — the entry never uses it).

Data hygiene (charter): full universe on disk, dead dirs kept (survivorship),
everything cut at 2026-07-31 11:00 UTC (ragged alphabet tail).

    uv run python notebooks/pump_dump_v3/_build_events.py [--symbols N]
writes _out/events.parquet
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v3")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

WIN = 15                    # trailing return window (minutes)
THR_P, THR_D = 0.05, 0.07   # pump +5%/15m, dump -7%/15m
VOL_MULT = 3.0              # pump needs surge15 >= 3x median volume
COOLDOWN = 10               # quiet minutes that end a cluster
DAY = 1440
FEE = 0.00075               # taker, per side
SLIP = 0.001                # extra on stop exits
CSTOP_P, CSTOP_D = 0.30, 0.20
HORIZONS = [15, 30, 60, 120, 240, 480, 720, 1440, 2880]   # minutes from entry
START, END = "2024-01-01", "2026-08-01"
CUT = pd.Timestamp("2026-07-31 11:00", tz="UTC")           # ragged July tail

def clusters(c, thr, side):
    """[(first_trigger_idx, last_trigger_idx, k)] — k = count of trigger bars."""
    n = len(c)
    if n < WIN + 5:
        return []
    r = np.full(n, np.nan); r[WIN:] = c[WIN:] / c[:-WIN] - 1
    cond = (r <= -thr) if side < 0 else (r >= thr)
    out = []; i = WIN
    while i < n:
        if not cond[i] or (i > 0 and cond[i - 1]):
            i += 1; continue
        st = i; last = i; k = 1; falses = 0; j = i + 1
        while j < n:
            if cond[j]:
                last = j; k += 1; falses = 0
            else:
                falses += 1
                if falses >= COOLDOWN:
                    break
            j += 1
        out.append((st, last, k)); i = last + COOLDOWN + 1
    return out

def pump_feats(c, v, h, l, st):
    def ret(kk): return c[st] / c[st - kk] - 1 if st - kk >= 0 else np.nan
    base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
    lr = np.diff(np.log(c[max(0, st - 60):st + 1]))
    r5 = ret(5); prev5 = (c[st - 5] / c[st - 10] - 1) if st - 10 >= 0 else np.nan
    return [ret(1), ret(3), r5, ret(15), ret(30), r5 - prev5,
            v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan,
            v[st] / base if base and base > 0 else np.nan,
            lr.std() if len(lr) > 5 else np.nan,
            (h[st - 4:st + 1].max() - l[st - 4:st + 1].min()) / c[st],
            c[st] / h[max(0, st - DAY):st + 1].max() - 1,
            c[st] / l[max(0, st - 60):st + 1].min() - 1]

def dump_feats(c, v, h, l, st):
    def ret(kk): return c[st] / c[st - kk] - 1 if st - kk >= 0 else np.nan
    base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
    lr = np.diff(np.log(c[max(0, st - 60):st + 1]))
    r5 = ret(5); prev10 = (c[st - 5] / c[st - 15] - 1) if st - 15 >= 0 else np.nan
    ds = 0; kk = st
    while kk > 0 and c[kk] < c[kk - 1]:
        ds += 1; kk -= 1
    return [ret(1), ret(3), r5, ret(15), ret(30), ret(60), r5 - prev10,
            v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan,
            v[st] / base if base and base > 0 else np.nan,
            lr.std() if len(lr) > 5 else np.nan,
            (h[st - 14:st + 1].max() - l[st - 14:st + 1].min()) / c[st],
            c[st] / h[max(0, st - DAY):st + 1].max() - 1,
            c[st] / l[max(0, st - DAY):st + 1].min() - 1, ds]

PF = ["r1", "r3", "r5", "r15", "r30", "accel5", "surge15", "surge1",
      "rvol60", "rng5", "dist_hi240", "dist_lo60"]
DF = ["r1", "r3", "r5", "r15", "r30", "r60", "accel", "surge15", "surge1",
      "volreg", "rng15", "hi_d", "lo_d", "dstreak"]

def leg_rows(sym, df, side):
    """Rows for one symbol/leg. Entry = close[st+1]; stop scan from st+2."""
    thr = THR_P if side > 0 else THR_D
    cstop = CSTOP_P if side > 0 else CSTOP_D
    c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
    h = df["high"].to_numpy("float64"); l = df["low"].to_numpy("float64")
    ts = df.index; n = len(c); dv = c * v
    hmax = HORIZONS[-1]
    rows = []
    for st, last, k in clusters(c, thr, side):
        if st < DAY or st + 1 + HORIZONS[0] >= n:
            continue
        base = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
        surge15 = v[st - 14:st + 1].sum() / (base * 15) if base and base > 0 else np.nan
        if side > 0 and not (surge15 >= VOL_MULT):
            continue
        e = c[st + 1]                                   # fill at next bar close
        # one scan to the max horizon: first bar where the catastrophe stop trips
        hi = min(st + 1 + hmax, n - 1)
        stop_t = -1
        for t in range(st + 2, hi + 1):
            if side > 0:
                if h[t] / e - 1 >= cstop:
                    stop_t = t; break
            else:
                if l[t] / e - 1 <= -cstop:
                    stop_t = t; break
        stop_pnl = (min(-cstop, -(c[stop_t] / e - 1)) if side > 0
                    else min(-cstop, (c[stop_t] / e - 1))) - SLIP if stop_t > 0 else np.nan
        feats = pump_feats(c, v, h, l, st) if side > 0 else dump_feats(c, v, h, l, st)
        names = PF if side > 0 else DF
        d = dict(sym=sym, entry=ts[st + 1], k=k, clen=last - st,
                 liq=np.median(dv[max(0, st - DAY):st]),
                 stream=("pump" if side > 0 else "dump"),
                 stop_t=(stop_t - (st + 1)) if stop_t > 0 else -1)
        d.update({names[i]: feats[i] for i in range(len(names))})
        for hz in HORIZONS:
            xt = st + 1 + hz
            if xt >= n:
                d[f"pnl{hz}"] = np.nan; continue
            if 0 < stop_t <= xt:
                pnl = stop_pnl
            else:
                raw = c[xt] / e - 1
                pnl = -raw if side > 0 else raw
            d[f"pnl{hz}"] = pnl - 2 * FEE
        rows.append(d)
    return rows

def main(nsym=None):
    syms = list_symbols()
    if nsym:
        syms = syms[:nsym]
    all_rows = []; loaded = 0; t0 = time.time()
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
        all_rows += leg_rows(sym, df, +1)
        all_rows += leg_rows(sym, df, -1)
        if loaded % 40 == 0:
            print(f"  {loaded} symbols, {len(all_rows)} events, {time.time()-t0:.0f}s", flush=True)
    X = pd.DataFrame(all_rows).sort_values("entry").reset_index(drop=True)
    out = "notebooks/pump_dump_v3/_out/events.parquet"
    X.to_parquet(out)
    for slabel in ("pump", "dump"):
        s = X[X.stream == slabel]
        print(f"{slabel}: {len(s)} events, {s.sym.nunique()} symbols, "
              f"k median {s.k.median():.0f} max {s.k.max()}", flush=True)
    print(f"symbols loaded {loaded}, wrote {out} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None)
    main(ap.parse_args().symbols)
