# KAMA Trend-Following Strategy

## Overview
This strategy uses Kaufman's Adaptive Moving Average (KAMA) to capture trends while minimizing whipsaws during sideways markets. It combines KAMA with an Efficiency Ratio (ER) filter and a higher-timeframe Momentum gate (CCI).

## Core Logic
- **Primary Trend**: KAMA(n=10, fast=2, slow=30). KAMA adjusts its smoothing speed based on market efficiency.
- **Trend Filter**: Efficiency Ratio (ER) must be rising, indicating increasing trend strength.
- **Momentum Gate**: CCI(20) on a 15-minute timeframe (aggregated from 1-minute bars) must be > 100 for longs (or < -100 for shorts if enabled).
- **Timeframe**: 1-minute bars.

## Entries and Exits
- **Long Entry**: Price closes above KAMA AND ER is rising AND 15m CCI > 100.
- **Long Exit**: Price closes below KAMA.
- **Short Entry**: Price closes below KAMA AND ER is rising AND 15m CCI < -100 (optional).
- **Short Exit**: Price closes above KAMA.

## Parameters
- `kama_n`: Period for Efficiency Ratio calculation (default 10).
- `kama_fast`: Fastest SC span (default 2).
- `kama_slow`: Slowest SC span (default 30).
- `cci_period`: Period for CCI (default 20).
- `cci_tf`: Timeframe for CCI calculation (default "15min").
- `vol_target`: Target volatility for sizing (default 0.01 or 1%).
