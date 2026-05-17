"""Cross-strategy multiple-testing report.

Gathers OOS returns from every strategy's current best.json, aligns them
on a common (daily) timestamp grid, and runs three formal tests:

  - White (2000) Reality Check  → "is the BEST of N better than zero?"
  - Hansen (2005) SPA           → same, less conservative
  - Romano-Wolf (2005) stepdown → per-strategy FWER-adjusted p-values

DSR already handles within-strategy selection bias (number of iters).
This module handles ACROSS-strategy selection bias (number of strategies
in the stable). Both are needed for an honest "the winner is real" claim.

Output:
  runs/_multistrat/multistrat_<UTC>.json        — full report
  runs/_multistrat/multistrat_<UTC>.parquet     — per-strategy daily returns

The output lives at REPO ROOT (not under any single strategy) because the
report is across strategies. The web layer surfaces the latest report.

Usage:
    uv run python -m runner.multistrat
    uv run python -m runner.multistrat --strategies xs_momentum,mr_zscore
    uv run python -m runner.multistrat --n-boot 2000 --seed 42
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from harness import env as env_mod
from harness.multistrat import multistrat_tests

ROOT = Path(__file__).resolve().parents[1]
STRATS = ROOT / "strategies"
OUT_DIR = ROOT / "runs" / "_multistrat"


# --------------------------------------------------------------------------- #
def _list_strategies() -> list[str]:
    if not STRATS.exists():
        return []
    out = []
    for d in sorted(STRATS.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if not (d / "strategy.py").exists():
            continue
        out.append(d.name)
    return out


def _load_oos_returns(strategy: str) -> tuple[pd.Series | None, dict]:
    """Extract daily OOS returns from this strategy's current best.json
    equity curve. Returns (series_or_None, info_dict).

    For walk-forward iters: concatenates each window's OOS slice
    (timestamp ≥ window split_cutoff) and converts bar-level equity to
    bar-level returns BEFORE daily resampling. Daily resample uses
    prod(1+r)−1 to avoid the well-known pct_change-of-last bugs (drops
    first bar; loses end-of-month→start-of-month transition).
    """
    sdir = STRATS / strategy
    best_path = sdir / "runs" / "best.json"
    if not best_path.exists():
        return None, {"reason": "no best.json"}
    try:
        best = json.loads(best_path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, {"reason": f"best.json unreadable: {e}"}

    iter_id = best.get("iter")
    if iter_id is None:
        return None, {"reason": "best.json missing iter"}

    eq_path = sdir / "runs" / "equity" / f"iter_{int(iter_id):04d}.parquet"
    eq_meta = sdir / "runs" / "equity" / f"iter_{int(iter_id):04d}.json"
    if not eq_path.exists():
        return None, {"reason": f"no equity parquet for iter {iter_id}"}

    df = pd.read_parquet(eq_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    cutoffs: list[pd.Timestamp] = []
    if eq_meta.exists():
        try:
            meta = json.loads(eq_meta.read_text(encoding="utf-8"))
            raw_cutoffs = meta.get("split_cutoffs") or (
                [meta["split_cutoff"]] if meta.get("split_cutoff") else []
            )
            cutoffs = [pd.Timestamp(c) for c in raw_cutoffs]
        except Exception:
            pass

    has_windows = "window" in df.columns
    bar_returns: list[pd.Series] = []
    n_bars_oos = 0
    if has_windows:
        for w, g in df.groupby("window"):
            g = g.sort_values("timestamp")
            wi = int(w)
            cut = cutoffs[wi] if wi < len(cutoffs) else None
            # OOS slice
            if cut is not None:
                oos = g[g["timestamp"] >= cut]
            else:
                # Fall back: assume last 25% is OOS (default split ratio).
                k = int(len(g) * 0.75)
                oos = g.iloc[k:]
            if oos.empty or len(oos) < 2:
                continue
            r = oos["equity"].pct_change().dropna()
            r.index = oos["timestamp"].iloc[1:].values
            bar_returns.append(r)
            n_bars_oos += len(r)
    else:
        df = df.sort_values("timestamp")
        cut = cutoffs[0] if cutoffs else None
        if cut is not None:
            oos = df[df["timestamp"] >= cut]
        else:
            k = int(len(df) * 0.75)
            oos = df.iloc[k:]
        if oos.empty or len(oos) < 2:
            return None, {"reason": "OOS slice has <2 bars"}
        r = oos["equity"].pct_change().dropna()
        r.index = oos["timestamp"].iloc[1:].values
        bar_returns.append(r)
        n_bars_oos = len(r)

    if not bar_returns:
        return None, {"reason": "no OOS returns extracted"}

    cat = pd.concat(bar_returns).sort_index()
    cat = cat[~cat.index.duplicated(keep="first")]
    # Daily compound: prod(1+r)-1 per UTC day.
    daily = ((1.0 + cat).resample("1D").prod() - 1.0).dropna()
    daily.name = strategy

    return daily, {
        "iter": int(iter_id),
        "n_bars_oos": int(n_bars_oos),
        "n_days": int(len(daily)),
        "tf": best.get("tf"),
        "composite": best.get("composite"),
        "oos_start": str(daily.index.min()) if len(daily) else None,
        "oos_end": str(daily.index.max()) if len(daily) else None,
    }


# --------------------------------------------------------------------------- #
def run_multistrat(
    strategies: list[str] | None = None,
    n_boot: int = 1000,
    block_size: int | None = None,
    seed: int | None = None,
    benchmark: float = 0.0,
    join: str = "inner",
) -> dict:
    """Build the [T × N] daily returns matrix and run the three tests.

    `join`: "inner" (default — strict intersect of timestamps) or
            "outer" (union; missing values become NaN and get dropped
            jointly by multistrat_tests). Inner is the honest default.
    """
    if strategies is None:
        strategies = _list_strategies()
    if not strategies:
        raise RuntimeError("no strategies discovered")

    per_strategy_meta: dict[str, dict] = {}
    series_list: list[pd.Series] = []
    skipped: list[dict] = []

    for s in strategies:
        ser, info = _load_oos_returns(s)
        per_strategy_meta[s] = info
        if ser is None:
            skipped.append({"strategy": s, **info})
            continue
        series_list.append(ser)

    if len(series_list) < 2:
        raise RuntimeError(
            f"need ≥2 strategies with usable OOS curves; got {len(series_list)} "
            f"(skipped: {len(skipped)})"
        )

    matrix = pd.concat(series_list, axis=1, join=join).sort_index()
    matrix.columns = [s.name for s in series_list]
    # Drop fully-NaN rows up front for clarity (multistrat_tests would do this anyway).
    matrix = matrix.dropna(how="any")

    if matrix.shape[0] < 30:
        raise RuntimeError(
            f"only {matrix.shape[0]} jointly-aligned daily observations after "
            f"intersect; need ≥30. Consider --join outer (less strict) or "
            f"removing strategies with very short OOS slices."
        )

    # Correlation matrix — useful for the UI, cheap to compute.
    corr = matrix.corr()
    corr_dict = {c: corr[c].to_dict() for c in corr.columns}

    tests = multistrat_tests(
        matrix, n_boot=n_boot, block_size=block_size,
        seed=seed, benchmark=benchmark,
    )

    started = datetime.now(timezone.utc)
    report = {
        "ran_at": started.isoformat(),
        "n_strategies_input": len(strategies),
        "n_strategies_used": int(matrix.shape[1]),
        "n_days": int(matrix.shape[0]),
        "strategies_used": list(matrix.columns),
        "strategies_skipped": skipped,
        "per_strategy_meta": {
            n: per_strategy_meta.get(n, {}) for n in matrix.columns
        },
        "join": join,
        "seed": seed,
        "correlation_matrix": corr_dict,
        "tests": tests,
        "env": env_mod.capture(),
    }
    return {"report": report, "matrix": matrix}


def _persist(rep_and_matrix: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = OUT_DIR / f"multistrat_{stamp}"
    rep = rep_and_matrix["report"]
    matrix: pd.DataFrame = rep_and_matrix["matrix"]
    base.with_suffix(".json").write_text(
        json.dumps(rep, indent=2, default=str), encoding="utf-8"
    )
    # Persist the matrix so the UI can plot per-strategy curves / scatter.
    df_persist = matrix.reset_index().rename(columns={"index": "timestamp"})
    # Ensure column 0 is named "timestamp" regardless of original index name.
    df_persist.columns = ["timestamp"] + list(matrix.columns)
    df_persist.to_parquet(base.with_suffix(".parquet"), compression="zstd", index=False)
    return base.with_suffix(".json")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategies", default=None,
                    help="Comma-separated subset; default = all discovered.")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--block-size", type=int, default=None,
                    help="Stationary-bootstrap mean block length. Default n^(1/3).")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed. Default: nondeterministic.")
    ap.add_argument("--benchmark", type=float, default=0.0,
                    help="H0: mean daily return ≤ benchmark. Default 0.")
    ap.add_argument("--join", choices=["inner", "outer"], default="inner")
    args = ap.parse_args()

    strats = [s.strip() for s in args.strategies.split(",")] if args.strategies else None
    rep_and_matrix = run_multistrat(
        strategies=strats,
        n_boot=args.n_boot, block_size=args.block_size,
        seed=args.seed, benchmark=args.benchmark, join=args.join,
    )
    out = _persist(rep_and_matrix)
    rep = rep_and_matrix["report"]
    tests = rep["tests"]

    print(json.dumps({
        "report_file": out.name,
        "n_strategies_used": rep["n_strategies_used"],
        "n_days": rep["n_days"],
        "reality_check_p": round(tests["reality_check"]["p_value"], 4),
        "spa_p_consistent": round(tests["spa"]["p_value_consistent"], 4),
        "spa_p_lower": round(tests["spa"]["p_value_lower"], 4),
        "spa_p_upper": round(tests["spa"]["p_value_upper"], 4),
        "rw_n_reject_at_05": sum(
            1 for r in tests["romano_wolf"] if r["reject_at_05"]
        ),
        "winners_at_05": [
            r["strategy"] for r in tests["romano_wolf"] if r["reject_at_05"]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
