# funding_arbitrage

## Thesis

Baseline cash-and-carry funding arbitrage: when a perp's funding yield is high,
hold the spot asset and short the perp 1:1 so the delta is neutral and the
portfolio harvests positive funding. Exit when the funding yield normalizes.

## Harness model

The harness models the perp leg directly and, when `SPOT_HEDGE = True`, adds a
synthetic 1:1 spot hedge priced from the same close series:

- spot notional is `-perp_notional`;
- spot price PnL offsets directional perp price PnL;
- perp funding remains in equity;
- spot taker fee and flat spot slippage are charged on hedge rebalances.

Remaining limitation: there is still no independent spot OHLCV feed, so
spot/perp basis is assumed to be zero. A true basis-arb backtest needs a real
spot close series and basis convergence accounting.

## Baseline rules

- Timeframe: 1h monitoring.
- Universe: liquid Bybit USDT perps already used in funding research.
- Entry: short perp when annualized funding exceeds 30%.
- Exit: flat when annualized funding falls below 10%.
- Sizing: raw portfolio weights, equal budget per active symbol, total gross
  exposure capped at 100%.
- Lookahead control: funding is forward-filled to the 1h grid and the emitted
  position is shifted by one decision bar.
