# Adaptive Volatility Channel Expansion

Baseline hypothesis: crypto perps periodically transition from low-volatility
compression into directional volatility expansion. A Bollinger-width squeeze
marks the compressed state; a close through the prior channel boundary arms a
directional breakout. The position is held until price loses the opposite
Keltner boundary or the channel reaches extreme expansion.

Initial baseline:
- Universe: BTCUSDT, ETHUSDT, SOLUSDT.
- Timeframe: 1h.
- Entry: Bollinger Band width in the bottom historical percentile, followed by
  a close above the prior upper band or below the prior lower band.
- Exit: opposite Keltner boundary or extreme Bollinger width expansion.
