# Pivot Points Mean Reversion with CCI

## Thesis
Mean reversion strategy using Daily Pivot Points as support/resistance levels and CCI (Commodity Channel Index) as a momentum/overbought/oversold filter.

## Logic
- **Pivots**: Standard daily pivots calculated from previous day's OHLC.
  - $P = (H + L + C) / 3$
  - $S1 = 2P - H$, $R1 = 2P - L$
  - $S2 = P - (H - L)$, $R2 = P + (H - L)$
- **CCI**: 20-period CCI on the trading timeframe.
- **Entry Long**: 
  - Price is near or below $S1$ or $S2$.
  - $CCI < -100$ and starts rising.
- **Entry Short**:
  - Price is near or above $R1$ or $R2$.
  - $CCI > 100$ and starts falling.
- **Exit**:
  - Long: Price reaches $P$ or $CCI > 100$.
  - Short: Price reaches $P$ or $CCI < -100$.

## Results (Baseline)
- **Period**: 2024-01-01 to 2024-04-01
- **Train Sharpe**: -1.29
- **OOS Sharpe**: 4.33
- **N Trades**: 101 (74 Train + 27 OOS)
- **Max Drawdown**: 20.6% (Train), 8.7% (OOS)

## Observations
- High variance between Train and OOS periods.
- Significant time in position (~64%).
- Profit factor in OOS is 2.41, but in Train is 0.77.
