"""Forward-test runner — run a strategy's locked best snapshot on bars
that came AFTER the holdout window ended.

Purpose:
  - Real-world out-of-sample. The strategy code is fixed (best_strategy.py),
    no params, no tuning. Whatever Sharpe it makes here is the closest
    paper-trading proxy we have without an execution layer.
  - Drift detection: compare the forward Sharpe to the backtest's OOS
    CI. If forward is consistently below the CI lower bound, the edge
    has decayed (or never existed).

Honesty rules:
  - Loads the SNAPSHOT (``runs/best_strategy.py``) not the current
    ``strategy.py`` — the live deployment would have run the locked
    version, not whatever you're editing today.
  - Writes to ``runs/forward/`` only. Never touches best.json,
    history.jsonl, or holdout/.
  - Does NOT participate in any keep/revert loop. Output is read-only
    diagnostic.

Usage:
    uv run python -m runner.forward strategies/<name>
    uv run python -m runner.forward strategies/<name> --start 2026-05-01 --end 2026-05-17
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from harness import backtest as bt
from harness import env as env_mod
from harness.forward import assess_drift, rolling_window_sharpe
from harness.splits import Split


DEFAULT_FORWARD_START = "2026-05-01"     # day after default holdout end
DEFAULT_FORWARD_END_DAYS_LAG = 1          # exclude today's possibly-partial bar


# --------------------------------------------------------------------------- #
def _load_strategy_snapshot(strategy_dir: Path):
    """Load ``runs/best_strategy.py`` as a module if present, else fall
    back to the current ``strategy.py``. We deliberately import the
    snapshot so forward-test reproduces what the locked best traded.
    """
    strategy_dir = Path(strategy_dir).resolve()
    snap = strategy_dir / "runs" / "best_strategy.py"
    file = snap if snap.exists() else (strategy_dir / "strategy.py")
    if not file.exists():
        raise FileNotFoundError(
            f"neither {snap} nor {strategy_dir / 'strategy.py'} found"
        )
    spec = importlib.util.spec_from_file_location(
        f"strategy_fwd_{strategy_dir.name}", file,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, file


def _default_end() -> str:
    """Today at 00:00 UTC minus a one-day buffer, so we don't read a
    partial bar from a still-in-progress day."""
    now = pd.Timestamp.now(tz="UTC").normalize()
    return (now - pd.Timedelta(days=DEFAULT_FORWARD_END_DAYS_LAG)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
def run_forward(strategy_dir: Path,
                start: str | None = None,
                end: str | None = None,
                tf: str | None = None,
                lookback: str | None = "60D") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    runs = strategy_dir / "runs"
    runs.mkdir(exist_ok=True)
    fwd_dir = runs / "forward"
    fwd_dir.mkdir(exist_ok=True)

    best_path = runs / "best.json"
    if not best_path.exists():
        raise RuntimeError(
            f"no best.json — run iterate first so there is a locked snapshot"
        )
    best = json.loads(best_path.read_text(encoding="utf-8"))
    iter_id = int(best.get("iter", 0))

    # Determine forward window. Default: max(holdout_end, best.period_end).
    if start is None:
        # Best.json's period_end is the train+val end; holdout default is
        # 2026-01-01 → 2026-05-01. Forward starts after holdout.
        cands = [DEFAULT_FORWARD_START]
        period = best.get("period") or []
        if period and len(period) == 2:
            cands.append(str(period[1]))
        # Highest of the candidates → "after everything we know about".
        cand_ts = [pd.Timestamp(c, tz="UTC") for c in cands]
        start = max(cand_ts).strftime("%Y-%m-%d")
    if end is None:
        end = _default_end()

    if pd.Timestamp(end, tz="UTC") <= pd.Timestamp(start, tz="UTC"):
        raise RuntimeError(
            f"forward end ({end}) <= start ({start}); no bars to test on. "
            "If today is before forward-window start, wait or use --end."
        )

    mod, code_file = _load_strategy_snapshot(strategy_dir)
    params = dict(getattr(mod, "DEFAULT_PARAMS", {}))
    symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")

    # Single OOS window covering [start, end). Use Split with degenerate
    # train so run_split reuses its full machinery (costs, funding,
    # meta-labeler, etc.). Note: meta-labeler training requires events
    # before ``split.train_end`` — with a degenerate train (train_end=start),
    # the meta-labeler will skip and signals pass through unchanged.
    # That's intentional: the forward window is too short to retrain a
    # meta classifier honestly, so the snapshot's primary signals run raw.
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    split = Split(train_start=s, train_end=s, oos_start=s, oos_end=e)

    out = bt.run_split(mod, params, symbols, split, tf=tf,
                       return_curves=True, lookback=lookback)
    if "equity" not in out:
        raise RuntimeError(
            f"forward backtest returned no equity curve. "
            f"OHLCV data for [{start}, {end}) on {symbols} may be missing — "
            f"run `python -m datafeed.download_bybit --all "
            f"--start {start[:7]} --end {end[:7]}` first."
        )

    equity = out["equity"]
    benchmark = out["benchmark"]
    fwd_returns = equity.pct_change().dropna()

    # Drift assessment vs backtest CI from best.json.
    backtest_metrics = (best.get("metrics") or {}).get("oos") or {}
    ci_lo = backtest_metrics.get("sharpe_ci_lo")
    ci_hi = backtest_metrics.get("sharpe_ci_hi")
    bt_sharpe = backtest_metrics.get("sharpe")
    drift = assess_drift(
        forward_returns=fwd_returns,
        backtest_oos_sharpe=bt_sharpe,
        backtest_sharpe_ci_lo=ci_lo,
        backtest_sharpe_ci_hi=ci_hi,
        tf=tf,
    )

    # Trailing-30d Sharpe trajectory for the UI.
    rs = rolling_window_sharpe(fwd_returns, window_days=30, tf=tf)

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    base = fwd_dir / f"forward_{stamp}_iter_{iter_id:04d}"

    report = {
        "iter": iter_id,
        "ran_at": started.isoformat(),
        "period": [start, end],
        "tf": tf,
        "symbols": symbols,
        "params": params,
        "code_source": str(code_file.relative_to(strategy_dir)) if code_file.is_relative_to(strategy_dir) else str(code_file),
        "snapshot_used": code_file.name == "best_strategy.py",
        "metrics": out.get("oos", {}),
        "drift": drift.to_dict(),
        "backtest_oos_sharpe": bt_sharpe,
        "backtest_sharpe_ci_lo": ci_lo,
        "backtest_sharpe_ci_hi": ci_hi,
        "env": env_mod.capture(),
    }
    base.with_suffix(".json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8",
    )

    # Persist the curve + rolling Sharpe for the UI.
    curve_df = pd.DataFrame({
        "timestamp": equity.index,
        "equity": equity.values,
        "benchmark": benchmark.reindex(equity.index).values,
        "rolling_sharpe_30d": rs.reindex(equity.index).values,
    })
    curve_df.to_parquet(base.with_suffix(".parquet"),
                        compression="zstd", index=False)

    # Convenience pointer: latest.json (the API reads this first).
    (fwd_dir / "latest.json").write_text(
        json.dumps({"file": base.with_suffix(".json").name},
                   indent=2, default=str),
        encoding="utf-8",
    )

    return report


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default=None,
                    help="Forward start. Default: max(2026-05-01, best.period_end).")
    ap.add_argument("--end", default=None,
                    help=f"Forward end. Default: today − {DEFAULT_FORWARD_END_DAYS_LAG}d.")
    ap.add_argument("--tf", default=None,
                    help="TF. Default: read from snapshot strategy module.")
    ap.add_argument("--lookback", default="60D",
                    help="Pre-load history before --start so rolling indicators "
                         "are warm. Default 60D.")
    args = ap.parse_args()

    rep = run_forward(
        Path(args.strategy_dir),
        start=args.start, end=args.end,
        tf=args.tf, lookback=args.lookback,
    )
    d = rep["drift"]
    print(json.dumps({
        "iter": rep["iter"],
        "period": rep["period"],
        "snapshot_used": rep["snapshot_used"],
        "forward_sharpe": round(d["forward_sharpe"], 4),
        "forward_max_dd": round(d["forward_max_dd"], 4),
        "forward_total_return": round(d["forward_total_return"], 4),
        "forward_psr": round(d["forward_psr"], 4) if d["forward_psr"] is not None else None,
        "backtest_sharpe_ci": [
            round(d["backtest_sharpe_ci_lo"], 4) if d["backtest_sharpe_ci_lo"] is not None else None,
            round(d["backtest_sharpe_ci_hi"], 4) if d["backtest_sharpe_ci_hi"] is not None else None,
        ],
        "drift_flag": d["flag"],
        "drift_reason": d["flag_reason"],
        "consecutive_below_ci_days": d["consecutive_below_ci_days"],
    }, indent=2))


if __name__ == "__main__":
    main()
