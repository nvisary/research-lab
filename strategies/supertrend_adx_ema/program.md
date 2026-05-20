# Supertrend + ADX + Global EMA

## Final Hypothesis
A triple-filter approach (Structural: EMA 200, Strength: ADX > 25, Reactive: Supertrend 10/3.0) on the 1-hour timeframe provides a high-quality trend-following signal for BTCUSDT. While activity is low, the signal quality is high in bullish regimes.

## Final Strategy Logic
1.  **Global Trend:** Long if Price > EMA(200).
2.  **Trend Strength:** Only enter if ADX(14) > 25.
3.  **Signal:** Supertrend(10, 3.0) for entry and trailing exit.
4.  **Timeframe:** 1-hour (best balance of signal vs noise).
5.  **Sizing:** Vol-targeted (ATR 14).
## Research Summary (Iters 17-20)
- **Iter 17 (Lower ADX Threshold)**: Reduced `adx_threshold` to 20. **REVERT**. Trade count up, but Sharpe down to 0.46. 25 is the critical threshold.
- **Iter 18 (Asymmetric Shorts)**: Faster ST (2.0) and lower ADX (20) for shorts. **REVERT**. No significant composite improvement.
- **Iter 19 (Relaxed ADX Slope)**: Entry without `adx_rising` if ADX > 35. **REVERT**. Sharpe fell to 0.86. Acceleration is vital.
- **Iter 20 (Faster ADX)**: Reduced `adx_period` to 7. **REVERT**. High sensitivity to noise, negative Sharpe.

## Final Conclusions
- **Selectivity is Edge**: The strategy's profitability comes from its extreme selectivity. Broadening the criteria consistently degrades the signal-to-noise ratio.
- **Momentum is Key**: The `adx_rising` filter is the most effective way to distinguish between a established trend and a stalling one.
- **Timeframe Robustness**: 1-hour remains the "goldilocks" timeframe—fast enough to catch trends, slow enough to filter noise.
- **Current Champion (Iter 8)**: This remains the most robust configuration, achieving a strong OOS Sharpe of 2.02 and a positive composite score despite low frequency.

## Champion Parameters
- `st_period`: 10
- `st_mult`: 3.0
- `adx_period`: 14
- `adx_threshold`: 25
- `adx_rising`: True (Entry only)
- `volume_filter`: volume > EMA(20)
- `long_only`: 1
- `DEFAULT_TF`: "1h"

- `st_mult`: 3.0
- `adx_period`: 14
- `adx_threshold`: 25
- `ema_period`: 200
- `DEFAULT_TF`: "1h"
