# oi_funding_breakout

## Thesis
Healthy trend breakouts in crypto perps should be supported by fresh capital
participation, not only by a thin liquidation impulse. The intended signal is:
price breaks a multi-day high/low on 1h bars, open interest expands into its
upper two-week tail, and funding is not already extremely crowded against the
trade.

## Data path
Open interest lives under `data/bybit/perp/open_interest/<SYMBOL>/<YYYY-MM>.parquet`
and is injected by `datafeed.loader.load_many()` as an optional
`open_interest` column. If that column is missing, the strategy falls back to
notional volume only so audits still run, but the intended research signal is
true OI.

## Baseline logic
- Universe: BTCUSDT and ETHUSDT.
- Decision TF: 1h.
- Entry channel: prior `breakout_days` high/low.
- Participation filter: current 1h OI delta is positive and above the trailing
  `oi_delta_quantile` percentile over roughly two weeks.
- Funding crowding proxy: do not go long when the trailing 8h return is already
  too hot; do not go short when it is already too washed out. This is a proxy
  until true funding values can be injected into `generate_signals`.
- Exit: OI falls below its moving average, opposite channel is hit, or ATR
  trailing stop is crossed.

## Hypothesis note for first iterate
1h Donchian trend continuation gated by upper-tail OI expansion; expect fewer
false breakouts and better OOS profit factor versus plain channel breakout.
