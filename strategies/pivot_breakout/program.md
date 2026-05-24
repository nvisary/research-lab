# Trend Following — R1/S1 Pivot Breakout with PP Filter

## Core Logic
Capture strong intraday expansion when the market breaks out of the initial daily trading range.

## Indicators & Calculations
*   **Standard Daily Pivot Points** calculated from previous day's OHLC:
    *   `PP = (High_prev + Low_prev + Close_prev) / 3`
    *   `R1 = (2 * PP) - Low_prev`
    *   `S1 = (2 * PP) - High_prev`
    *   `R2 = PP + (High_prev - Low_prev)`
    *   `S2 = PP - (High_prev - Low_prev)`
*   **Intraday Open Price** (`Price_Open_Day`) at 00:00 UTC.

## Entry Rules
Execute on Bar Close, 1H timeframe.

*   **Long Entry:** `Close > R1` AND `Price_Open_Day > PP`
*   **Short Entry:** `Close < S1` AND `Price_Open_Day < PP`

## Exit Rules
*   **Take Profit (TP):** Target R2 for Longs, S2 for Shorts (Hard coded levels).
*   **Stop Loss (SL):** Triggers if price crosses back past the Central PP line (or a fixed % below entry, tunable parameter `sl_mode` and `sl_fixed_pct`).
