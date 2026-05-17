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
from harness import env as env_mod
from harness import lookahead as la
from harness import tearsheet as tearsheet_mod
from harness.metrics import aggregate_wf_composite, composite_score
from harness.stats import deflated_sharpe
from datafeed.loader import load_many


# --------------------------------------------------------------------------- #
@dataclass
class IterationConfig:
    # Default split (see AGENTS.md):
    #   train+val = Jan 2024 .. Dec 2025  (used for composite score & keep/revert)
    #   holdout   = Jan 2026 .. Apr 2026  (NEVER touched here — see runner.holdout)
    period_start: str = "2024-01-01"
    period_end: str = "2026-01-01"
    # tf=None means "read from strategy.py:DEFAULT_TF, fall back to 1h".
    # CLI --tf or programmatic override forces a specific value regardless.
    tf: str | None = None
    walk_windows: int = 4   # 4 walk-forward windows ~5mo each on the default period
    # Embargo between train and OOS in each split. Default 1d:
    # at 1h TF this is 24 bars, covering most strategies' rolling-indicator
    # lookbacks; at 5m TF it's 288 bars (covers a 200-bar SMA). Set to "0"
    # to disable. See harness/splits.py for the rationale.
    embargo: str = "1D"
    # Lookback padding: pre-load history before each WF window's
    # train_start so rolling-indicator strategies (vol, momentum,
    # supertrend) have a warm history at bar 1 instead of wasting
    # the first ~lookback bars on signal-blind warmup. Mirrors what
    # a live operator does. Default 60D covers most reasonable
    # rolling lookbacks; strategies that use longer (90d+ momentum)
    # should override.
    lookback: str = "60D"
    # Cost model:
    #   "static" — legacy flat 1bp slippage (backwards-compatible).
    #   "spread" — per-bar half-spread from data/meta/spreads/.
    #   "full"   — spread + size-impact via depth proxy.
    # Static is the default to preserve baseline comparability with
    # pre-existing best.json. Switch to "spread" or "full" deliberately
    # when the operator is ready to re-establish baselines.
    cost_model: str = "static"
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
    # Walk-forward training-window mode:
    #   False (default) = disjoint tiles, each window trained on its own
    #     slice of length ``total / n_windows`` (legacy behaviour).
    #   True            = expanding window, every window's train_start
    #     is period_start so the training history grows monotonically.
    #     Closer to the live operator's "use all history I have" stance,
    #     better for slow-indicator strategies that need ≥1 year of bars
    #     to warm up. OOS slicing and embargo are identical between modes.
    walk_expanding: bool = False


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

    cs_deps = (report.extra or {}).get("cross_symbol_dependencies", [])
    audit_log.write_text(json.dumps({
        "sha256": cur_hash,
        "passed": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "k": report.k_perturbations,
        "n_symbols": report.n_symbols_tested,
        "n_bars": report.n_bars_tested,
        "duration_seconds": report.duration_seconds,
        "notes": report.notes,
        "cross_symbol_dependencies": cs_deps,
    }, indent=2), encoding="utf-8")
    return False, None, {
        "audit": "passed",
        "sha256": cur_hash,
        "k_perturbations": report.k_perturbations,
        "duration_seconds": report.duration_seconds,
        # Informational — basket strategies expect this to be non-empty.
        # Per-symbol independent strategies showing pairs here likely
        # have a basket-level leak (e.g. accidentally reading another
        # symbol's data when computing this one's signal).
        "n_cross_symbol_dependencies": len(cs_deps),
        "cross_symbol_dependencies": cs_deps,
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
            "env": env_mod.capture(),
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
        from harness.costs import CostModel
        cost_kwargs = {
            "static": {},
            "spread": {"use_dynamic_spread": True},
            "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
        }[cfg.cost_model]
        costs = CostModel(**cost_kwargs)
        result = bt.run(strategy_dir, cfg.period_start, cfg.period_end,
                        tf=cfg.tf, walk_windows=cfg.walk_windows,
                        embargo=cfg.embargo, costs=costs,
                        lookback=cfg.lookback,
                        return_curves=True,
                        seed_hint=iter_id,
                        walk_expanding=cfg.walk_expanding)
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

    # ---- Research-integrity layer: bootstrap p-values + session overfit ----
    # Persisted into the history row as `research_stats` so the UI can plot
    # null distributions and the operator can scan p-values at a glance.
    # All best-effort — never fail the iter on a bug here.
    research_stats: dict | None = None
    try:
        from harness import bootstrap as _boot
        from harness import multiple_testing as _mt
        from harness import pbo as _pbo
        from harness.metrics import _resolve_periods_per_year

        if not error and 'oos_returns_concat' in locals() \
                and len(oos_returns_concat.dropna()) >= 30:
            # 1) Per-iter null-distribution p-values (block + permutation).
            pv = _boot.both_pvalues(oos_returns_concat, n_boot=1000, tf=cfg.tf)

            # 2) Harvey-Liu haircut Sharpe using iter_id as n_trials.
            ppy = _resolve_periods_per_year(oos_returns_concat.dropna().index, cfg.tf)
            hl = _mt.haircut_sharpe(
                sharpe_ann=oos.get("sharpe", 0.0),
                n_periods=int(len(oos_returns_concat.dropna())),
                n_trials=iter_id,
                periods_per_year=ppy,
            )

            # 3) Session overfit stats from history's (train_sharpe, oos_sharpe)
            #    pairs accumulated so far (including this iter).
            history_path = runs / "history.jsonl"
            train_sh: list[float] = []
            oos_sh: list[float] = []
            if history_path.exists():
                with history_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                            tr = (row.get("metrics_train") or {}).get("sharpe")
                            os_ = (row.get("metrics_oos") or {}).get("sharpe")
                            if tr is not None and os_ is not None:
                                train_sh.append(float(tr))
                                oos_sh.append(float(os_))
                        except Exception:
                            pass
            # Include this iter's pair (history.jsonl is written after).
            cur_train = (result.get("main") or {}).get("train", {}).get("sharpe")
            if cur_train is not None and oos.get("sharpe") is not None:
                train_sh.append(float(cur_train))
                oos_sh.append(float(oos.get("sharpe", 0.0)))
            session_overfit = _pbo.session_overfit_stats(train_sh, oos_sh)

            # 4) Trial Sharpe summary for the UI histogram (driven by OOS).
            ts_sum = _mt.trial_sharpe_summary(oos_sh)

            research_stats = {
                "bootstrap": pv,
                "haircut_sharpe": hl,
                "session_overfit": session_overfit,
                "trial_sharpes": ts_sum,
            }
    except Exception as e:
        traceback.print_exception(type(e), e, e.__traceback__)
        research_stats = None

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
            "env": env_mod.capture(),
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
                    "env": env_mod.capture(),
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
                "env": env_mod.capture(),
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
        "research_stats": research_stats,
        "env": env_mod.capture(),
        "note": note,
        "error": error,
    }
    _append_history(runs, row)

    # Capacity: report worst-window max participation. Only flag in
    # summary when above the threshold (5% by default in capacity_metrics)
    # so the iter loop noise stays low; the full numbers are in
    # history.jsonl / wf_aggregate / metrics_oos for inspection.
    cap_max = (wf_agg or {}).get("max_participation_pct") if wf_agg else \
              oos.get("max_participation_pct")
    cap_n_over = (wf_agg or {}).get("n_trades_over_threshold") if wf_agg else \
                 oos.get("n_trades_over_threshold")
    capacity_warning = None
    if cap_max is not None and cap_max > 5.0:
        capacity_warning = (
            f"max_participation_pct={cap_max:.1f}% (>5%); "
            f"{cap_n_over or 0} trade(s) over threshold. "
            f"Static slippage under-charges; consider --cost-model full "
            f"or smaller size."
        )

    summary = {
        "iter": iter_id,
        "verdict": verdict,
        "composite": round(composite, 4) if composite != float("-inf") else None,
        "best_before": round(best_score, 4) if best_score != float("-inf") else None,
        "oos_sharpe": round(oos.get("sharpe", 0.0), 4),
        "oos_max_dd": round(oos.get("max_dd", 0.0), 4),
        "oos_n_trades": oos.get("n_trades", 0),
        "dsr": round(dsr_value, 4),
        "max_participation_pct": (round(cap_max, 2) if cap_max is not None else None),
        "capacity_warning": capacity_warning,
        "error": error,
    }

    # Research-integrity highlights for the CLI (full details in
    # row["research_stats"] -> history.jsonl). Keeps the per-iter
    # summary scannable.
    if research_stats:
        rs = {}
        bp = ((research_stats.get("bootstrap") or {}).get("block") or {}) \
                .get("p_values") or {}
        if bp:
            rs["p_sharpe_block"] = round(bp.get("sharpe", 1.0), 4)
            rs["p_max_dd_block"] = round(bp.get("max_dd", 1.0), 4)
        hl = (research_stats.get("haircut_sharpe") or {}).get("bhy") or {}
        if hl:
            rs["bhy_haircut_sharpe"] = round(hl.get("sharpe", 0.0), 4)
            rs["bhy_haircut_pct"] = round(hl.get("haircut_pct", 0.0), 4)
        so = research_stats.get("session_overfit") or {}
        if so.get("spearman_is_oos") is not None:
            rs["session_spearman_is_oos"] = round(so["spearman_is_oos"], 4)
        if so.get("logit_overfit") is not None:
            rs["session_logit_overfit"] = round(so["logit_overfit"], 4)
        if rs:
            summary["research"] = rs

    # vs_best: deltas of all key metrics vs the prior best. Informative
    # only — doesn't drive keep/revert. Surfaces "composite +0.05 but
    # MaxDD +3% and PF -0.2" kind of trade-offs that the headline
    # composite hides. Skipped on first iter (no prior best).
    if best is not None and not error and composite != float("-inf"):
        try:
            cur_wf = wf_agg or {}
            best_wf = best.get("wf_aggregate") or {}
            best_oos_legacy = ((best.get("metrics") or {}).get("oos")) or {}

            def _pick_cur(field_wf: str, field_legacy: str | None = None) -> float | None:
                if cur_wf.get(field_wf) is not None:
                    return cur_wf.get(field_wf)
                if field_legacy is not None:
                    return oos.get(field_legacy)
                return None

            def _pick_best(field_wf: str, field_legacy: str | None = None) -> float | None:
                if best_wf.get(field_wf) is not None:
                    return best_wf.get(field_wf)
                if field_legacy is not None:
                    return best_oos_legacy.get(field_legacy)
                return None

            def _delta(cur, prev, ndigits: int = 4) -> float | None:
                if cur is None or prev is None:
                    return None
                try:
                    return round(float(cur) - float(prev), ndigits)
                except Exception:
                    return None

            # (current_field_wf, current_field_legacy, best_field_wf, best_field_legacy, label)
            fields = [
                ("mean_sharpe", "sharpe", "mean_sharpe", "sharpe", "sharpe"),
                ("worst_max_dd", "max_dd", "worst_max_dd", "max_dd", "max_dd"),
                ("mean_n_trades", "n_trades", "mean_n_trades", "n_trades", "n_trades"),
                ("mean_profit_factor", "profit_factor",
                 "mean_profit_factor", "profit_factor", "profit_factor"),
                ("mean_expectancy", "expectancy",
                 "mean_expectancy", "expectancy", "expectancy"),
                ("mean_information_ratio", "information_ratio",
                 "mean_information_ratio", "information_ratio",
                 "information_ratio"),
                ("worst_cvar_95", "cvar_95",
                 "worst_cvar_95", "cvar_95", "cvar_95"),
            ]

            vs: dict[str, dict] = {
                "composite": {
                    "cur": (round(composite, 4) if composite != float("-inf")
                            else None),
                    "prev": (round(best_score, 4) if best_score != float("-inf")
                             else None),
                    "delta": _delta(composite, best_score),
                },
                "dsr": {
                    "cur": round(dsr_value, 4),
                    "prev": (round(float(best.get("dsr")), 4)
                             if best.get("dsr") is not None else None),
                    "delta": _delta(dsr_value, best.get("dsr")),
                },
            }
            for cw, cl, bw, bl, label in fields:
                cur_v = _pick_cur(cw, cl)
                prev_v = _pick_best(bw, bl)
                vs[label] = {
                    "cur": (round(float(cur_v), 4) if cur_v is not None else None),
                    "prev": (round(float(prev_v), 4) if prev_v is not None else None),
                    "delta": _delta(cur_v, prev_v),
                }
            summary["vs_best"] = {
                "best_iter": best.get("iter"),
                "fields": vs,
            }
        except Exception:
            # vs_best is informative — never block iteration on a bug here.
            pass

    # Rich diagnostics — best-effort. Surfaces per-window shape, DSR
    # trajectory, monthly streaks, fat-tail checks, and one-line flags
    # the agent should scan after each iter. See harness/diagnostics.py.
    try:
        from harness import diagnostics as diag_mod
        summary["diagnostics"] = diag_mod.build_diagnostics(
            iter_id, runs, summary, result, dsr_value,
        )
        # Persist for retroactive review.
        diag_dir = runs / "diagnostics"
        diag_dir.mkdir(exist_ok=True)
        (diag_dir / f"iter_{iter_id:04d}.json").write_text(
            json.dumps(summary["diagnostics"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        # Diagnostics are optional context; don't fail the iter on a bug here.
        traceback.print_exception(type(e), e, e.__traceback__)
    return summary


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--tf", default=None,
                    help="Decision timeframe. If omitted, read strategy.py:DEFAULT_TF, "
                         "fall back to '1h'.")
    ap.add_argument("--walk", type=int, default=4,
                    help="Number of walk-forward windows (1 == single train/OOS split).")
    ap.add_argument("--embargo", default="1D",
                    help="Gap between train and OOS in each split, parseable "
                         "as pd.Timedelta (e.g. '1D', '12h', '144min'). "
                         "Default: 1D. Set to '0' to disable. "
                         "See harness/splits.py for the rationale.")
    ap.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static",
                    help="static (default) = legacy flat 1bp slippage. "
                         "spread = per-bar half-spread from saved estimates. "
                         "full = spread + size-impact. Switching changes "
                         "score scale; baselines will need re-establishing.")
    ap.add_argument("--lookback", default="60D",
                    help="Pre-load history before each WF window's "
                         "train_start so rolling-indicator strategies are "
                         "warm at bar 1. Default: 60D. Set to '0' to "
                         "disable. See harness/backtest.py.")
    ap.add_argument("--audit", choices=["once", "always", "never"], default="once",
                    help="Lookahead audit: once (default, when strategy.py changed), "
                         "always (every iter), never (skip — only for trusted strategies).")
    ap.add_argument("--audit-k", type=int, default=12,
                    help="Number of per-bar perturbations in the audit.")
    ap.add_argument("--dd-penalty", type=float, default=0.5)
    ap.add_argument("--min-trades", type=int, default=50)
    ap.add_argument("--epsilon", type=float, default=0.01)
    ap.add_argument("--expanding-wf", action="store_true",
                    help="Use expanding-window walk-forward (each window "
                         "trains on all data from period_start through its "
                         "cutoff). Default is disjoint-tile mode.")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    cfg = IterationConfig(
        period_start=args.start,
        period_end=args.end,
        tf=args.tf,
        walk_windows=args.walk,
        embargo=args.embargo,
        lookback=args.lookback,
        cost_model=args.cost_model,
        dd_penalty=args.dd_penalty,
        min_trades=args.min_trades,
        epsilon=args.epsilon,
        audit_mode=args.audit,
        audit_k=args.audit_k,
        walk_expanding=args.expanding_wf,
    )
    # Force UTF-8 on stdout so the diagnostics flag glyphs (✓/⚠/✗/ℹ)
    # render readable instead of escaped \uXXXX. Windows cp1252 console
    # is the typical caller; the buffer wrap is a no-op elsewhere.
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    out = run_one(Path(args.strategy_dir), cfg, note=args.note)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out.get("error"):
        sys.exit(2)


if __name__ == "__main__":
    main()
