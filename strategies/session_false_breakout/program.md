# Session High/Low False Breakout

## Hypothesis
Asian-session highs and lows often act as liquidity pools. When London opens,
price may briefly sweep the Asian range edge and then close back inside the
range; fading that sweep should capture mean reversion toward the Asian range
midpoint or opposite edge.

## Baseline Rules
- Universe: BTCUSDT, ETHUSDT, SOLUSDT.
- Decision timeframe: 5 minutes.
- Asian range: 00:00-08:00 UTC for each calendar day.
- Entry window: first two hours of the European session, 08:00-10:00 UTC.
- Short entry: bar high sweeps above Asian high and bar close finishes back
  below the Asian high.
- Long entry: bar low sweeps below Asian low and bar close finishes back above
  the Asian low.
- Exit: take profit at Asian range midpoint by default.
- Stop: close beyond the swept level plus a small range-based buffer.
- Positions are shifted one bar before emission to avoid lookahead.

## Baseline Note
This initial version intentionally keeps the idea simple. It tests whether
the raw London false-breakout pattern has enough edge before adding filters
such as volatility regime, trend context, volume confirmation, or target choice.
