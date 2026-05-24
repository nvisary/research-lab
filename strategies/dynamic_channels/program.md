# Mean Reversion — Bollinger Bands + RSI

## Thesis
Price returns to the mean after extreme volatility expansions (Bollinger Bands) confirmed by oversold/overbought conditions (RSI).

## Indicators
*   **Bollinger Bands**: Period 20, StdDev 2.0 (tunable).
*   **RSI**: Period 14 (tunable).

## Entry Rules
*   **Long**: `Close < Lower Band` AND `RSI < 30`
*   **Short**: `Close > Upper Band` AND `RSI > 70`

## Exit Rules
*   **Target**: `Close >= Basis (SMA 20)` (for Longs) or `Close <= Basis` (for Shorts).
