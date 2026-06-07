"""Train-only parameter optimizer → a *universe* of robust parameter plateaus.

What this is
------------
Every strategy exports ``PARAM_SPACE`` (hint ranges like ``{"cci_period":
(10, 30)}``). This tool searches that space and hands the agent back a small set
of **robust parameter regions** — plateaus where the strategy is consistently
good across inner walk-forward folds — instead of one cherry-picked peak.

The discipline (read this before trusting the output)
-----------------------------------------------------
The optimizer runs **strictly inside the train slice**: ``[period_start,
train_cutoff)`` where ``train_cutoff`` is the single-split train/OOS boundary
(default: first 75% of the iter period). It therefore NEVER sees:

  - the reserved OOS tail that ``runner.iterate`` uses for keep/revert, nor
  - the holdout (2026), which is hard-capped out.

Within the train slice it runs its own inner walk-forward (``--inner-windows``)
and scores each candidate by ``mean(fold_sharpe) - 0.5*std(fold_sharpe)`` with
trade-count and time-in-position gates. This is exactly the "tune within the
train slice, choose without seeing OOS" rule from METHODS.md §6.2.

Read-only relative to the iter loop: it writes ONLY to
``strategies/<name>/optimize/<id>/`` and never touches ``best.json``,
``history.jsonl``, ``strategy.py``, or ``program.md``.

Agent workflow
--------------
    1. uv run python -m runner.optimize strategies/<name> --params cci_period cci_threshold
    2. Read universe.json → pick the CENTER of the widest high-score plateau
       (a wide plateau is robust; a 1-config spike is overfit).
    3. Set those values in DEFAULT_PARAMS in strategy.py.
    4. uv run python -m runner.iterate strategies/<name> --note "..."  ← OOS judges it,
       and the OOS was never seen by the optimizer.

Usage
-----
    uv run python -m runner.optimize strategies/pivot_cci \
        --params cci_period cci_threshold

    uv run python -m runner.optimize strategies/pivot_cci \
        --params cci_period cci_threshold trend_period cci_exit \
        --n-quasi 256 --inner-windows 3 --tag "cci-family"
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from harness import paramopt as po

# Holdout starts here — the optimizer is hard-capped before it, so a parameter
# search can never, even by misconfiguration, peek at the final exam.
HOLDOUT_START = "2026-01-01"


# --------------------------------------------------------------------------- #
# Per-candidate worker (process-pool friendly: module-level, dict args)
# --------------------------------------------------------------------------- #
def _eval_candidate(args: dict) -> dict:
    """Backtest one candidate over the train slice with inner walk-forward.

    Returns the candidate's per-fold OOS metrics (inner-oos, train-only). The
    scoring + clustering happens in the parent so all candidates share one
    consistent view. Designed to be picklable and run in a separate process.
    """
    strategy_dir = args["strategy_dir"]
    params = args["params"]
    symbols = args["symbols"]
    tf = args["tf"]
    opt_start = args["opt_start"]
    opt_end = args["opt_end"]
    inner_windows = args["inner_windows"]
    embargo = args["embargo"]
    lookback = args["lookback"]
    cost_model = args["cost_model"]

    fold_sharpe: list[float] = []
    fold_trades: list[float] = []
    fold_tip: list[float] = []
    error: str | None = None
    t0 = time.time()

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            from harness import backtest as bt
            from harness.costs import CostModel

            cost_kwargs = {
                "static": {},
                "spread": {"use_dynamic_spread": True},
                "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
            }[cost_model]
            costs = CostModel(**cost_kwargs)

            res = bt.run(
                strategy_dir, opt_start, opt_end,
                symbols=symbols, tf=tf,
                params=params,
                walk_windows=inner_windows,
                embargo=embargo,
                lookback=lookback,
                costs=costs,
                return_curves=False,
            )
            wf = (res.get("walk_forward") or {}).get("windows") or []
            blocks = [w.get("oos", {}) for w in wf] if wf \
                else [(res.get("main") or {}).get("oos", {})]
            for b in blocks:
                if not b:
                    continue
                fold_sharpe.append(float(b.get("sharpe", 0.0)))
                fold_trades.append(float(b.get("n_trades", 0)))
                tip = b.get("pct_time_in_position")
                if tip is not None:
                    fold_tip.append(float(tip))
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exception(type(e), e, e.__traceback__)

    return {
        "params": params,
        "fold_sharpe": fold_sharpe,
        "fold_trades": fold_trades,
        "fold_tip": fold_tip,
        "duration_s": round(time.time() - t0, 2),
        "error": error,
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _strategy_sha256(strategy_dir: Path) -> str:
    h = hashlib.sha256()
    h.update((strategy_dir / "strategy.py").read_bytes())
    return h.hexdigest()


def _train_cutoff(period_start: str, period_end: str, oos_fraction: float) -> pd.Timestamp:
    """Single-split train/OOS boundary — the optimizer's hard right edge."""
    from harness.splits import train_oos
    return train_oos(period_start, period_end, oos_fraction=oos_fraction).train_end


