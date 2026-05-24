# Volatility Breakout (Toby Crabel Style)

## Goal
Capture explosive moves following periods of consolidation by entering in the direction of the momentum when price breaks out of a volatility-adjusted range.

## Logic
- **Base Timeframe:** 1-minute bars.
- **Reference Price (Anchor):** The daily open price (00:00 UTC).
- **Volatility:** Average True Range (ATR) calculated over a lookback period (e.g., 20 hours).
- **Breakout Threshold:** `Anchor ± (k * ATR)`.
- **Volume Filter:** Breakout must occur on volume greater than `v_mult * average_volume` over the same lookback period.

### Entry Rules
1. **Long:** `Price > Daily Open + (k * ATR)` AND `Volume > v_mult * AvgVolume`.
2. **Short:** `Price < Daily Open - (k * ATR)` AND `Volume > v_mult * AvgVolume`.

### Exit Rules
1. **Trailing Stop:** Exit if price crosses a trailing ATR-based stop (e.g., `Highest High - 2 * ATR` for longs).
2. **Time Exit:** Optional closing at the end of the day or after a fixed duration.
3. **Trend Reversal:** Exit if a signal in the opposite direction is triggered.

## Parameters
- `atr_period`: Lookback for ATR and Volume average (in bars).
- `k`: Multiplier for the breakout threshold.
- `v_mult`: Multiplier for the volume filter.
- `stop_mult`: Multiplier for the ATR trailing stop.
- `vol_target`: Annualized volatility target for position sizing.

## Advantages
- Effective in high-momentum markets like Crypto.
- High signal-to-noise ratio due to volume and volatility filters.
- Clear, objective entry and exit points.

## Risks
- False breakouts (whipsaws) in low-volatility, range-bound environments.
- Slippage during high-volatility moves.
