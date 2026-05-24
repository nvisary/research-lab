# Camarilla Breakout

## Baseline Thesis

Switching from Mean Reversion to Trend Following. Crypto markets exhibit strong, sustained trends. The Camarilla R4 and S4 levels represent significant daily extremes; breaking these levels often signals the start of a directional impulse.

**Camarilla Pivots (Daily)**
- `Range = H - L`
- `R4 = C + Range * 1.1 / 2`
- `S4 = C - Range * 1.1 / 2`

**Breakout Thesis:**
- **Long:** 1h Close > Daily R4.
- **Short:** 1h Close < Daily S4.

**Exit Mechanism:**
- Trend following requires letting profits run. We will use an ATR-based Chandelier trailing stop.
  - Long Stop: `Highest High Since Entry - k * ATR`
  - Short Stop: `Lowest Low Since Entry + k * ATR`

**Universe Expansion:**
- Expanding from 3 to 10 majors to increase trade frequency and capture cross-sectional trend dispersion.

## Iteration Plan

1. **Iter 1 (Baseline):** Pure R4/S4 crossover with a wide `trail_k=3.0` trailing stop on 10 coins.
2. **Iter 2 (HTF Trend Gate):** Only take breakouts aligned with a Daily EMA trend (avoiding counter-trend fakeouts).
3. **Iter 3 (Vol-Cap Filter):** Reject breakouts during extreme volatility spikes (ATR/Close > 90th percentile) to avoid exhaustion fakeouts.
4. **Iter 4 (Volume Confirmation):** Require breakout candle volume to be > 1.2x the rolling average volume.
5. **Iter 5 (Asymmetric Trailing):** Tighter trailing stop for shorts due to crypto's upward drift bias.
