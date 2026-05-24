# Mean Reversion — Rolling Fibonacci Channels (High-Low Bands)

## Thesis
Statistical reversion to the median using dynamically updating historical boundaries based on Fibonacci ratios. The strategy assumes that when price overextends to the 61.8% or 38.2% levels (retraced from the top/bottom) of its recent range and shows signs of rejection, it will likely revert to the 50% median. To avoid being "run over" by strong trends, a 200-period EMA trend filter is applied.

## Indicators
- **Rolling Window (N)**: 50 periods (default).
- **Highest High**: Rolling maximum of High over N periods.
- **Lowest Low**: Rolling minimum of Low over N periods.
- **Range**: Highest High - Lowest Low.
- **Level_618 (Support)**: `Lowest_Low + (Range * 0.382)` (38.2% absolute level).
- **Level_382 (Resistance)**: `Lowest_Low + (Range * 0.618)` (61.8% absolute level).
- **Median**: `Lowest_Low + (Range * 0.50)` (Target Level).
- **Trend Filter**: 200-period EMA.

## Entry Rules
- **Long Entry**:
  - `Low <= Support` (Level_618)
  - `Close > Support`
  - `Close > EMA 200` (Trend is Up or Sideways)
- **Short Entry**:
  - `High >= Resistance` (Level_382)
  - `Close < Resistance`
  - `Close < EMA 200` (Trend is Down or Sideways)

## Exit Rules
- **Take Profit (TP)**: price hits the `Median` (50% level).
- **Stop Loss (SL)**: Fixed distance (0.5% or 1x ATR) outside the local 0% or 100% boundary.
