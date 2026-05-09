"""Multi-strategy portfolio runner — Path B (separate accounts, sum equity).

Each component strategy runs independently with its own $X capital
allocation. Equity curves are scaled by capital and summed into one
portfolio equity. Per-strategy metrics, correlation matrix, and
combined-portfolio metrics are reported.

Path B architecture choices:
  - **Separate accounts**: each strategy uses init_cash = 10_000 internally
    (vectorbt default); equity is scaled by capital / 10_000 before
    aggregation.
  - **No shared cash pool**: each strategy's costs computed independently.
    Realistic for a multi-strat fund where sub-strategies run on separate
    sub-accounts (or different exchanges).
  - **Daily resampling for cross-strategy comparison**: per-strategy equity
    is resampled to daily so different-TF strategies can be combined and
    correlated on the same time grid. Combined metrics computed on daily
    returns (annualization factor 365.25).
  - **Benchmark composition**: each strategy's equal-weight buy-and-hold
    of its own universe, scaled by its capital, summed. Answers
    "what if I just bought-and-held each strategy's universe at the
    same allocations?".

This is NOT meant to model:
  - Shared cash with one common margin account (use Path C / unified
    positions for that).
  - Auto-allocation / regime-conditional weights (operator decides).
  - Capital reallocation across time (allocations are static).

Usage:
    uv run python -m runner.portfolio \\
        --strategy xs_momentum:7000 --strategy supertrend:3000 \\
        --start 2024-01-01 --end 2026-01-01
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from harness import backtest as bt
from harness import env as env_mod
from harness import metrics as M
from harness.costs import CostModel
from harness.splits import Split


@dataclass
class PortfolioComponent:
    """One sub-strategy in the portfolio."""
    strategy: str         # directory name under strategies/
    capital: float        # $ allocated to this sub-strategy
    tf: str | None = None  # override DEFAULT_TF if needed (else read from module)


def _run_one_strategy(component: PortfolioComponent,
                      period_start: str, period_end: str,
                      embargo, lookback, costs) -> dict:
    """Backtest one strategy on a degenerate single-window split covering
    [period_start, period_end). Mirrors what runner.holdout does — single
    continuous OOS slice, no train/test split inside.
    """
    strategy_dir = Path("strategies") / component.strategy
    mod = bt.load_strategy(strategy_dir)
    params = dict(getattr(mod, "DEFAULT_PARAMS", {}))
    symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])
    tf = component.tf or getattr(mod, "DEFAULT_TF", "1h")

    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    split = Split(train_start=s, train_end=s, oos_start=s, oos_end=e)

    return bt.run_split(mod, params, symbols, split, tf=tf,
                        costs=costs, return_curves=True,
                        lookback=lookback)


def run_portfolio(components: list[PortfolioComponent],
                  period_start: str = "2024-01-01",
                  period_end: str = "2026-01-01",
                  embargo: str | None = "1D",
                  lookback: str | None = "60D",
                  cost_model: str = "static") -> dict:
    """Run each component, sum capital-scaled equity, compute portfolio metrics."""
    cost_kwargs = {
        "static": {},
        "spread": {"use_dynamic_spread": True},
        "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
    }[cost_model]
    costs = CostModel(**cost_kwargs)

    per_strategy: dict[str, dict] = {}
    eq_series: dict[str, pd.Series] = {}
    bench_series: dict[str, pd.Series] = {}

    for c in components:
        print(f"[portfolio] running {c.strategy} (capital ${c.capital:,.0f})...", flush=True)
        out = _run_one_strategy(c, period_start, period_end, embargo, lookback, costs)
        if "equity" not in out or out["equity"].empty:
            print(f"[portfolio] WARNING: {c.strategy} returned no equity — skipping",
                  flush=True)
            continue
        # Scale internal $10k init_cash to user's allocation. Mathematically
        # equivalent to running with init_cash=capital because the strategy's
        # signals are dimensionless (positions in [-1, 1]).
        scale = c.capital / 10_000.0
        eq = out["equity"] * scale
        bench = out["benchmark"] * scale
        eq_series[c.strategy] = eq
        bench_series[c.strategy] = bench
        oos_metrics = out.get("oos", {})
        per_strategy[c.strategy] = {
            "capital": c.capital,
            "tf": (out.get("trades")["entry_time"].dt.normalize().nunique()
                   if isinstance(out.get("trades"), pd.DataFrame) and not out["trades"].empty
                   else None),
            "metrics": {k: v for k, v in oos_metrics.items()
                        if not isinstance(v, (pd.Series, pd.DataFrame))},
            "final_equity": float(eq.iloc[-1]),
            "pnl_dollar": float(eq.iloc[-1] - c.capital),
            "pnl_pct": float(eq.iloc[-1] / c.capital - 1.0),
        }

    if not eq_series:
        return {"error": "no strategies returned valid equity"}

    # Resample each strategy to DAILY before aggregation. Different
    # strategies may have different TFs; daily is the natural common
    # ground for cross-strategy comparison.
    eq_daily: dict[str, pd.Series] = {}
    bench_daily: dict[str, pd.Series] = {}
    for name, eq in eq_series.items():
        eq_daily[name] = eq.resample("1D").last().ffill()
    for name, b in bench_series.items():
        bench_daily[name] = b.resample("1D").last().ffill()

    # Outer-join all on daily index. Forward-fill so a strategy starting
    # later doesn't drop the earlier ones from the sum.
    eq_df = pd.DataFrame(eq_daily).ffill()
    eq_df = eq_df.bfill()  # fill leading NaN with first valid (capital amount)
    bench_df = pd.DataFrame(bench_daily).ffill().bfill()

    # Combined equity = sum of capital-scaled per-strategy equity.
    combined_equity = eq_df.sum(axis=1)
    combined_bench = bench_df.sum(axis=1)
    combined_returns = combined_equity.pct_change().fillna(0.0)

    # Correlation matrix on daily returns.
    returns_df = eq_df.pct_change().fillna(0.0)
    corr = returns_df.corr().round(4)

    # Portfolio metrics — daily annualization (factor 365.25).
    total_n_trades = sum(
        int(p["metrics"].get("n_trades", 0) or 0)
        for p in per_strategy.values()
    )
    portfolio_metrics = M.summary(
        combined_equity, combined_returns,
        positions=pd.DataFrame(0.0, index=combined_equity.index, columns=["_"]),
        n_trades=total_n_trades,
        tf="1d",
        benchmark=combined_bench,
    )

    total_capital = sum(c.capital for c in components)
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "components": [asdict(c) for c in components],
        "period": [period_start, period_end],
        "embargo": embargo,
        "lookback": lookback,
        "cost_model": cost_model,
        "total_capital": total_capital,
        "final_equity": float(combined_equity.iloc[-1]),
        "total_pnl_dollar": float(combined_equity.iloc[-1] - total_capital),
        "total_pnl_pct": float(combined_equity.iloc[-1] / total_capital - 1.0),
        "combined_curve": {
            "timestamp": [t.isoformat() for t in combined_equity.index],
            "equity": combined_equity.tolist(),
            "benchmark": combined_bench.tolist(),
        },
        "per_strategy_curves": {
            name: {
                "timestamp": [t.isoformat() for t in eq.index],
                "equity": eq.tolist(),
                "benchmark": bench_daily[name].reindex(eq.index).tolist(),
            }
            for name, eq in eq_daily.items()
        },
        "per_strategy": per_strategy,
        "portfolio_metrics": portfolio_metrics,
        "correlation_matrix": {
            row: {col: (None if pd.isna(corr.loc[row, col]) else float(corr.loc[row, col]))
                  for col in corr.columns}
            for row in corr.index
        },
        "env": env_mod.capture(),
    }


def _parse_strategy_arg(s: str) -> PortfolioComponent:
    """Parse 'name:capital' or 'name:capital:tf'."""
    parts = s.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            f"--strategy expects 'name:capital' or 'name:capital:tf', got {s!r}"
        )
    name = parts[0]
    capital = float(parts[1])
    tf = parts[2] if len(parts) >= 3 else None
    return PortfolioComponent(strategy=name, capital=capital, tf=tf)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", action="append", required=True,
                    type=_parse_strategy_arg,
                    help="Sub-strategy spec 'name:capital' or 'name:capital:tf'. "
                         "Repeat for each component, e.g. "
                         "--strategy xs_momentum:7000 --strategy supertrend:3000")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--embargo", default="1D")
    ap.add_argument("--lookback", default="60D")
    ap.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static")
    ap.add_argument("--out", default=None,
                    help="Optional JSON output path")
    args = ap.parse_args()

    rep = run_portfolio(
        components=args.strategy,
        period_start=args.start,
        period_end=args.end,
        embargo=args.embargo,
        lookback=args.lookback,
        cost_model=args.cost_model,
    )

    summary = {
        "period": rep["period"],
        "components": [{"strategy": c["strategy"], "capital": c["capital"]}
                       for c in rep["components"]],
        "total_capital": rep["total_capital"],
        "final_equity": round(rep["final_equity"], 2),
        "total_pnl_dollar": round(rep["total_pnl_dollar"], 2),
        "total_pnl_pct": round(rep["total_pnl_pct"] * 100, 2),
        "portfolio_sharpe": round(rep["portfolio_metrics"]["sharpe"], 3),
        "portfolio_max_dd": round(rep["portfolio_metrics"]["max_dd"], 4),
        "alpha_vs_benchmark": (round(rep["portfolio_metrics"]["alpha_sharpe"], 3)
                                if rep["portfolio_metrics"].get("alpha_sharpe") is not None
                                else None),
        "correlation_matrix": rep["correlation_matrix"],
    }
    print(json.dumps(summary, indent=2, default=str))

    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2, default=str),
                                  encoding="utf-8")
        print(f"\nFull report → {args.out}")


if __name__ == "__main__":
    main()
