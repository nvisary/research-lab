"""fib_mr_channels — Mean Reversion — Rolling Fibonacci Channels (High-Low Bands).

Thesis: Statistical reversion to the median using dynamically updating historical
boundaries based on Fibonacci ratios.
- Long: Low <= Support (38.2% level), Close > Support. Target: Median (50%).
- Short: High >= Resistance (61.8% level), Close < Resistance. Target: Median (50%).
- Exit: TP at Median, SL at fixed distance outside local boundary.
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
]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "window": 33,
    "sl_pct": 0.00965,  # 0.5%
    "atr_period": 22,
    "use_atr_sl": 0,  # 0=fixed %, 1=ATR-based
    "atr_k": 2.08945,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "window": (20, 100),
    "sl_pct": (0.002, 0.02),
    "atr_period": (10, 30),
    "use_atr_sl": (0, 1),
    "atr_k": (1.0, 3.0),
    "long_only": (0, 1),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Per-symbol position series."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    window = int(params.get("window", 50))
    sl_pct = float(params.get("sl_pct", 0.005))
    atr_period = int(params.get("atr_period", 14))
    use_atr_sl = int(params.get("use_atr_sl", 0)) == 1
    atr_k = float(params.get("atr_k", 1.5))
    long_only = int(params.get("long_only", 0)) == 1

    # --- 1. Indicators ---
    hh = high.rolling(window=window).max()
    ll = low.rolling(window=window).min()
    rng = hh - ll

    # Trend Filter: 200 EMA
    ema_trend = close.ewm(span=200, adjust=False).mean()
    trend_up = close > ema_trend
    trend_dn = close < ema_trend

    # Standard Fib levels from bottom: 0.382 and 0.618
    # Support (Lower) = 38.2% level
    # Resistance (Upper) = 61.8% level
    level_382 = ll + rng * 0.382  # Support (Lower)
    level_618 = ll + rng * 0.618  # Resistance (Upper)
    median = ll + rng * 0.50

    # ATR for SL if enabled
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()

    # --- 2. Entry Signals ---
    # Long: Low touched/pierced Support (level_382 or Lowest_Low) but Close > Support
    # Filter: Only Long if trend is UP (fade pullback in uptrend) or FLAT (we'll use EMA as proxy)
    enter_long = (low <= level_382) & (close > level_382) & trend_up

    # Short: High touched/pierced Resistance (level_618 or Highest_High) but Close < Resistance
    # Filter: Only Short if trend is DOWN (fade bounce in downtrend)
    enter_short = (high >= level_618) & (close < level_618) & trend_dn
    # --- 3. State Machine (Positions with TP/SL) ---
    pos = pd.Series(0.0, index=df.index)
    current_pos = 0.0
    stop_price = 0.0
    take_profit = 0.0

    close_arr = close.values
    high_arr = high.values
    low_arr = low.values
    hh_arr = hh.values
    ll_arr = ll.values
    median_arr = median.values
    l382_arr = level_382.values
    l618_arr = level_618.values
    atr_arr = atr.values

    enter_long_arr = enter_long.values
    enter_short_arr = enter_short.values

    pos_arr = np.zeros(len(df))

    for i in range(len(df)):
        if np.isnan(l382_arr[i]) or np.isnan(l618_arr[i]):
            continue

        if current_pos == 0:
            if enter_long_arr[i]:
                current_pos = 1.0
                take_profit = median_arr[i]
                if use_atr_sl:
                    stop_price = ll_arr[i] - (atr_k * atr_arr[i])
                else:
                    stop_price = ll_arr[i] * (1.0 - sl_pct)
            elif not long_only and enter_short_arr[i]:
                current_pos = -1.0
                take_profit = median_arr[i]
                if use_atr_sl:
                    stop_price = hh_arr[i] + (atr_k * atr_arr[i])
                else:
                    stop_price = hh_arr[i] * (1.0 + sl_pct)
        else:
            # Check Exit
            exit_trade = False

            if current_pos == 1.0:
                # TP at Median or above
                if high_arr[i] >= take_profit:
                    exit_trade = True
                # SL at stop_price
                elif low_arr[i] <= stop_price:
                    exit_trade = True
            elif current_pos == -1.0:
                # TP at Median or below
                if low_arr[i] <= take_profit:
                    exit_trade = True
                # SL at stop_price
                elif high_arr[i] >= stop_price:
                    exit_trade = True

            if exit_trade:
                current_pos = 0.0
                # Check for immediate reversal entry
                if enter_long_arr[i]:
                    current_pos = 1.0
                    take_profit = median_arr[i]
                    if use_atr_sl:
                        stop_price = ll_arr[i] - (atr_k * atr_arr[i])
                    else:
                        stop_price = ll_arr[i] * (1.0 - sl_pct)
                elif not long_only and enter_short_arr[i]:
                    current_pos = -1.0
                    take_profit = median_arr[i]
                    if use_atr_sl:
                        stop_price = hh_arr[i] + (atr_k * atr_arr[i])
                    else:
                        stop_price = hh_arr[i] * (1.0 + sl_pct)

        pos_arr[i] = current_pos

    pos.iloc[:] = pos_arr
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params)
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": df.index,
                    "symbol": symbol,
                    "position": pos.values,
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
