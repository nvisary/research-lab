# fractal_ao_breakout — research log

## Slot
Per-asset trend-following (TS). Baseline strategy.
Same quadrant as `trend_supertrend`, `keltner`, `donchian_breakout`, `macd_ema200`.

## Baseline thesis
Williams Fractal breakout with Awesome Oscillator (AO) momentum confirmation.
- **Williams Fractal (5-bar)**: The highest high or lowest low of a 5-bar sequence. An up fractal is formed when the high at `t-2` is greater than the highs at `t-4, t-3, t-1, t`.
- **Awesome Oscillator (AO)**: `SMA(MedianPrice, 5) - SMA(MedianPrice, 34)`. It measures the market momentum.
- **Long signal**: `close > last_up_fractal` AND `AO > 0`.
- **Short signal**: `close < last_down_fractal` AND `AO < 0`.

Fractals identify key support and resistance levels. A breakout of these levels suggests a new trend direction, while the AO ensures we are entering with momentum in the same direction.

## Caveats noted up-front
- **Fractal Lag**: Fractals at `t-2` are only confirmed at `t`. This 2-bar lag is structural.
- **Chop risk**: Breakout strategies are prone to whipsaws in ranging markets.
- **Asymmetric drift**: Crypto has a natural long-bias; symmetric shorts might need tighter filters or be disabled.

## Planned iteration directions
1. Establish baseline at default params (4h TF, 5/34 AO).
2. AO parameter sweep (e.g., 5/34 vs 10/70).
3. Fractal window sweep (though 5 is standard).
4. Long-only vs symmetric.
5. Vol-targeted sizing.
6. Multi-TF confirmation (e.g., gate by 1d EMA).
7. Extreme-vol gate (skip entries during flash crashes).

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | TotalRet | Note |
|---|---------|-----------|------------|-------|----------|------|----------|------|
| 1 | **KEEP** | -3.730 | -1.528 | 12.1% | 20 | 20.5% | -3.2% | **BASELINE**: 4h BTCUSDT, AO(5,34), 5-bar Fractal, symmetric. Works in bull regimes, fails in flat/bear. |
| 2 | **KEEP** | -3.032 | -0.635 | 6.7% | 13 | 14.0% | -0.6% | **ADX FILTER**: Implement ADX(14) > 20. Improved Sharpe and DD significantly, but reduced trade count. Still fails in flat regimes (Sharpe < -5). |
| 3 | **KEEP** | -2.618 | -0.184 | 6.0% | 8 | 7.8% | 0.0% | **ADX TUNING**: Tune adx_min to 25. Further improved Sharpe and DD. Trading is now very selective. |
| 4 | **KEEP** | -2.360 | -0.164 | 6.2% | 9 | 9.0% | -0.1% | **AO SWEEP**: AO(5, 21) for faster reaction. Improved composite and Sharpe. |
| 5 | REVERT | -2.581 | -0.019 | 5.8% | 7 | 7.6% | 0.3% | AO(8, 34) - slower confirmation hurt performance. |
| 6 | REVERT | -2.800 | -0.597 | 6.2% | 5 | 4.9% | -0.5% | Extreme-vol gate (q=0.8) reduced activity too much. |
| 7 | **KEEP** | -2.237 | +0.201 | 5.8% | 7 | 7.6% | 0.4% | **HTF GATE**: Added 1d EMA50 trend gate. First positive OOS Sharpe. |
| 8 | REVERT | -2.271 | +0.186 | 5.8% | 7 | 7.5% | 0.4% | AO(8, 21) - slower fast window marginally worse than (5, 21). |
| 9 | **KEEP** | -1.536 | **+1.276** | **3.8%** | 2 | 2.9% | +0.1% | **LONG ONLY**: Switched to long-only. Sharpe boosted, DD reduced. Very selective (TiP 2.9%). |
| 10 | REVERT | -2.586 | -0.451 | 3.7% | 5 | 5.7% | -0.5% | Relax ADX filter (20) to increase activity - backfired, performance dropped. |
| 11 | REVERT | -1.555 | +1.254 | 3.8% | 2 | 2.8% | +0.1% | AO(5, 34) - standard slow window marginally worse than (5, 21). |

## Final summary (11 iterations)

**Champion — iter 9:**
- Composite: **-1.536**
- OOS Sharpe: **+1.276**
- OOS MaxDD: **3.8%**
- OOS Total return: **+0.1%**
- Setup: BTCUSDT 4h, AO(5, 21) + Williams Fractal breakout + ADX(14)>25 + 1d EMA50 trend gate, Long-only.

**Trajectory.** Baseline (-3.73 composite) -> ADX Filtered (-2.62) -> HTF Gated (-2.24) -> Long-only champion (-1.54). Total composite gain: **+2.2**.

**What's actually working:**
1. **ADX Filter (min 25)**: Crucial for avoiding chop.
2. **HTF Trend Gate (1d EMA50)**: Correctly aligns entries with the dominant trend.
3. **Long-Only**: Exploits the structural long-bias in crypto.
4. **Faster AO (5, 21)**: Provides more timely momentum confirmation.

**Caveats:**
- Strategy is now **extremely selective** (Time-in-position < 3%). High Sharpe may be a "cash-heavy" artifact.
- Total return is very low; needs a broader universe or higher volatility to be meaningful.

**What's been ruled out:**
- Symmetric shorts: structurally bleeds in this setup.
- Extreme-vol gate: over-filters valid entries.
- AO(8, 34) and AO(8, 21): slower confirmations are laggier and worse.

