# EMA Pilot — agent instructions

## Hypothesis
Trend-following EMA crossover should capture sustained moves on BTC perp futures.
It is intentionally weak; the goal is to validate the research loop, not to ship alpha.

## What you may change in `strategy.py`
- `DEFAULT_PARAMS` values
- `PARAM_SPACE` ranges (if you discover a useful new dimension)
- The body of `generate_signals` — indicators, filters, position sizing within [-1, 1]
- `DEFAULT_SYMBOLS` (start with BTCUSDT only; add more only if you have a reason)

## What you must NOT change
- The function signature `generate_signals(data, params) -> DataFrame[timestamp, symbol, position]`
- The presence of `DEFAULT_PARAMS` and `DEFAULT_SYMBOLS`
- Lookahead bias: always `.shift(1)` before emitting positions

## Constraints
- `position` ∈ [-1, 1]
- No external data sources — only what's in `data`
- Keep the file under 200 lines; clarity over cleverness
- Indicators on minute bars only with caution — too noisy. Prefer 15m–4h logic.

## Evaluation
Harness scores `composite = OOS_Sharpe − 0.5·MaxDD` with a flat -0.5 if `n_trades < 50`.
Aim to keep trade count meaningful and OOS Sharpe positive on BTCUSDT 2025.

## Forbidden tricks
- Loading future data
- Hardcoding parameters that overfit to specific calendar dates
- Using `data` to peek beyond the current bar's index
