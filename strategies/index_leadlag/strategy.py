"""index_leadlag — trade alts vs a self-built market index.

Construct a cap-proxy index from a top-30 USDT-perp basket:
    w_i[t] = rolling-30d dollar-volume share of symbol i
    r_idx[t] = Σ_i w_i[t-1] * r_i[t]       # past-weights × current returns
    idx[t]   = cumprod(1 + r_idx)

Per-symbol signal (lead-lag mean reversion vs index):
    spread_L = log_ret_L(alt) - log_ret_L(idx)        # alt under/outperformance
    z = (spread_L - mean(spread_L, zw)) / std(spread_L, zw)
    LONG  when z < -entry_k AND idx_ret_L > +idx_move : index ran, alt lagged → catch-up
    SHORT when z > +entry_k AND idx_ret_L < -idx_move : index dumped, alt held → catch-down
    EXIT  when |z| < exit_k

No lookahead: weights use lagged dollar-volume (rolling sum then shift(1)),
index uses lagged weights × current returns, all per-symbol signals are
.shift(1) before being emitted.

Equal cash split across symbols (raw sizing, 1/n per symbol).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Top-30 USDT-perp universe — selected by typical Bybit dollar-volume rank
# and verified to have full 2024-01 → 2026-04 coverage on disk.
DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "BCHUSDT", "NEARUSDT", "ATOMUSDT", "LTCUSDT", "OPUSDT",
    "INJUSDT", "FILUSDT", "UNIUSDT", "SUIUSDT", "ICPUSDT",
    "ETCUSDT", "HBARUSDT", "SANDUSDT", "LDOUSDT", "XLMUSDT",
    "TIAUSDT", "SEIUSDT", "TONUSDT", "1000PEPEUSDT", "IMXUSDT",
]

DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    # Index construction
    "dvol_window": 720,        # 30d on 1h TF (24 * 30) — rolling $-volume sum
    # Lead-lag signal
    "lookback": 24,            # bars over which to measure alt-vs-index spread
    "z_window": 168,           # 1w of 1h bars — rolling baseline for spread z-score
    "entry_k": 2.2,            # z threshold to open
    "exit_k": 0.5,             # |z| < exit_k closes
    "idx_move": 0.0,           # disable directional gate — CS-z already filters extremes
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "dvol_window": (168, 1440),
    "lookback": (6, 96),
    "z_window": (48, 480),
    "entry_k": (1.0, 3.5),
    "exit_k": (0.0, 1.5),
    "idx_move": (0.0, 0.03),
    "long_only": (0, 1),
}


RAW_SIZING = True


def _build_index(
    data: dict[str, pd.DataFrame],
    dvol_window: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (log_idx_per_symbol_DF, log_close_DF).

    *** Structural change: jackknife / exclude-own index. ***
    For each symbol s, the index it is compared against is the basket of
    the OTHER symbols (re-normalized to sum=1). This removes the structural
    bias where a heavy-weight symbol's spread vs the full index is dampened
    (it IS the index) while a light-weight symbol's is exaggerated — the
    two were not comparable in a cross-section. Now every symbol sees a
    fair benchmark of "what did the rest of the basket do?".
    """
    frames = {s: df for s, df in data.items() if df is not None and not df.empty}
    if not frames:
        raise ValueError("no symbols with data")
    idx = sorted(set().union(*(df.index for df in frames.values())))
    idx = pd.DatetimeIndex(idx)

    closes = pd.DataFrame(index=idx)
    dvol = pd.DataFrame(index=idx)
    for sym, df in frames.items():
        closes[sym] = df["close"].reindex(idx)
        dvol[sym] = (df["close"] * df["volume"]).reindex(idx)

    log_close = np.log(closes)
    log_ret = log_close.diff()

    dvol_roll = dvol.rolling(dvol_window, min_periods=max(dvol_window // 4, 24)).sum()
    w_full = dvol_roll.div(dvol_roll.sum(axis=1), axis=0)
    w_lag = w_full.shift(1)  # (T, N) — past-only weights

    # Full-basket weighted contribution at each bar, per symbol:
    # contrib_i[t] = w_i[t-1] * r_i[t]. Sum across i = full index return.
    contrib = w_lag.fillna(0.0) * log_ret.fillna(0.0)
    full_r_idx = contrib.sum(axis=1)
    full_w_sum = w_lag.fillna(0.0).sum(axis=1)

    # Excl-own index return for symbol s:
    #   r_idx_excl[s, t] = (full_r_idx[t] - contrib[s, t]) / (full_w_sum[t] - w[s, t-1])
    # i.e. renormalize remaining weights to sum 1.
    log_idx_per_sym = pd.DataFrame(index=idx, columns=log_close.columns, dtype=float)
    for s in log_close.columns:
        denom = (full_w_sum - w_lag[s].fillna(0.0))
        r_excl = (full_r_idx - contrib[s]) / denom.replace(0.0, np.nan)
        # Mask early bars where weights haven't settled (any-NaN row in w_lag).
        r_excl = r_excl.where(w_lag.notna().any(axis=1))
        log_idx_per_sym[s] = r_excl.fillna(0.0).cumsum().where(r_excl.notna())

    return log_idx_per_sym, log_close


def _positions_from_z(
    z: pd.Series,
    idx_ret_L: pd.Series,
    params: dict,
    n_universe: int,
    valid: pd.Series,
) -> pd.Series:
    entry_k = float(params["entry_k"])
    exit_k = float(params["exit_k"])
    idx_move = float(params["idx_move"])
    long_only = int(params.get("long_only", 0)) == 1

    long_entry = (z < -entry_k) & (idx_ret_L > idx_move) & valid
    short_entry = (z > entry_k) & (idx_ret_L < -idx_move) & valid
    exit_zone = z.abs() < exit_k

    state = np.zeros(len(z), dtype=np.float64)
    cur = 0.0
    z_v = z.to_numpy()
    le = long_entry.fillna(False).to_numpy()
    se = short_entry.fillna(False).to_numpy()
    ex = exit_zone.fillna(False).to_numpy()
    for i in range(len(z_v)):
        if np.isnan(z_v[i]):
            cur = 0.0
            state[i] = 0.0
            continue
        if cur == 0.0:
            if le[i]:
                cur = 1.0
            elif se[i] and not long_only:
                cur = -1.0
        else:
            if ex[i]:
                cur = 0.0
            elif cur > 0 and se[i] and not long_only:
                cur = -1.0
            elif cur < 0 and le[i]:
                cur = 1.0
        state[i] = cur

    pos = pd.Series(state, index=z.index)
    pos = pos / max(n_universe, 1)
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    dvol_window = int(params.get("dvol_window", 720))
    lookback = int(params["lookback"])
    log_idx, log_close = _build_index(data, dvol_window)
    syms = [s for s in log_close.columns if log_close[s].notna().any()]
    n = len(syms)

    # *** Structural change: cross-sectional z on top of exclude-own backbone. ***
    # With exclude-own index, every symbol's spread = alt_ret_L − own-jackknife-idx_ret_L
    # has a comparable distribution across symbols. Now z by cross-section
    # (at each bar across the universe), not by own history — asks
    # "is this the most laggy/leading alt of the basket right now?".
    log_close_v = log_close[syms]
    log_idx_v = log_idx[syms]
    alt_ret_L = log_close_v - log_close_v.shift(lookback)      # (T, N)
    idx_ret_L = log_idx_v - log_idx_v.shift(lookback)          # (T, N) — per symbol
    spread_df = alt_ret_L - idx_ret_L                          # (T, N)

    valid_count = spread_df.notna().sum(axis=1)
    cs_mean = spread_df.mean(axis=1, skipna=True)
    cs_std = spread_df.std(axis=1, skipna=True)
    z_df = spread_df.sub(cs_mean, axis=0).div(cs_std, axis=0)
    z_df = z_df.where(valid_count >= 5)

    frames = []
    for sym in syms:
        valid = log_close[sym].notna()
        pos = _positions_from_z(
            z_df[sym], idx_ret_L[sym], params, n_universe=n, valid=valid,
        )
        pos = pos.where(valid, 0.0)
        frames.append(pd.DataFrame({
            "timestamp": pos.index,
            "symbol": sym,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
