"""One iteration of the auto-research loop.

The intended workflow:
    1. The agent (you) reads strategies/<name>/program.md and runs/history.jsonl.
    2. The agent edits strategies/<name>/strategy.py.
    3. The agent runs:  python -m runner.iterate strategies/<name> --note "what I tried"
    4. iterate.py:
         - snapshots the current strategy.py as runs/last_attempt.py
         - runs the harness over the configured period (train + OOS, optional walk-forward)
         - computes composite score
         - if better than best.json by --epsilon -> updates best.json (saves the file as runs/best_strategy.py)
         - else -> reverts strategy.py from runs/best_strategy.py (or keeps if no best yet)
         - appends a row to history.jsonl
         - prints a short verdict for the agent
    5. The agent reads the verdict and history, plans the next move, repeat.

Stateless across calls. All state lives in strategies/<name>/runs/.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness import backtest as bt
from harness.metrics import composite_score


# --------------------------------------------------------------------------- #
@dataclass
class IterationConfig:
    # Default split (see AGENTS.md):
    #   train+val = Jan 2024 .. Sep 2025  (used for composite score & keep/revert)
    #   holdout   = Oct 2025 .. Dec 2025  (NEVER touched here — see runner.holdout)
    period_start: str = "2024-01-01"
    period_end: str = "2025-10-01"
    tf: str = "1h"
    walk_windows: int = 0
    dd_penalty: float = 0.5
    min_trades: int = 50
    low_trades_penalty: float = 0.5
    epsilon: float = 0.01            # composite must beat best by this to keep


# --------------------------------------------------------------------------- #
def _runs_dir(strategy_dir: Path) -> Path:
    d = strategy_dir / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_best(runs: Path) -> dict | None:
    p = runs / "best.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _save_best(runs: Path, best: dict, strategy_file: Path) -> None:
    (runs / "best.json").write_text(json.dumps(best, indent=2, default=str))
    shutil.copy2(strategy_file, runs / "best_strategy.py")


def _append_history(runs: Path, row: dict) -> None:
    with (runs / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _next_iter_id(runs: Path) -> int:
    p = runs / "history.jsonl"
    if not p.exists():
        return 1
    with p.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f) + 1


# --------------------------------------------------------------------------- #
def run_one(strategy_dir: Path, cfg: IterationConfig, note: str = "") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    runs = _runs_dir(strategy_dir)
    strategy_file = strategy_dir / "strategy.py"
    if not strategy_file.exists():
        raise FileNotFoundError(strategy_file)

    iter_id = _next_iter_id(runs)
    snapshot = runs / "last_attempt.py"
    shutil.copy2(strategy_file, snapshot)

    started = datetime.now(timezone.utc).isoformat()
    error = None
    try:
        result = bt.run(strategy_dir, cfg.period_start, cfg.period_end,
                        tf=cfg.tf, walk_windows=cfg.walk_windows,
                        return_curves=True)
    except Exception as e:
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        result = {"main": {"train": {}, "oos": {}}}
    finished = datetime.now(timezone.utc).isoformat()

    # Persist equity curve for the dashboard
    curves = result.pop("curves", None) if isinstance(result, dict) else None
    equity_dir = runs / "equity"
    equity_dir.mkdir(exist_ok=True)
    equity_path = None
    if curves is not None:
        import pandas as _pd
        df_curve = _pd.DataFrame({
            "timestamp": curves["equity"].index,
            "equity": curves["equity"].values,
            "benchmark": curves["benchmark"].reindex(curves["equity"].index).values,
        })
        equity_path = equity_dir / f"iter_{iter_id:04d}.parquet"
        df_curve.to_parquet(equity_path, compression="zstd", index=False)
        # Also remember the train/OOS split cutoff for shading the chart
        cutoff_path = equity_dir / f"iter_{iter_id:04d}.json"
        cutoff_path.write_text(json.dumps({"split_cutoff": str(curves["split_cutoff"])}))

    oos = result.get("main", {}).get("oos", {}) or {}
    composite = composite_score(
        oos,
        dd_penalty=cfg.dd_penalty,
        min_trades=cfg.min_trades,
        low_trades_penalty=cfg.low_trades_penalty,
    ) if oos and not error else float("-inf")

    best = _load_best(runs)
    best_score = best["composite"] if best else float("-inf")
    keep = composite > best_score + cfg.epsilon and error is None

    if keep:
        new_best = {
            "iter": iter_id,
            "composite": composite,
            "params": result.get("params"),
            "symbols": result.get("symbols"),
            "tf": result.get("tf"),
            "period": result.get("period"),
            "metrics": result.get("main"),
            "walk_forward": result.get("walk_forward"),
            "note": note,
            "saved_at": finished,
        }
        _save_best(runs, new_best, strategy_file)
        verdict = "KEEP"
    else:
        # Revert to best (if any). If no best yet, leave the current file as-is
        # but note that it didn't improve over -inf only when there was an error.
        best_file = runs / "best_strategy.py"
        if best_file.exists() and not (best is None):
            shutil.copy2(best_file, strategy_file)
            verdict = "REVERT"
        else:
            verdict = "KEEP_NO_BASELINE" if error is None else "ERROR"
            if error is None:
                # First successful iteration — adopt as baseline regardless.
                new_best = {
                    "iter": iter_id,
                    "composite": composite,
                    "params": result.get("params"),
                    "symbols": result.get("symbols"),
                    "tf": result.get("tf"),
                    "period": result.get("period"),
                    "metrics": result.get("main"),
                    "walk_forward": result.get("walk_forward"),
                    "note": note + " [adopted as initial baseline]",
                    "saved_at": finished,
                }
                _save_best(runs, new_best, strategy_file)
                verdict = "BASELINE"

    row = {
        "iter": iter_id,
        "started": started,
        "finished": finished,
        "verdict": verdict,
        "composite": composite,
        "best_before": best_score,
        "params": result.get("params"),
        "metrics_oos": oos,
        "metrics_train": result.get("main", {}).get("train", {}),
        "walk_forward": result.get("walk_forward"),
        "note": note,
        "error": error,
    }
    _append_history(runs, row)

    summary = {
        "iter": iter_id,
        "verdict": verdict,
        "composite": round(composite, 4) if composite != float("-inf") else None,
        "best_before": round(best_score, 4) if best_score != float("-inf") else None,
        "oos_sharpe": round(oos.get("sharpe", 0.0), 4),
        "oos_max_dd": round(oos.get("max_dd", 0.0), 4),
        "oos_n_trades": oos.get("n_trades", 0),
        "error": error,
    }
    return summary


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-04-01")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--walk", type=int, default=0)
    ap.add_argument("--dd-penalty", type=float, default=0.5)
    ap.add_argument("--min-trades", type=int, default=50)
    ap.add_argument("--epsilon", type=float, default=0.01)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    cfg = IterationConfig(
        period_start=args.start,
        period_end=args.end,
        tf=args.tf,
        walk_windows=args.walk,
        dd_penalty=args.dd_penalty,
        min_trades=args.min_trades,
        epsilon=args.epsilon,
    )
    out = run_one(Path(args.strategy_dir), cfg, note=args.note)
    print(json.dumps(out, indent=2))
    if out.get("error"):
        sys.exit(2)


if __name__ == "__main__":
    main()