def run_optimize(strategy_dir: Path, params: list[str] | None = None,
                 period_start: str = "2024-01-01", period_end: str = "2026-01-01",
                 oos_fraction: float = 0.25, inner_windows: int = 3,
                 method: str = "auto", grid_resolution: int = 12,
                 n_quasi: int = 256, seed: int = 0,
                 stability_penalty: float = 0.5,
                 min_trades_per_fold: float = 10.0, low_trades_penalty: float = 0.5,
                 min_tip: float = 20.0, tip_penalty: float = 1.0,
                 top_frac: float = 0.25, cluster_radius: float = 0.2,
                 max_plateaus: int = 8,
                 embargo: str = "1D", lookback: str = "60D",
                 cost_model: str = "static", tf: str | None = None,
                 parallel: int | None = None, max_evals: int | None = None,
                 tag: str = "") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    if not (strategy_dir / "strategy.py").exists():
        raise FileNotFoundError(strategy_dir / "strategy.py")

    from harness.backtest import load_strategy
    mod = load_strategy(strategy_dir)
    param_space = dict(getattr(mod, "PARAM_SPACE", {}) or {})
    defaults = dict(getattr(mod, "DEFAULT_PARAMS", {}) or {})
    if not param_space:
        raise SystemExit("strategy exports no PARAM_SPACE — nothing to optimize")
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")
    symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])

    # ---- Train-only boundary (the safety property) ----
    cutoff = _train_cutoff(period_start, period_end, oos_fraction)
    opt_start = period_start
    opt_end = cutoff.strftime("%Y-%m-%d")
    holdout = pd.Timestamp(HOLDOUT_START, tz="UTC")
    if cutoff > holdout:
        # Should never happen with default 2026-01-01 end, but a user passing a
        # late --end could otherwise drag the train cutoff into the holdout.
        opt_end = HOLDOUT_START
    print(f"[optimize] train-only window: {opt_start} -> {opt_end} "
          f"(reserved OOS tail {opt_end} -> {period_end} and holdout are NOT touched)",
          flush=True)

    # ---- Build candidate set ----
    specs = po.infer_specs(param_space, defaults, only=params)
    candidates, method_used = po.build_candidates(
        specs, method=method, grid_resolution=grid_resolution,
        n_quasi=n_quasi, seed=seed,
    )
    if max_evals is not None and len(candidates) > max_evals:
        print(f"[optimize] capping {len(candidates)} candidates -> {max_evals} "
              f"(--max-evals)", flush=True)
        candidates = candidates[:max_evals]
    print(f"[optimize] {len(specs)} param(s) via {method_used}: "
          f"{[s.name for s in specs]} -> {len(candidates)} candidates, "
          f"inner_windows={inner_windows}", flush=True)

    # ---- Output dir ----
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    opt_id = f"{stamp}__{tag}" if tag else stamp
    out_dir = strategy_dir / "optimize" / opt_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Evaluate (parallel) ----
    cells = [{
        "strategy_dir": str(strategy_dir),
        "params": {**defaults, **cand},   # full param dict so untuned keys keep defaults
        "symbols": symbols,
        "tf": tf,
        "opt_start": opt_start,
        "opt_end": opt_end,
        "inner_windows": inner_windows,
        "embargo": embargo,
        "lookback": lookback,
        "cost_model": cost_model,
    } for cand in candidates]

    workers = parallel or min(8, os.cpu_count() or 4)
    print(f"[optimize] evaluating with {workers} worker(s) ...", flush=True)
    started = time.time()
    raw_results: list[dict] = [None] * len(cells)  # preserve candidate order

    def _progress(done: int):
        try:
            (out_dir / "progress.json").write_text(json.dumps({
                "done": done, "total": len(cells),
                "elapsed_s": round(time.time() - started, 1),
            }), encoding="utf-8")
        except Exception:
            pass

    if workers == 1:
        for i, c in enumerate(cells):
            raw_results[i] = _eval_candidate(c)
            _progress(i + 1)
            print(f"[optimize] {i+1}/{len(cells)}  err={raw_results[i]['error']}",
                  flush=True)
    else:
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_eval_candidate, c): i for i, c in enumerate(cells)}
            done = 0
            for fut in cf.as_completed(futs):
                i = futs[fut]
                try:
                    raw_results[i] = fut.result()
                except Exception as e:
                    raw_results[i] = {
                        "params": cells[i]["params"], "fold_sharpe": [],
                        "fold_trades": [], "fold_tip": [],
                        "error": f"worker crash: {type(e).__name__}: {e}",
                    }
                done += 1
                _progress(done)
                if done % max(1, len(cells) // 20) == 0 or done == len(cells):
                    print(f"[optimize] {done}/{len(cells)}", flush=True)

    # ---- Score each candidate (train-only) ----
    scores: list[po.FoldScore] = []
    for r in raw_results:
        scores.append(po.train_only_score(
            r["fold_sharpe"], r["fold_trades"], r["fold_tip"],
            stability_penalty=stability_penalty,
            min_trades_per_fold=min_trades_per_fold,
            low_trades_penalty=low_trades_penalty,
            min_tip=min_tip, tip_penalty=tip_penalty,
        ))

    # Candidate dicts restricted to the tuned params (for clustering / display).
    tuned_cands = [{s.name: r["params"][s.name] for s in specs}
                   for r in raw_results]

    # ---- Cluster into plateaus ----
    plateaus = po.cluster_plateaus(
        tuned_cands, scores, specs,
        top_frac=top_frac, radius=cluster_radius, max_plateaus=max_plateaus,
    )

    duration = round(time.time() - started, 1)
    n_eligible = sum(1 for s in scores if s.eligible)
    n_errors = sum(1 for r in raw_results if r.get("error"))

    # ---- Persist: full candidate table ----
    rows = []
    for cand, sc, r in zip(tuned_cands, scores, raw_results):
        row = dict(cand)
        row.update({
            "score": (sc.score if np.isfinite(sc.score) else None),
            "eligible": sc.eligible,
            "reason": sc.reason,
            "mean_fold_sharpe": sc.mean_sharpe,
            "std_fold_sharpe": sc.std_sharpe,
            "median_fold_sharpe": sc.median_sharpe,
            "mean_n_trades": sc.mean_n_trades,
            "mean_tip": sc.mean_tip,
            "n_folds": sc.n_folds,
            "error": r.get("error"),
        })
        rows.append(row)
    cand_df = pd.DataFrame(rows)
    cand_df.to_parquet(out_dir / "candidates.parquet", compression="zstd", index=False)

    # ---- Universe: the plateau payload the agent reads ----
    universe = [{
        "region": chr(ord("A") + i),
        "center": p.center,
        "span": p.span,
        "n_configs": p.n_configs,
        "mean_score": round(p.mean_score, 4),
        "max_score": round(p.max_score, 4),
        "median_fold_sharpe": round(p.median_fold_sharpe, 4),
        "mean_fold_std": round(p.mean_fold_std, 4),
    } for i, p in enumerate(plateaus)]

    manifest = {
        "optimize_id": opt_id,
        "strategy": strategy_dir.name,
        "strategy_sha256": _strategy_sha256(strategy_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": duration,
        "tag": tag,
        "tf": tf,
        "symbols": symbols,
        "tuned_params": [s.name for s in specs],
        "param_kinds": {s.name: s.kind for s in specs},
        "param_bounds": {s.name: [s.lo, s.hi] for s in specs},
        "fixed_params": {k: v for k, v in defaults.items()
                          if k not in {s.name for s in specs}},
        "method": method_used,
        "n_candidates": len(candidates),
        "n_eligible": n_eligible,
        "n_errors": n_errors,
        "inner_windows": inner_windows,
        "scoring": {
            "formula": "mean(fold_sharpe) - stability_penalty*std(fold_sharpe) "
                       "- graded low-trades & low-tip penalties (composite-style)",
            "stability_penalty": stability_penalty,
            "min_trades_per_fold": min_trades_per_fold,
            "low_trades_penalty": low_trades_penalty,
            "min_tip": min_tip,
            "tip_penalty": tip_penalty,
            "ineligible_only_when": "mean_n_trades == 0",
        },
        "clustering": {"top_frac": top_frac, "radius": cluster_radius,
                       "max_plateaus": max_plateaus},
        "train_only_window": [opt_start, opt_end],
        "reserved_oos_tail": [opt_end, period_end],
        "holdout_untouched": True,
        "embargo": embargo,
        "lookback": lookback,
        "cost_model": cost_model,
        "universe": universe,
    }
    (out_dir / "universe.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    # ---- CLI summary ----
    print("", flush=True)
    print(f"[optimize] done in {duration}s — {len(candidates)} candidates, "
          f"{n_eligible} eligible, {n_errors} errors", flush=True)
    if not universe:
        print("[optimize] NO eligible plateau — every candidate either never "
              "traded (mean_n_trades==0) or errored. Check ranges / data "
              "coverage.", flush=True)
    else:
        print(f"[optimize] {len(universe)} plateau region(s):", flush=True)
        for u in universe:
            center = ", ".join(f"{k}={v}" for k, v in u["center"].items())
            print(f"[optimize]   {u['region']}: center({center})  "
                  f"score={u['mean_score']}  med_SR={u['median_fold_sharpe']}  "
                  f"fold_std={u['mean_fold_std']}  n={u['n_configs']}", flush=True)
    print(f"[optimize] artefacts: {out_dir}", flush=True)

    return {"optimize_id": opt_id, "out_dir": str(out_dir),
            "universe": universe, "manifest": manifest}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    # UTF-8 stdout BEFORE argparse: --help prints the docstring/help text which
    # contains unicode arrows; the Windows cp1252 console would otherwise choke.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("strategy_dir", help="Path to strategies/<name>/")
    ap.add_argument("--params", nargs="*", default=None,
                    help="Subset of PARAM_SPACE keys to tune. Default: all keys. "
                         "Tune 1-2 for a visualizable grid; 3+ uses Sobol.")
    ap.add_argument("--start", default="2024-01-01",
                    help="Iter period start (same default as runner.iterate).")
    ap.add_argument("--end", default="2026-01-01",
                    help="Iter period end (exclusive). The train cutoff is "
                         "derived from this; the optimizer stays strictly before it.")
    ap.add_argument("--oos-fraction", type=float, default=0.25,
                    help="Fraction reserved as the OOS tail the optimizer must "
                         "NOT touch. Default 0.25 (matches runner.iterate split).")
    ap.add_argument("--inner-windows", type=int, default=3,
                    help="Walk-forward windows INSIDE the train slice for "
                         "robustness scoring. Default 3.")
    ap.add_argument("--method", choices=["auto", "grid", "quasi"], default="auto",
                    help="auto = grid for <=2 params, Sobol for 3+.")
    ap.add_argument("--grid-resolution", type=int, default=12,
                    help="Points per axis for grid search. Default 12.")
    ap.add_argument("--n-quasi", type=int, default=256,
                    help="Sobol samples for 3+ params. Default 256.")
    ap.add_argument("--seed", type=int, default=0,
                    help="Quasi-random seed (reproducible). Default 0.")
    ap.add_argument("--stability-penalty", type=float, default=0.5,
                    help="k in score = mean(SR) - k*std(SR). Default 0.5.")
    ap.add_argument("--min-trades-per-fold", type=float, default=10.0,
                    help="Per-fold trade threshold; below it a GRADED low-trades "
                         "penalty applies (composite-style). Only mean_n_trades==0 "
                         "is ineligible. Default 10.")
    ap.add_argument("--low-trades-penalty", type=float, default=0.5,
                    help="Weight of the graded low-trades penalty. Default 0.5.")
    ap.add_argument("--min-tip", type=float, default=20.0,
                    help="Per-fold time-in-position threshold (%%); below it a "
                         "GRADED penalty applies (composite-style), NOT a hard "
                         "cutoff. Default 20.")
    ap.add_argument("--tip-penalty", type=float, default=1.0,
                    help="Weight of the graded time-in-position penalty. Default 1.0.")
    ap.add_argument("--top-frac", type=float, default=0.25,
                    help="Top fraction by score considered plateau-eligible "
                         "before clustering. Default 0.25.")
    ap.add_argument("--cluster-radius", type=float, default=0.2,
                    help="Plateau merge radius in normalized [0,1]^d space. "
                         "Default 0.2.")
    ap.add_argument("--max-plateaus", type=int, default=8)
    ap.add_argument("--embargo", default="1D")
    ap.add_argument("--lookback", default="60D")
    ap.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static")
    ap.add_argument("--tf", default=None,
                    help="If omitted, read strategy DEFAULT_TF, fall back to 1h.")
    ap.add_argument("--parallel", type=int, default=None,
                    help="Worker processes. Default min(8, cpu_count).")
    ap.add_argument("--max-evals", type=int, default=None,
                    help="Hard cap on candidates evaluated (truncates the set). "
                         "Use to bound runtime.")
    ap.add_argument("--tag", default="", help="Free-form label appended to optimize_id.")
    args = ap.parse_args()

    out = run_optimize(
        Path(args.strategy_dir), params=args.params,
        period_start=args.start, period_end=args.end,
        oos_fraction=args.oos_fraction, inner_windows=args.inner_windows,
        method=args.method, grid_resolution=args.grid_resolution,
        n_quasi=args.n_quasi, seed=args.seed,
        stability_penalty=args.stability_penalty,
        min_trades_per_fold=args.min_trades_per_fold,
        low_trades_penalty=args.low_trades_penalty,
        min_tip=args.min_tip, tip_penalty=args.tip_penalty,
        top_frac=args.top_frac, cluster_radius=args.cluster_radius,
        max_plateaus=args.max_plateaus,
        embargo=args.embargo, lookback=args.lookback, cost_model=args.cost_model,
        tf=args.tf, parallel=args.parallel, max_evals=args.max_evals, tag=args.tag,
    )
    print(json.dumps({
        "optimize_id": out["optimize_id"],
        "out_dir": out["out_dir"],
        "universe": out["universe"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
