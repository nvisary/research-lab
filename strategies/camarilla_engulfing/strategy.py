"""camarilla_breakout — Robust Rolling Camarilla Breakout (1h).

Thesis: structural breakouts on 1h TF using rolling 12h pivots.
Includes inverse-volatility sizing and strict momentum gating to improve trust.
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "TRXUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "SHIBUSDT",
    "UNIUSDT",
    "BCHUSDT",
    "XLMUSDT",
    "ALGOUSDT",
    "ICPUSDT",
    "XMRUSDT",
    "VETUSDT",
    "FILUSDT",
    "HBARUSDT",
    "OPUSDT",
    "ARBUSDT",
    "LDOUSDT",
    "APTUSDT",
    "STXUSDT",
    "ATOMUSDT",
    "RNDRUSDT",
    "INJUSDT",
    "TIAUSDT",
    "SEIUSDT",
    "SUIUSDT",
    "GRTUSDT",
    "MKRUSDT",
    "FTMUSDT",
    "EGLDUSDT",
    "IMXUSDT",
    "ROSEUSDT",
    "FETUSDT",
    "AGIXUSDT",
    "OCEANUSDT",
    "GALAUSDT",
]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "atr_period": 24,
    "trail_k_long": 3.0,
    "trail_k_short": 2.0,
    "ema_period": 100,
    "adx_period": 14,
    "adx_min": 20,
    "rsi_period": 14,
    "rsi_ub": 80,
    "rsi_lb": 20,
    "vol_target": 0.02,
    "hold_bars": 12,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "atr_period": (12, 48),
    "trail_k_long": (2.0, 5.0),
    "trail_k_short": (1.0, 4.0),
    "ema_period": (50, 200),
    "adx_min": (15, 30),
    "vol_target": (0.01, 0.05),
    "hold_bars": (6, 24),
    "long_only": (0, 1),
}


def _calc_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> pd.Series:
    prev_high, prev_low, prev_close = high.shift(1), low.shift(1), close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    up_move = high - prev_high
    dn_move = prev_low - low
    pos_dm = pd.Series(
        np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    neg_dm = pd.Series(
        np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0), index=high.index
    )
    alpha = 1.0 / period
    pos_di = (
        100
        * pos_dm.ewm(alpha=alpha, adjust=False).mean()
        / tr.ewm(alpha=alpha, adjust=False).mean()
    )
    neg_di = (
        100
        * neg_dm.ewm(alpha=alpha, adjust=False).mean()
        / tr.ewm(alpha=alpha, adjust=False).mean()
    )
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    atr_period = int(params.get("atr_period", 24))
    trail_k_long = float(params.get("trail_k_long", 3.0))
    trail_k_short = float(params.get("trail_k_short", 2.0))
    ema_period = int(params.get("ema_period", 100))
    adx_period = int(params.get("adx_period", 14))
    adx_min = float(params.get("adx_min", 20))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_ub = float(params.get("rsi_ub", 80))
    rsi_lb = float(params.get("rsi_lb", 20))
    vol_target = float(params.get("vol_target", 0.02))
    hold_bars = int(params.get("hold_bars", 12))
    long_only = int(params.get("long_only", 0)) == 1

    r_h = high.shift(1).rolling(12).max()
    r_l = low.shift(1).rolling(12).min()
    r_c = close.shift(1)
    rolling_r4 = r_c + (r_h - r_l) * 1.1 / 2
    rolling_s4 = r_c - (r_h - r_l) * 1.1 / 2

    atr = (
        pd.concat(
            [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
            axis=1,
        )
        .max(axis=1)
        .ewm(span=atr_period, adjust=False)
        .mean()
    )

    bb_sma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bbw = (4 * bb_std) / bb_sma.replace(0, np.nan)
    bbw_median = bbw.rolling(100).median()
    vol_hot = (bbw > bbw_median).fillna(False)

    ema = close.ewm(span=ema_period, adjust=False).mean()
    adx = _calc_adx(high, low, close, adx_period)
    adx_rising = adx > adx.shift(1)  # Added for Iter 5

    delta = close.diff()
    gain, loss = (
        (delta.where(delta > 0, 0)).rolling(rsi_period).mean(),
        (-delta.where(delta < 0, 0)).rolling(rsi_period).mean(),
    )
    rsi = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))

    sizing_base = (vol_target / (atr / close).fillna(0.03)).clip(0.1, 2.0)
    sizing_arr = sizing_base.values

    enter_long = (
        (close > rolling_r4)
        & (adx > adx_min)
        & (close > ema)
        & (rsi < rsi_ub)
        & vol_hot
        & adx_rising
    )
    enter_short = (
        (close < rolling_s4)
        & (adx > adx_min)
        & (close < ema)
        & (rsi > rsi_lb)
        & vol_hot
        & adx_rising
    )

    pos_arr = np.zeros(len(df))
    curr_pos, stop_price, hh, ll, bars = 0.0, 0.0, 0.0, float("inf"), 0
    el_v, es_v, c_v, h_v, l_v, a_v = (
        enter_long.values,
        enter_short.values,
        close.values,
        high.values,
        low.values,
        atr.values,
    )

    for i in range(len(df)):
        if curr_pos == 0:
            if el_v[i]:
                curr_pos = sizing_arr[i]
                hh, bars = c_v[i], 1
                stop_price = hh - (trail_k_long * a_v[i])
            elif not long_only and es_v[i]:
                curr_pos = -sizing_arr[i]
                ll, bars = c_v[i], 1
                stop_price = ll + (trail_k_short * a_v[i])
        else:
            exit_t, bars = False, bars + 1
            if curr_pos > 0:
                hh = max(hh, h_v[i])
                stop_price = max(stop_price, hh - (trail_k_long * a_v[i]))
                if c_v[i] < stop_price:
                    exit_t = True
            else:
                ll = min(ll, l_v[i])
                stop_price = min(stop_price, ll + (trail_k_short * a_v[i]))
                if c_v[i] > stop_price:
                    exit_t = True

            if bars >= hold_bars:
                exit_t = True

            if exit_t:
                curr_pos, bars = 0.0, 0
                if el_v[i]:
                    curr_pos = sizing_arr[i]
                    hh, bars = c_v[i], 1
                    stop_price = hh - (trail_k_long * a_v[i])
                elif not long_only and es_v[i]:
                    curr_pos = -sizing_arr[i]
                    ll, bars = c_v[i], 1
                    stop_price = ll + (trail_k_short * a_v[i])

        pos_arr[i] = curr_pos

    return pd.Series(pos_arr, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": df.index,
                    "symbol": symbol,
                    "position": _signals_for_symbol(df, params).values,
                }
            )
        )
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["timestamp", "symbol", "position"])
    )
