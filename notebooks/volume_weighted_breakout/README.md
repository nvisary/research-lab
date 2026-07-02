# notebooks/volume_weighted_breakout - manual strategy pipeline

Small notebook pipeline for inspecting `strategies/volume_weighted_breakout`
outside the auto-iteration loop.

The first notebook is intentionally a smoke run: load a pair of assets, run the
current strategy through the canonical harness, print the train/OOS metrics, and
render equity plus target positions. It is not a new hypothesis and does not
touch `best.json`, `history.jsonl`, or holdout.

`01_full_period_1000.ipynb` runs the standard visible 2-year research window
(`2024-01-01` -> `2026-01-01`) on the strategy defaults and rebases equity to a
`$1000` account for display, with drawdowns, monthly returns, positions, trades,
and the full metrics payload.

`02_full_universe_no_btc_1000.ipynb` repeats the same 2-year diagnostic run on
every locally available symbol except `BTCUSDT`, with extra exposure and
per-symbol trade breakdowns.

`03_champion_replay_1000.ipynb` replays the archived champion from
`strategies/volume_weighted_breakout/runs/best_strategy.py` and `best.json`
without editing the live strategy file.

`04_champion_slices.ipynb` dissects the champion trade ledger by symbol, side,
month, train/OOS slice, and entry context such as prior move, volume ratio, and
realized volatility.

`05_december_2025_deep_dive.ipynb` zooms into the champion's weakest visible
month, December 2025, with day clusters, symbol/side damage, equity drawdown,
and price context around entries and exits.

`06_december_guard_variants.ipynb` backtests three temporary champion variants
for December-like events: a short volatility cap, a tighter short exhaustion
guard, and a market-wide short cluster guard.

`07_short_cluster_guard_grid.ipynb` focuses on the preferred market-wide short
cluster guard and sweeps cluster size plus prior-24h drop thresholds to find a
robust protection/return compromise.

`08_current_champion_all_available_1000.ipynb` runs the promoted live strategy
on every symbol currently available on disk and rebases the portfolio to
`$1000`.

`09_dca_and_leverage_risk.ipynb` simulates `$1000` initial capital plus `$200`
monthly DCA on the promoted core champion, then scales the realized return
stream across leverage levels and reports drawdown/liquidation-risk proxies.

`10_correlation_vwb3x_vs_pump_dump.ipynb` compares the promoted VWB champion
at 3x leverage with the latest corrected `pump_dump_combined` notebook stream
from `15_2_kelly_walkforward`: rollover/catastrophe-stop events, WF entry
filter, realistic DCA/capacity/slippage engine, and walk-forward per-leg sizing.
It computes daily return correlation, rolling correlation, scatter/equity/
drawdown charts, and a daily-rebalanced portfolio weight sweep.

## Run

```bash
uv run jupyter lab
```

Headless execution:

```bash
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/volume_weighted_breakout/00_smoke_pair_run.ipynb
```

## Helper

```python
import sys; sys.path.insert(0, ".")   # cwd = notebooks/volume_weighted_breakout
from _lab import *

strategy.DEFAULT_SYMBOLS
run_pair(["BTCUSDT", "ETHUSDT"], "2024-01-01", "2024-07-01")
show("example")
```

Use `show("name")` instead of `plt.show()` so plots are both displayed inline
and saved under `_out/`.
