"""Combinatorial Purged CV diagnostic.

Runs ``C(n_groups, k_test)`` test paths over the train+val period and
reports the distribution of OOS Sharpe / DD / total-return. Diagnostic
only — does NOT update best.json. Intended for spot-checks before
committing to a candidate or before reading the holdout.

The default (n_groups=10, k_test=2) yields 45 paths. Costs roughly the
same as one walk-forward backtest (the strategy is fixed code, so we
run it once and re-aggregate metrics on different OOS subsets).

Usage:
    uv run python -m runner.cpcv strategies/<name>
    uv run python -m runner.cpcv strategies/<name> --n-groups 8 --k-test 2 --embargo 1D

Output:
    runs/cpcv/cpcv_<UTC>_iter_<best_iter>.json   (summary + per-path table)
    runs/cpcv/cpcv_<UTC>_iter_<best_iter>.parquet (per-path table only)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from harness import backtest as bt
from harness import env as env_mod
from harness.costs import CostModel
from harness.cpcv import cpcv_paths, evaluate_path, summarize_paths
from harness.splits import Split


def run_cpcv(strategy_dir: Path, period_start: str, period_end: str,
             n_groups: int = 10, k_test: int = 2,
             embargo: str | None = "1D",
             cost_model: str = "static",
             tf: str | None = None,
             lookback: str | None = "60D") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    runs = strategy_dir / "runs"
    runs.mkdir(exist_ok=True)
    cpcv_dir = runs / "cpcv"
    cpcv_dir.mkdir(exist_ok=True)

    best_path = runs / "best.json"
    best = json.loads(best_path.read_text(encoding="utf-8")) if best_path.exists() else None
    iter_id = (best or {}).get("iter", 0)

    mod = bt.load_strategy(strategy_dir)
    params = dict(getattr(mod, "DEFAULT_PARAMS", {}))
    symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")

    cost_kwargs = {
        "static": {},
        "spread": {"use_dynamic_spread": True},
        "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
    }[cost_model]
    costs = CostModel(**cost_kwargs)

    # ---- One full-period backtest ----
    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    # Degenerate Split: empty train, full OOS — gives full-period equity
    # in return_curves with no train metrics waste.
    split = Split(train_start=s, train_end=s, oos_start=s, oos_end=e)
    out = bt.run_split(mod, params, symbols, split, tf=tf, costs=costs,
                       return_curves=True, lookback=lookback)
    if "equity" not in out:
        raise RuntimeError("backtest returned no equity curve; check data availability")

    equity = out["equity"]
    returns = out.get("oos_returns")
    if returns is None or returns.empty:
        returns = equity.pct_change().fillna(0.0)
    trades = out.get("trades")

    # ---- Generate paths and evaluate each ----
    paths = cpcv_paths(period_start, period_end,
                       n_groups=n_groups, k_test=k_test, embargo=embargo)
    print(f"[cpcv] running {len(paths)} paths "
          f"(n_groups={n_groups}, k_test={k_test}, embargo={embargo})...",
          flush=True)
    path_results = []
    for i, path in enumerate(paths):
        r = evaluate_path(returns, equity, trades, path, tf=tf)
        path_results.append(r)
    summary = summarize_paths(path_results)

    # ---- Persist ----
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out_base = cpcv_dir / f"cpcv_{stamp}_iter_{iter_id:04d}"

    report = {
        "iter": iter_id,
        "ran_at": started.isoformat(),
        "period": [period_start, period_end],
        "tf": tf,
        "symbols": symbols,
        "params": params,
        "cost_model": cost_model,
        "n_groups": n_groups,
        "k_test": k_test,
        "n_paths": len(paths),
        "embargo": embargo,
        "summary": summary,
        "best_composite_train_val": (best or {}).get("composite"),
        "env": env_mod.capture(),
    }
    out_base.with_suffix(".json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    # Per-path table (richer than summary, suitable for plotting).
    rows = []
    for r in path_results:
        rows.append({
            "test_groups": ",".join(map(str, r["test_groups"])),
            "n_periods": r["n_periods"],
            "n_trades": r["n_trades"],
            "sharpe": r["sharpe"],
            "sortino": r["sortino"],
            "max_dd": r["max_dd"],
            "total_return": r["total_return"],
            "hit_rate": r["hit_rate"],
        })
    pd.DataFrame(rows).to_parquet(
        out_base.with_suffix(".parquet"), compression="zstd", index=False
    )

    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--tf", default=None,
                    help="If omitted, read strategy.py:DEFAULT_TF, fall back to '1h'.")
    ap.add_argument("--n-groups", type=int, default=10,
                    help="Number of equal-time groups to split the period into.")
    ap.add_argument("--k-test", type=int, default=2,
                    help="Number of test groups per path. n_paths = C(n_groups, k_test).")
    ap.add_argument("--embargo", default="1D",
                    help="Forward embargo after each test group, parseable as "
                         "pd.Timedelta. Default 1D. Set '0' to disable.")
    ap.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static")
    ap.add_argument("--lookback", default="60D",
                    help="Pre-load history before period_start so rolling "
                         "indicators are warm at bar 1 of the CPCV window. "
                         "Default: 60D.")
    args = ap.parse_args()

    rep = run_cpcv(Path(args.strategy_dir), args.start, args.end,
                   n_groups=args.n_groups, k_test=args.k_test,
                   embargo=args.embargo, cost_model=args.cost_model, tf=args.tf,
                   lookback=args.lookback)
    print(json.dumps({
        "iter": rep["iter"],
        "n_paths": rep["n_paths"],
        "median_sharpe": round(rep["summary"]["median_sharpe"], 4),
        "mean_sharpe": round(rep["summary"]["mean_sharpe"], 4),
        "iqr_sharpe": [round(x, 4) for x in rep["summary"]["iqr_sharpe"]],
        "pct_positive_sharpe": round(rep["summary"]["pct_positive_sharpe"], 1),
        "pct_above_1": round(rep["summary"]["pct_above_1"], 1),
        "worst_max_dd": round(rep["summary"]["worst_max_dd"], 4),
        "best_composite_train_val": (round(rep["best_composite_train_val"], 4)
                                     if rep["best_composite_train_val"] is not None else None),
    }, indent=2))


if __name__ == "__main__":
    main()
