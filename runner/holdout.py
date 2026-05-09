"""Read-only holdout sanity check.

The iteration loop (`runner.iterate`) optimizes against the train + OOS panel.
The holdout window is the truly out-of-sample slice that NEVER feeds back into
keep/revert decisions — the model has no way to "learn" it because the agent
can't condition on its score during iteration.

Use this script when you (the human) want to sanity-check the current best.json
against unseen data. Output is written to runs/holdout/holdout_iter_<N>.{json,parquet}
where N is the iter number recorded in best.json (so re-running before iterating
again does not pile up duplicate reports).

Usage:
    uv run python -m runner.holdout strategies/<name>
    uv run python -m runner.holdout strategies/<name> --start 2025-10-01 --end 2026-01-01
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from harness import backtest as bt
from harness import env as env_mod
from harness import lookahead as la
from harness import metrics as M
from harness.splits import Split
from datafeed.loader import load_many


DEFAULT_HOLDOUT_START = "2026-01-01"
DEFAULT_HOLDOUT_END = "2026-05-01"   # 4-month holdout: 2026-Q1 + Apr.
# Train+val now spans 2024 + 2025 (24 months) — covers both 2024 bull
# rally AND 2025 cycle peak + Q4 flash crashes, so strategies see a
# wider regime mix during fitting. Holdout is shorter as a result;
# accept the smaller sample size on the final exam in exchange for
# regime-diverse training.


def run_holdout(strategy_dir: Path, start: str, end: str, tf: str | None = None,
                audit: bool = True, lookback: str | None = "60D") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    runs = strategy_dir / "runs"
    runs.mkdir(exist_ok=True)
    holdout_dir = runs / "holdout"
    holdout_dir.mkdir(exist_ok=True)

    best_path = runs / "best.json"
    best = json.loads(best_path.read_text(encoding="utf-8")) if best_path.exists() else None
    iter_id = (best or {}).get("iter", 0)

    mod = bt.load_strategy(strategy_dir)
    params = dict(getattr(mod, "DEFAULT_PARAMS", {}))
    symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")

    # Lookahead audit pre-flight (cheap on its own; runs even when iterate
    # already audited because the human may have edited strategy.py since).
    audit_summary: dict | None = None
    if audit:
        audit_symbols = symbols[:2]
        audit_data = load_many(audit_symbols,
                               pd.Timestamp(start, tz="UTC"),
                               pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=120),
                               tf=tf)
        audit_data = {s: df for s, df in audit_data.items() if not df.empty}
        if audit_data:
            try:
                report = la.audit(mod, audit_data, params, k=12)
                audit_summary = {"audit": "passed", "k": report.k_perturbations,
                                 "duration_seconds": report.duration_seconds}
            except (la.LookaheadError, la.DeterminismError) as e:
                msg = str(e)
                bad = {
                    "iter": iter_id,
                    "ran_at": datetime.now(timezone.utc).isoformat(),
                    "period": [start, end],
                    "tf": tf,
                    "symbols": symbols,
                    "params": params,
                    "metrics": {},
                    "composite": None,
                    "best_composite_train_val": (best or {}).get("composite"),
                    "audit": {"audit": "FAILED", "error_type": type(e).__name__,
                              "mode": getattr(e, "mode", None), "message": msg},
                    "error": msg,
                }
                (holdout_dir / f"holdout_iter_{iter_id:04d}.json").write_text(
                    json.dumps(bad, indent=2, default=str), encoding="utf-8"
                )
                return bad

    # Holdout is a single window. Use a degenerate Split (no train) to reuse run_split.
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    split = Split(train_start=s, train_end=s, oos_start=s, oos_end=e)

    out = bt.run_split(mod, params, symbols, split, tf=tf,
                       return_curves=True, lookback=lookback)
    metrics = out.get("oos", {})
    composite = M.composite_score(metrics)

    started = datetime.now(timezone.utc).isoformat()
    report = {
        "iter": iter_id,
        "ran_at": started,
        "period": [start, end],
        "tf": tf,
        "symbols": symbols,
        "params": params,
        "metrics": metrics,
        "composite": composite,
        "best_composite_train_val": (best or {}).get("composite"),
        "audit": audit_summary,
        "env": env_mod.capture(),
    }

    (holdout_dir / f"holdout_iter_{iter_id:04d}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    if "equity" in out:
        df_curve = pd.DataFrame({
            "timestamp": out["equity"].index,
            "equity": out["equity"].values,
            "benchmark": out["benchmark"].reindex(out["equity"].index).values,
        })
        df_curve.to_parquet(
            holdout_dir / f"holdout_iter_{iter_id:04d}.parquet",
            compression="zstd", index=False,
        )
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--end", default=DEFAULT_HOLDOUT_END)
    ap.add_argument("--tf", default=None,
                    help="If omitted, read strategy.py:DEFAULT_TF, fall back to '1h'.")
    ap.add_argument("--no-audit", action="store_true",
                    help="Skip lookahead audit (only for trusted strategies).")
    ap.add_argument("--lookback", default="60D",
                    help="Pre-load history before holdout start so rolling "
                         "indicators are warm at bar 1. Default: 60D.")
    args = ap.parse_args()
    rep = run_holdout(Path(args.strategy_dir), args.start, args.end, tf=args.tf,
                      audit=not args.no_audit, lookback=args.lookback)
    if rep.get("error"):
        print(json.dumps({"audit_failed": rep["audit"], "error": rep["error"]},
                         indent=2, default=str))
        return
    print(json.dumps({
        "iter": rep["iter"],
        "period": rep["period"],
        "composite_holdout": round(rep["composite"], 4) if rep["composite"] not in (float("inf"), float("-inf")) else None,
        "best_composite_train_val": (round(rep["best_composite_train_val"], 4)
                                     if rep["best_composite_train_val"] is not None else None),
        "sharpe": round(rep["metrics"].get("sharpe", 0.0), 4),
        "max_dd": round(rep["metrics"].get("max_dd", 0.0), 4),
        "n_trades": rep["metrics"].get("n_trades", 0),
    }, indent=2))


if __name__ == "__main__":
    main()
