# VWAP Mean Reversion (1m)

## Thesis
1-minute crypto charts outside of strong news are often chaotic and mean-reverting due to market maker activity. Extreme deviations from VWAP (Volume Weighted Average Price) tend to be corrected.

## Logic
- **Indicator**: Sliding VWAP (default window: 240 minutes) with Standard Deviation bands.
- **Entry (Long)**: Price punctures the lower band (-3σ or -4σ) and then a 1-minute candle closes back above the band.
- **Entry (Short)**: Price punctures the upper band (+3σ or +4σ) and then a 1-minute candle closes back below the band.
- **Exit (Take Profit)**: Price touches the VWAP line (the mean).
- **Risk Management**: Mandatory Stop-Loss (e.g., 1.5%) to protect against runaway trends.

## Advantages
- High win rate in ranging markets.
- Clear, objective entry and exit points.
- Leverages 1-minute granularity to catch sharp reversals.

## Risks
- "Runaway" trends can cause significant drawdowns without a strict SL.
- High trading frequency can lead to high transaction costs.
