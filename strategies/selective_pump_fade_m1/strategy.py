"""Selective 1m pump fade with symbol-level meta filter.

This starts from the accepted `pump_dump_m1` shock detector, then applies a
simple symbol allowlist before emitting trades. The first hypothesis is that the
microstructure fade is not uniform across liquid perps: some symbols keep
overshooting after public pump shocks, while majors and some high-beta names
trend through the fade often enough to dilute expectancy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "WLDUSDT",
    "1000PEPEUSDT",
]

DEFAULT_TF: str = "1min"

PUMP_SHORT_SYMBOLS = {
    "XRPUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "WLDUSDT",
    "1000PEPEUSDT",
}

DUMP_RECLAIM_SYMBOLS = {
    "ETHUSDT",
    "DOGEUSDT",
    "WLDUSDT",
}

DEFAULT_PARAMS: dict = {
    "lookback": 120,
    "min_history": 60,
    "return_z": 4.0,
    "volume_z": 4.0,
    "min_abs_return_bps": 60.0,
    "entry_delay_bars": 1,
    "hold_bars": 50,
    "cooldown_bars": 15,
    "event_weight": 0.08,
    "long_weight": 0.05,
    "min_volume_usd": 100_000.0,
    "regime_window": 240,
    "flat_return_bps": 120.0,
    "max_up_drift_bps": 20.0,
    "long_min_drift_bps": -250.0,
    "long_max_drift_bps": 180.0,
    "vol_quantile_window": 2880,
    "vol_quantile_min": 0.50,
    "long_reclaim_bars": 30,
    "long_reclaim_ema": 20,
    "long_reclaim_vwap": 60,
    "long_min_bounce_bps": 40.0,
    "trade_pumps": 1,
    "trade_dumps": 1,
}

PARAM_SPACE: dict = {
    "lookback": (60, 360),
    "min_history": (30, 240),
    "return_z": (2.5, 7.0),
    "volume_z": (2.5, 7.0),
    "min_abs_return_bps": (30.0, 200.0),
    "entry_delay_bars": (1, 5),
    "hold_bars": (5, 60),
    "cooldown_bars": (5, 60),
    "event_weight": (0.01, 0.15),
    "long_weight": (0.01, 0.10),
    "min_volume_usd": (0.0, 1_000_000.0),
    "regime_window": (60, 720),
    "flat_return_bps": (30.0, 400.0),
    "max_up_drift_bps": (-100.0, 200.0),
    "long_min_drift_bps": (-800.0, 100.0),
    "long_max_drift_bps": (0.0, 500.0),
    "vol_quantile_window": (720, 10080),
    "vol_quantile_min": (0.5, 0.95),
    "long_reclaim_bars": (5, 90),
    "long_reclaim_ema": (5, 80),
    "long_reclaim_vwap": (15, 240),
    "long_min_bounce_bps": (0.0, 120.0),
    "trade_pumps": (0, 1),
    "trade_dumps": (0, 1),
}


RAW_SIZING = True
MAX_POSITION = 0.20


def _zscore(value: pd.Series, lookback: int, min_history: int) -> pd.Series:
    mean = value.rolling(lookback, min_periods=min_history).mean()
    std = value.rolling(lookback, min_periods=min_history).std(ddof=1)
    return (value - mean) / std.replace(0.0, np.nan)


def _positions_for_symbol(symbol: str, df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    lookback = int(params.get("lookback", 120))
    min_history = int(params.get("min_history", 60))
    return_z = float(params.get("return_z", 4.0))
    volume_z = float(params.get("volume_z", 4.0))
    min_abs_return_bps = float(params.get("min_abs_return_bps", 80.0))
    entry_delay = max(1, int(params.get("entry_delay_bars", 1)))
    hold_bars = max(1, int(params.get("hold_bars", 20)))
    cooldown_bars = max(0, int(params.get("cooldown_bars", 15)))
    event_weight = float(params.get("event_weight", 0.04))
    long_weight = float(params.get("long_weight", event_weight))
    min_volume_usd = float(params.get("min_volume_usd", 0.0))
    regime_window = max(2, int(params.get("regime_window", 240)))
    flat_return_bps = float(params.get("flat_return_bps", 120.0))
    max_up_drift_bps = float(params.get("max_up_drift_bps", flat_return_bps))
    long_min_drift_bps = float(params.get("long_min_drift_bps", -flat_return_bps))
    long_max_drift_bps = float(params.get("long_max_drift_bps", flat_return_bps))
    vol_quantile_window = max(regime_window, int(params.get("vol_quantile_window", 2880)))
    vol_quantile_min = float(params.get("vol_quantile_min", 0.75))
    long_reclaim_bars = max(1, int(params.get("long_reclaim_bars", 30)))
    long_reclaim_ema = max(2, int(params.get("long_reclaim_ema", 20)))
    long_reclaim_vwap = max(2, int(params.get("long_reclaim_vwap", 60)))
    long_min_bounce_bps = float(params.get("long_min_bounce_bps", 0.0))
    trade_pumps = int(params.get("trade_pumps", 1)) == 1
    trade_dumps = int(params.get("trade_dumps", 1)) == 1

    log_ret = np.log(close).diff()
    ret_bps = log_ret * 10_000.0
    ret_z = _zscore(log_ret, lookback, min_history)
    vol_z = _zscore(volume, lookback, min_history)
    volume_usd = volume * close
    signed_regime_ret_bps = (np.log(close) - np.log(close).shift(regime_window)) * 10_000.0
    regime_ret_bps = signed_regime_ret_bps.abs()
    realized_vol = log_ret.rolling(regime_window, min_periods=regime_window).std(ddof=1)
    vol_rank = realized_vol.rolling(
        vol_quantile_window,
        min_periods=max(regime_window, vol_quantile_window // 4),
    ).rank(pct=True)

    liquid_enough = volume_usd >= min_volume_usd
    shock_big_enough = ret_bps.abs() >= min_abs_return_bps
    flat_high_vol = (
        (regime_ret_bps <= flat_return_bps)
        & (signed_regime_ret_bps <= max_up_drift_bps)
        & (vol_rank >= vol_quantile_min)
    )
    pump = (
        (symbol in PUMP_SHORT_SYMBOLS)
        & (ret_z >= return_z)
        & (vol_z >= volume_z)
        & shock_big_enough
        & liquid_enough
        & flat_high_vol
    )
    dump = (ret_z <= -return_z) & (vol_z >= volume_z) & shock_big_enough & liquid_enough & flat_high_vol

    ema_reclaim = close.ewm(span=long_reclaim_ema, adjust=False, min_periods=long_reclaim_ema).mean()
    vwap_num = (close * volume).rolling(long_reclaim_vwap, min_periods=long_reclaim_vwap).sum()
    vwap_den = volume.rolling(long_reclaim_vwap, min_periods=long_reclaim_vwap).sum()
    reclaim_vwap = vwap_num / vwap_den.replace(0.0, np.nan)
    recent_dump = dump.fillna(False).rolling(long_reclaim_bars + 1, min_periods=1).max().astype(bool)
    long_regime = signed_regime_ret_bps.between(long_min_drift_bps, long_max_drift_bps)
    dump_idx = np.flatnonzero(dump.fillna(False).to_numpy())
    last_dump_idx = np.full(len(df), -1, dtype=np.int64)
    if len(dump_idx) > 0:
        last_dump_idx[dump_idx] = dump_idx
        last_dump_idx = np.maximum.accumulate(last_dump_idx)
    valid_dump = last_dump_idx >= 0
    post_dump_bounce_bps = np.full(len(df), np.nan, dtype=np.float64)
    log_close_values = np.log(close).to_numpy()
    post_dump_bounce_bps[valid_dump] = (
        log_close_values[valid_dump] - log_close_values[last_dump_idx[valid_dump]]
    ) * 10_000.0
    reclaim_confirmed = (
        (symbol in DUMP_RECLAIM_SYMBOLS)
        & recent_dump
        & (post_dump_bounce_bps >= long_min_bounce_bps)
        & (close > ema_reclaim)
        & (close > reclaim_vwap)
        & (log_ret > 0.0)
        & liquid_enough
        & long_regime
        & (vol_rank >= vol_quantile_min)
    )

    side = np.zeros(len(df), dtype=np.float64)
    weight = np.zeros(len(df), dtype=np.float64)
    if trade_pumps:
        mask = pump.fillna(False).to_numpy()
        side[mask] = -1.0
        weight[mask] = event_weight
    if trade_dumps:
        mask = reclaim_confirmed.fillna(False).to_numpy()
        idx = np.flatnonzero(mask)
        idx = idx[side[idx] == 0.0]
        side[idx] = 1.0
        weight[idx] = long_weight

    pos = np.zeros(len(df), dtype=np.float64)
    next_allowed_event = 0
    for event_idx in np.flatnonzero(side != 0.0):
        if event_idx < next_allowed_event:
            continue
        entry_idx = event_idx + entry_delay
        if entry_idx >= len(pos):
            continue
        if pos[entry_idx] != 0.0:
            continue
        exit_idx = min(len(pos), entry_idx + hold_bars)
        pos[entry_idx:exit_idx] = side[event_idx] * weight[event_idx]
        next_allowed_event = event_idx + cooldown_bars

    return pd.Series(pos, index=df.index)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _positions_for_symbol(symbol, df, params)
        frames.append(pd.DataFrame({
            "timestamp": df.index,
            "symbol": symbol,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
