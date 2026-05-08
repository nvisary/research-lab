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
import dataclasses
import json
import shutil
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from harness import backtest as bt
from harness import lookahead as la
from harness import tearsheet as tearsheet_mod
from harness.metrics import aggregate_wf_composite, composite_score
from harness.stats import deflated_sharpe
from datafeed.loader import load_many


# --------------------------------------------------------------------------- #
@dataclass
class IterationConfig:
    # Default split (see AGENTS.md):
    #   train+val = Jan 2024 .. Sep 2025  (used for composite score & keep/revert)
    #   holdout   = Oct 2025 .. Dec 2025  (NEVER touched here — see runner.holdout)
    period_start: str = "2024-01-01"
    period_end: str = "2025-10-01"
    # tf=None means "read from strategy.py:DEFAULT_TF, fall back to 1h".
    # CLI --tf or programmatic override forces a specific value regardless.
    tf: str | None = None
    walk_windows: int = 4   # 4 walk-forward windows ~5mo each on the default period
    stability_penalty: float = 0.5
    dd_penalty: float = 0.5
    min_trades: int = 50
    low_trades_penalty: float = 0.5
    epsilon: float = 0.01            # composite must beat best by this to keep
    # Lookahead audit:
    #   "once"   — run when strategy.py's sha256 changed since last passing audit
    #   "always" — run every iteration (slower, exhaustive)
    #   "never"  — skip (only for tight optimization on already-trusted strategies)
    audit_mode: str = "once"
    audit_k: int = 12
    audit_sample_bars: int = 1500


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


