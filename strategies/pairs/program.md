# pairs — cointegration / mean-reversion pairs trading

## Origin

Logic ported from `C:\[win] projects\pairs_bot` (a live Bybit pairs-trading
bot, ~1400 LOC). Stripped of all live-trading infrastructure (ccxt, telegram,
WebSocket feed, async scheduler, position tracking, persistence) and
rewritten as a single pure `generate_signals` function. Mathematical core
preserved: log-price regression for β/α, ADF test for cointegration,
half-life filter, EWMA z-score on the spread, hysteresis entry/exit.

## Hypothesis (baseline)

> **Cointegrated cross-sections of the broad Bybit perp universe contain
> mean-reverting log-price spreads. Trading the top-N pairs by score with a
> Z>2 entry / Z<0.5 exit / Z>4 hard-stop, refit on a 30-day rolling window
> every 7 days, produces positive risk-adjusted return after 5.5 bps × 4
> legs cost.**

Default values mirror the conservative end of pairs_bot's production config
(MIN_CORR=0.65, Z_ENTRY=2.0, Z_EXIT=0.5 — pairs_bot used 0.8, we tighten
slightly), with rebalanced timeframe (1h instead of pairs_bot's 5m trade /
1h scan, since costs eat 5m on a 24-month synthetic backtest).

## Universe & timeframe

- **DEFAULT_SYMBOLS** = 173 currently-listed Bybit USDT-perps with 1m data
  on disk (full breadth from `data/bybit/perp/1m/`).
- **DEFAULT_TF** = `"1h"`.

### Survivorship caveat (per AGENTS.md §10d)

The universe is **currently-listed only** — pairs that broke and got
delisted are absent by construction. This biases pair-trading metrics
upward:
1. The pair-fit step picks the best-cointegrated pair on a *survivor*
   universe. Real trading would have included pairs that later collapsed.
2. The "edge" measured here is partly an illusion of post-hoc selection.
3. Discount any OOS Sharpe by ~30%+ when generalising forward.

This is the most significant caveat for the strategy. Treat the baseline
result as a sanity check on the porting itself, not a deployable edge
estimate. Only the holdout tells us anything close to the truth, and even
that is survivor-biased.

## Sizing semantics

`RAW_SIZING = True`, `MAX_POSITION = 1.0`. Each pair gets `leg_size /
n_active_pairs` of total equity per leg. With `top_n_pairs=5` and
`leg_size=0.5`, gross per leg ~5%, total gross 50% (10 legs across 5
pairs, ±β-scaled). vectorbt `cash_sharing=True` caps total at 100% of
equity — so even if multiple pairs push, gross cannot exceed 100%.

## Fixes applied vs. pairs_bot at port time

| pairs_bot location | Issue | Fix in port |
|---|---|---|
| `pairs.py:239–247` (`estimate_half_life`) | `phi ≤ 0` returned `inf`, rejecting fast / oscillating mean-reversion | `phi ≥ 1` → `inf`; `phi ≤ 0` → 0.5 (treated as fast MR) |
| `pairs.py:273–282` (`_beta_stability`) | Hardcoded 0.7 ratio threshold | Exposed as `beta_stab_max` param |
| Multiple magic numbers (β bounds, MIN_RETURNS_CORR, SCAN_SIGMA_MIN, Z thresholds, EWMA span, HL bounds) | All hardcoded | All exposed in `DEFAULT_PARAMS` / `PARAM_SPACE` |
| ADF p-value filter (statsmodels.adfuller) | `statsmodels` was not in harness deps initially | User authorized adding `statsmodels>=0.14` to `pyproject.toml`; ADF restored with `adf_pmax` param (default 0.05) per pairs_bot's original logic. |

## What was NOT ported (deliberate; future iteration candidates)

- **Kalman β/α refit** (`kalman.py`) — adaptive β tracking via Kalman filter.
  Could replace the periodic OLS refit with a per-bar state estimator.
- **KPSS confirmation filter** (off in pairs_bot by default).
- **Hurst R/S filter** (untested in pairs_bot, default off).
- **BH FDR correction** across pair candidates (`pairs.py:526`). Important
  for multiple-testing honesty, especially given how many candidate pairs
  are scanned per refresh.
- **Vol-targeted leg sizing** (`leg_a_notional = RISK_USD / σ_spread`).
- **Multi-timeframe gating** (1h trigger + 4h regime confirm).
- **Cost-aware skip**: don't trade pairs where `z_entry · σ < commission`.
- **Drop-and-replace mid-trade**: a pair that loses cointegration in the
  middle of an active trade should be force-closed rather than waiting
  for z-exit or hard-stop.

## Iterations

| iter | verdict | composite | OOS Sharpe | DSR | n_trades | hypothesis | notes |
|------|---------|-----------|------------|-----|----------|------------|-------|
| _baseline run pending_ | | | | | | | |
