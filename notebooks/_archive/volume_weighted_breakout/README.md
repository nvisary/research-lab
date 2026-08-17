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
drawdown charts, and a daily-rebalanced portfolio weight sweep. **SUPERSEDED by
nb11 — nb10 read `pump_rollover_visible.parquet` / `dump_rollover_visible.parquet`,
which do not exist on disk, so its pump/dump leg was not the real corrected book.**

`11_portfolio_vwb3x_pump_dump.ipynb` — **corrected replacement for nb10.** Rebuilds
the pump_dump leg from the canonical full caches (`pump_rollover.parquet` /
`dump_rollover.parquet`, as `15_2`) — WF classifier entry filter + catastrophe
stop + capacity/slippage engine run as a clean lump-sum $1000 (no DCA cashflow),
per-leg sizing **pump 8% / dump 3%**; VWB champion at **3x**. Visible window
2024-01-01→2026-01-01, PD equity clipped to it (holdout untouched). **RESULT: the
two lines are essentially UNCORRELATED — Pearson −0.05, Spearman +0.00** (VWB = 4h
breakout on 4 majors, PD = 1m mean-reversion on ~170 microcaps: different assets,
horizons, regimes). Standalone: PD Sharpe 3.64/CAGR 421%/DD −22.4% (in-sample
small-cap ceiling), VWB 3x Sharpe 1.90/CAGR 168%/DD −24.2%. **Blending is a
free lunch: Sharpe peaks at 30% VWB / 70% PD (4.19 > either leg), min drawdown at
~50/50 (−14.5%, below both), best Calmar 45% VWB (21.0); CAGR falls monotonically
with VWB weight (PD is the higher-return leg).** HONEST: both legs in-sample over
one 2024→2026 window (no true alt-mania); PD worst trade still −37% (gap tail);
pump8/dump3 = aggressive growth tilt (more money, worse DD than flat-5% bot); VWB
3x leverage is an arbitrary risk dial (at 1x the optimal weight shifts). Robust
takeaway = the near-zero correlation and the diversification it enables, not the
headline Sharpe. Charts: `_out/port_11_corr.png`, `port_11_mix.png`, `port_11_frontier.png`.

`12_combined_dca_1000_plus_200.ipynb` — **the two strategies run together as one DCA
account: $1000 initial + $200/mo, full available data 2024-01→2026-04** (SOL delists
2025-07). Reuses nb11's two daily-return streams (VWB 3x, PD pump8/dump3), blends at a
fixed target weight (daily-rebalanced), and DCAs into the blend with NAV/unit accounting
(monthly cash buys units at current NAV — no fake return jumps). Risk (Sharpe/Sortino/DD)
on per-unit NAV; dollars & IRR money-weighted. **Contributed $6,400 →** 0% VWB (PD-only)
$75k/11.7×/IRR 333%/Sh 3.49; **30% VWB / 70% PD $45k/7.1×/IRR 233%/Sh 3.67/Sortino 4.73/
maxDD(NAV) −20.3% (best risk-adjusted, 24 green / 3 red months)**; 50/50 $31k/4.8×/Sh 3.11/
DD −27%; 100% VWB $9.7k/1.5×/Sh 1.19/DD −59%. **The extra 2026 tail (beyond nb11's cutoff)
is where VWB-3x-alone craters to −60% NAV DD while the blends stay shallow — the
diversification story holds up out past the correlation window.** ~30% VWB is the
Sharpe/DD sweet spot; more VWB trades growth for little extra smoothing. Same in-sample
ceilings apply (worst PD trade −37%, 3x arbitrary, capacity binds as account grows, daily
rebalance idealized). Charts: `_out/dca_12_account.png`, `dca_12_monthly.png`.

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