def _run_audit(runs: Path, strategy_dir: Path, cfg: IterationConfig) -> tuple[bool, str | None, dict | None]:
    """Lookahead audit pre-flight.

    Returns (should_skip_backtest, error_message_or_none, audit_summary_or_none).
    On a clean pass, writes runs/last_audit.json with the strategy's sha256.
    On any failure (lookahead or determinism) returns an error message and a
    structured summary; the caller should record verdict=LOOKAHEAD_BUG and
    revert the strategy file.
    """
    strategy_file = strategy_dir / "strategy.py"
    cur_hash = la.file_sha256(strategy_file)
    audit_log = runs / "last_audit.json"

    # "once": skip when prior pass exists for the same hash.
    if cfg.audit_mode == "never":
        return False, None, None
    if cfg.audit_mode == "once" and audit_log.exists():
        try:
            prev = json.loads(audit_log.read_text(encoding="utf-8"))
            if prev.get("sha256") == cur_hash and prev.get("passed") is True:
                return False, None, {"audit": "skipped (sha256 unchanged)",
                                     "sha256": cur_hash}
        except Exception:
            pass

    # Load a small audit window — independent of full iter dataset for speed.
    mod = bt.load_strategy(strategy_dir)
    symbols = (getattr(mod, "DEFAULT_SYMBOLS", None) or ["BTCUSDT"])[:2]
    audit_start = pd.Timestamp(cfg.period_start, tz="UTC")
    audit_end = audit_start + pd.Timedelta(days=120)  # 4 months × 1h ≈ 2900 bars
    audit_data = load_many(symbols, audit_start, audit_end, tf=cfg.tf)
    audit_data = {s: df for s, df in audit_data.items() if not df.empty}
    if not audit_data:
        return False, None, {"audit": "skipped (no data in audit window)",
                             "sha256": cur_hash}

    try:
        report = la.audit(
            mod, audit_data, dict(getattr(mod, "DEFAULT_PARAMS", {})),
            k=cfg.audit_k, sample_bars=cfg.audit_sample_bars,
        )
    except (la.LookaheadError, la.DeterminismError) as e:
        offending = getattr(e, "offending", None)
        summary = {
            "audit": "FAILED",
            "sha256": cur_hash,
            "error_type": type(e).__name__,
            "mode": getattr(e, "mode", None),
            "message": str(e),
            "offending_first_5": [
                {"timestamp": str(t), "symbol": s, "orig": o, "perturbed": p}
                for (t, s, o, p) in (offending or [])[:5]
            ],
        }
        # Persist the failed audit so future "once"-mode runs do not silently
        # skip a known-bad strategy until it gets a code change.
        audit_log.write_text(json.dumps({
            "sha256": cur_hash,
            "passed": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            **summary,
        }, indent=2), encoding="utf-8")
        return True, str(e), summary

    audit_log.write_text(json.dumps({
        "sha256": cur_hash,
        "passed": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "k": report.k_perturbations,
        "n_symbols": report.n_symbols_tested,
        "n_bars": report.n_bars_tested,
        "duration_seconds": report.duration_seconds,
        "notes": report.notes,
    }, indent=2), encoding="utf-8")
    return False, None, {
        "audit": "passed",
        "sha256": cur_hash,
        "k_perturbations": report.k_perturbations,
        "duration_seconds": report.duration_seconds,
    }


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

    # Resolve TF: explicit cfg.tf wins; otherwise read DEFAULT_TF from the
    # strategy module; otherwise fall back to "1h".
    if cfg.tf is None:
        try:
            mod_for_tf = bt.load_strategy(strategy_dir)
            cfg = dataclasses.replace(cfg, tf=getattr(mod_for_tf, "DEFAULT_TF", "1h"))
        except Exception:
            cfg = dataclasses.replace(cfg, tf="1h")

    started = datetime.now(timezone.utc).isoformat()

    # ---- Lookahead audit pre-flight ----
    audit_block, audit_err, audit_summary = _run_audit(runs, strategy_dir, cfg)
    if audit_block:
        # Strategy is broken — revert to best (if any) and record without backtesting.
        finished = datetime.now(timezone.utc).isoformat()
        best_now = _load_best(runs)
        best_file = runs / "best_strategy.py"
        if best_file.exists() and best_now is not None:
            shutil.copy2(best_file, strategy_file)
            verdict = "LOOKAHEAD_BUG"
        else:
            # No baseline yet; leave the file in place so the agent can fix it.
            verdict = "LOOKAHEAD_BUG_NO_BASELINE"
        row = {
            "iter": iter_id,
            "started": started,
            "finished": finished,
            "verdict": verdict,
            "composite": None,
            "best_before": (best_now or {}).get("composite"),
            "params": None,
            "metrics_oos": {},
            "metrics_train": {},
            "walk_forward": None,
            "wf_aggregate": None,
            "dsr": 0.0,
            "audit": audit_summary,
            "note": note,
            "error": audit_err,
        }
        _append_history(runs, row)
        return {
            "iter": iter_id,
            "verdict": verdict,
            "composite": None,
            "best_before": (best_now or {}).get("composite"),
            "oos_sharpe": 0.0,
            "oos_max_dd": 0.0,
            "oos_n_trades": 0,
            "dsr": 0.0,
            "error": audit_err,
            "audit": audit_summary,
        }

    error = None
    try:
        result = bt.run(strategy_dir, cfg.period_start, cfg.period_end,
                        tf=cfg.tf, walk_windows=cfg.walk_windows,
                        return_curves=True)
    except Exception as e:
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        result = {"main": {"train": {}, "oos": {}}}
    finished = datetime.now(timezone.utc).isoformat()

    # Persist equity curve(s) for the dashboard.
    # WF mode: concatenate all window curves into one file with a `window` column
    # so the frontend can color them; cutoffs become a list. Single-split mode:
    # behaves as before.
    curves = result.pop("curves", None) if isinstance(result, dict) else None
    wf_curves = (result.get("walk_forward") or {}).pop("curves", None)
    equity_dir = runs / "equity"
    equity_dir.mkdir(exist_ok=True)
    equity_path = None

    def _curve_to_df(c, window: int | None = None):
        import pandas as _pd
        eq = c["equity"]
        cols = {
            "timestamp": eq.index,
            "equity": eq.values,
            "benchmark": c["benchmark"].reindex(eq.index).values,
        }
        if c.get("raw_equity") is not None:
            cols["raw_equity"] = c["raw_equity"].reindex(eq.index).values
        if c.get("funding_cashflow") is not None:
            cols["funding_cashflow"] = c["funding_cashflow"].reindex(eq.index).values
        df = _pd.DataFrame(cols)
        if window is not None:
            df["window"] = window
        return df

    # Trade ledger: concat across windows with a `window` column for analysis.
    trades_dir = runs / "trades"
    trades_dir.mkdir(exist_ok=True)
    if wf_curves and any(c.get("trades") is not None and not c["trades"].empty for c in wf_curves):
        import pandas as _pd
        frames = []
        for i, c in enumerate(wf_curves):
            t = c.get("trades")
            if t is not None and not t.empty:
                tt = t.copy()
                tt["window"] = i
                frames.append(tt)
        if frames:
            df_trades = _pd.concat(frames, ignore_index=True)
            df_trades.to_parquet(trades_dir / f"iter_{iter_id:04d}.parquet",
                                 compression="zstd", index=False)
    elif curves is not None and curves.get("trades") is not None and not curves["trades"].empty:
        curves["trades"].to_parquet(trades_dir / f"iter_{iter_id:04d}.parquet",
                                     compression="zstd", index=False)

    cutoffs: list[str] = []
    if wf_curves:
        import pandas as _pd
        frames = [_curve_to_df(c, window=i) for i, c in enumerate(wf_curves)]
        df_curve = _pd.concat(frames, ignore_index=True)
        equity_path = equity_dir / f"iter_{iter_id:04d}.parquet"
        df_curve.to_parquet(equity_path, compression="zstd", index=False)
        cutoffs = [str(c["split_cutoff"]) for c in wf_curves]
        (equity_dir / f"iter_{iter_id:04d}.json").write_text(
            json.dumps({"split_cutoffs": cutoffs}, indent=2)
        )
    elif curves is not None:
        df_curve = _curve_to_df(curves)
        equity_path = equity_dir / f"iter_{iter_id:04d}.parquet"
        df_curve.to_parquet(equity_path, compression="zstd", index=False)
        cutoffs = [str(curves["split_cutoff"])]
        (equity_dir / f"iter_{iter_id:04d}.json").write_text(
            json.dumps({"split_cutoff": cutoffs[0], "split_cutoffs": cutoffs}, indent=2)
        )

    # Composite: WF aggregate when we have multiple windows, else the old
    # single-OOS rule. Both keep the n_trades=0 -> -inf safety.
    wf_block = result.get("walk_forward") or {}
    wf_windows = wf_block.get("windows") or []
    wf_oos = [w.get("oos", {}) for w in wf_windows]
    if wf_oos and not error:
        composite, wf_agg = aggregate_wf_composite(
            wf_oos,
            dd_penalty=cfg.dd_penalty,
            min_trades=cfg.min_trades,
            low_trades_penalty=cfg.low_trades_penalty,
            stability_penalty=cfg.stability_penalty,
        )
        oos = {
            "sharpe": wf_agg["mean_sharpe"],
            "max_dd": wf_agg["worst_max_dd"],
            "n_trades": int(wf_agg["mean_n_trades"]),
        }
    else:
        oos = result.get("main", {}).get("oos", {}) or {}
        wf_agg = None
        composite = composite_score(
            oos,
            dd_penalty=cfg.dd_penalty,
            min_trades=cfg.min_trades,
            low_trades_penalty=cfg.low_trades_penalty,
        ) if oos and not error else float("-inf")

    # DSR: concatenate OOS returns across whatever windows ran, run deflated
    # Sharpe with n_trials = iter_id. We pull trial sharpes from history for a
    # tighter (less conservative) std estimate when we have enough data.
    dsr_value = 0.0
    try:
        import pandas as _pd
        if wf_curves:
            oos_returns_concat = _pd.concat(
                [c["oos_returns"] for c in wf_curves if c.get("oos_returns") is not None]
            ).sort_index()
        elif curves is not None and curves.get("oos_returns") is not None:
            oos_returns_concat = curves["oos_returns"]
        else:
            oos_returns_concat = _pd.Series(dtype="float64")

        if len(oos_returns_concat.dropna()) >= 30 and not error:
            # Trial Sharpes for the variance estimate. Pull from history.jsonl.
            history_path = runs / "history.jsonl"
            trial_sharpes: list[float] = []
            if history_path.exists():
                with history_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                            sh = (row.get("metrics_oos") or {}).get("sharpe")
                            if sh is not None:
                                trial_sharpes.append(float(sh))
                        except Exception:
                            pass
            dsr_value = deflated_sharpe(
                oos_returns_concat,
                n_trials=iter_id,
                trial_sharpes=trial_sharpes if len(trial_sharpes) >= 2 else None,
                tf=cfg.tf,
            )
    except Exception:
        dsr_value = 0.0

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
            "wf_aggregate": wf_agg,
            "dsr": dsr_value,
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
                    "wf_aggregate": wf_agg,
                    "dsr": dsr_value,
                    "note": note + " [adopted as initial baseline]",
                    "saved_at": finished,
                }
                _save_best(runs, new_best, strategy_file)
                verdict = "BASELINE"

    # Tear sheet: only when we accepted the iter (so we don't accumulate noise
    # for reverted attempts). Reverted strategy files are already restored;
    # there's no useful HTML to make for them.
    if verdict in ("KEEP", "BASELINE") and equity_path is not None:
        try:
            import pandas as _pd
            ts_dir = runs / "tearsheets"
            ts_dir.mkdir(exist_ok=True)
            eq_df = _pd.read_parquet(equity_path)
            trades_pq = runs / "trades" / f"iter_{iter_id:04d}.parquet"
            tr_df = _pd.read_parquet(trades_pq) if trades_pq.exists() else None
            history_so_far = []
            if (runs / "history.jsonl").exists():
                with (runs / "history.jsonl").open("r", encoding="utf-8") as fh:
                    for ln in fh:
                        try:
                            history_so_far.append(json.loads(ln))
                        except Exception:
                            pass
            iter_data_for_ts = {
                "iter": iter_id,
                "verdict": verdict,
                "composite": composite,
                "dsr": dsr_value,
                "params": result.get("params"),
                "symbols": result.get("symbols"),
                "period": result.get("period"),
                "tf": result.get("tf"),
                "metrics": result.get("main"),
                "wf_aggregate": wf_agg,
            }
            tearsheet_mod.render_to_file(
                iter_data_for_ts, eq_df, tr_df, history_so_far,
                ts_dir / f"iter_{iter_id:04d}.html",
            )
        except Exception as e:
            traceback.print_exception(type(e), e, e.__traceback__)

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
        "wf_aggregate": wf_agg,
        "dsr": dsr_value,
        "audit": audit_summary,
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
        "dsr": round(dsr_value, 4),
        "error": error,
    }
    return summary


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-10-01")
    ap.add_argument("--tf", default=None,
                    help="Decision timeframe. If omitted, read strategy.py:DEFAULT_TF, "
                         "fall back to '1h'.")
    ap.add_argument("--walk", type=int, default=4,
                    help="Number of walk-forward windows (1 == single train/OOS split).")
    ap.add_argument("--audit", choices=["once", "always", "never"], default="once",
                    help="Lookahead audit: once (default, when strategy.py changed), "
                         "always (every iter), never (skip — only for trusted strategies).")
    ap.add_argument("--audit-k", type=int, default=12,
                    help="Number of per-bar perturbations in the audit.")
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
        audit_mode=args.audit,
        audit_k=args.audit_k,
    )
    out = run_one(Path(args.strategy_dir), cfg, note=args.note)
    print(json.dumps(out, indent=2))
    if out.get("error"):
        sys.exit(2)


if __name__ == "__main__":
    main()
