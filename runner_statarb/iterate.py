"""One iteration of the stat-arb auto-research loop.

Mirrors the runner.iterate pattern but adapted to the stat-arb engine:
  1. Snapshot strategy.py → runs/last_attempt.py
  2. Run the stat-arb lookahead audit (find_structures + trade_basket)
  3. If audit fails → record LOOKAHEAD_BUG, revert to best, exit.
  4. Otherwise run harness_statarb.backtest.run_statarb (walk-forward)
     with return_curves=True so we get equity/trades for the dashboard.
  5. Compute statarb composite (mean − stability_penalty · std per-window)
     AND the standard research-integrity panel: DSR (Bailey & López
     de Prado), bootstrap p-values (block + permutation), Harvey-Liu
     haircut Sharpe, session overfit stats. Same functions runner.iterate
     uses so the dashboard's research panel works unchanged.
  6. Write equity / trades parquets and a rendered tearsheet HTML.
  7. Compare against best.json; KEEP if improved by ≥ epsilon, else REVERT.
  8. Append a row to history.jsonl, print a verdict summary.

State lives in strategies_statarb/<name>/runs/. Stateless across calls.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from harness import env as env_mod
from harness import tearsheet as tearsheet_mod
from harness_statarb import backtest as sa_bt
from harness_statarb import lookahead as sa_la
from harness_statarb.metrics import (
    aggregate_wf_statarb_composite,
    statarb_composite_score,
)


@dataclass
class IterationConfig:
    period_start: str = "2024-01-01"
    period_end: str = "2026-01-01"
    tf: str | None = None
    walk_windows: int = 4
    embargo: str = "1D"
    lookback: str = "120D"
    cost_model: str = "static"
    stability_penalty: float = 0.5
    dd_penalty: float = 0.5
    min_trades: int = 30
    low_trades_penalty: float = 0.5
    min_time_in_position: float = 20.0
    time_in_position_penalty: float = 1.0
    survival_threshold: float = 0.5
    survival_weight: float = 1.0
    half_life_weight: float = 0.5
    target_half_life_ratio: float = 0.25
    epsilon: float = 0.01
    audit_mode: str = "once"
    audit_start: str = "2024-03-01"
    audit_end: str = "2024-09-01"
    walk_workers: int = 1


def _runs_dir(strategy_dir: Path) -> Path:
    d = strategy_dir / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_best(runs: Path) -> dict | None:
    p = runs / "best.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_best(runs: Path, best: dict, strategy_file: Path) -> None:
    (runs / "best.json").write_text(json.dumps(best, indent=2, default=str), encoding="utf-8")
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
    """Lookahead audit pre-flight. Returns (skip_backtest, error_msg, summary)."""
    strategy_file = strategy_dir / "strategy.py"
    cur_hash = sa_la.file_sha256(strategy_file)
    audit_log = runs / "last_audit.json"

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

    try:
        rep = sa_la.audit_strategy(
            strategy_dir,
            period_start=cfg.audit_start,
            period_end=cfg.audit_end,
            tf=cfg.tf,
        )
    except (sa_la.DeterminismError, sa_la.LookaheadError) as e:
        is_la = isinstance(e, sa_la.LookaheadError)
        summary = {
            "audit": "FAILED",
            "sha256": cur_hash,
            "error_type": type(e).__name__,
            "message": str(e),
        }
        if is_la:
            summary["stage"] = e.stage
            summary["detail"] = e.detail
        audit_log.write_text(json.dumps({
            "sha256": cur_hash, "passed": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            **summary,
        }, indent=2, default=str), encoding="utf-8")
        return True, str(e), summary

    audit_log.write_text(json.dumps({
        "sha256": cur_hash, "passed": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "n_baskets_fitted": rep.n_baskets_fitted,
        "n_perturbations": rep.n_perturbations,
        "notes": rep.notes,
        "extra": rep.extra,
    }, indent=2, default=str), encoding="utf-8")
    return False, None, {
        "audit": "passed",
        "sha256": cur_hash,
        "n_baskets_fitted": rep.n_baskets_fitted,
        "n_perturbations": rep.n_perturbations,
    }


def _curve_to_df(c: dict, window: int | None = None) -> pd.DataFrame:
    eq = c["equity"]
    cols = {
        "timestamp": eq.index,
        "equity": eq.values,
    }
    bench = c.get("benchmark")
    if bench is not None:
        cols["benchmark"] = bench.reindex(eq.index).values
    if c.get("raw_equity") is not None:
        cols["raw_equity"] = c["raw_equity"].reindex(eq.index).values
    if c.get("funding_cashflow") is not None:
        cols["funding_cashflow"] = c["funding_cashflow"].reindex(eq.index).values
    df = pd.DataFrame(cols)
    if window is not None:
        df["window"] = window
    return df


def _write_basket_events(main_res: dict, wf_windows: list[dict],
                          runs: Path, iter_id: int) -> Path | None:
    """Persist basket lifecycle events as parquet.

    Strips `basket_events_df` off each result dict (so it doesn't end up
    in history.jsonl as an unserializable DataFrame) and writes a single
    parquet with a `window` column for analysis.
    """
    baskets_dir = runs / "baskets"
    baskets_dir.mkdir(exist_ok=True)
    frames = []
    # Per-window WF runs.
    for i, w in enumerate(wf_windows):
        df = w.pop("basket_events_df", None) if isinstance(w, dict) else None
        if df is not None and not df.empty:
            df = df.copy()
            df["window"] = i
            frames.append(df)
    # Single-split main (no WF, or WF + single-split parallel).
    main_df = main_res.pop("basket_events_df", None) if isinstance(main_res, dict) else None
    if not frames and main_df is not None and not main_df.empty:
        main_df = main_df.copy()
        main_df["window"] = 0
        frames.append(main_df)
    if not frames:
        return None
    df_all = pd.concat(frames, ignore_index=True)
    out = baskets_dir / f"iter_{iter_id:04d}.parquet"
    df_all.to_parquet(out, compression="zstd", index=False)
    return out


def _write_curves(curves: dict | None, wf_curves: list[dict] | None,
                  runs: Path, iter_id: int) -> tuple[Path | None, list[str]]:
    """Write equity + trades parquets. Returns (equity_path, split_cutoffs)."""
    equity_dir = runs / "equity"
    equity_dir.mkdir(exist_ok=True)
    trades_dir = runs / "trades"
    trades_dir.mkdir(exist_ok=True)

    equity_path: Path | None = None
    cutoffs: list[str] = []

    if wf_curves:
        frames = [_curve_to_df(c, window=i) for i, c in enumerate(wf_curves)]
        df_curve = pd.concat(frames, ignore_index=True)
        equity_path = equity_dir / f"iter_{iter_id:04d}.parquet"
        df_curve.to_parquet(equity_path, compression="zstd", index=False)
        cutoffs = [str(c["split_cutoff"]) for c in wf_curves]
        (equity_dir / f"iter_{iter_id:04d}.json").write_text(
            json.dumps({"split_cutoffs": cutoffs}, indent=2),
            encoding="utf-8",
        )
        # Trades — concat across windows with `window` column.
        trade_frames = []
        for i, c in enumerate(wf_curves):
            t = c.get("trades")
            if t is not None and not t.empty:
                tt = t.copy()
                tt["window"] = i
                trade_frames.append(tt)
        if trade_frames:
            pd.concat(trade_frames, ignore_index=True).to_parquet(
                trades_dir / f"iter_{iter_id:04d}.parquet",
                compression="zstd", index=False,
            )
    elif curves is not None:
        df_curve = _curve_to_df(curves)
        equity_path = equity_dir / f"iter_{iter_id:04d}.parquet"
        df_curve.to_parquet(equity_path, compression="zstd", index=False)
        cutoffs = [str(curves["split_cutoff"])]
        (equity_dir / f"iter_{iter_id:04d}.json").write_text(
            json.dumps({"split_cutoff": cutoffs[0], "split_cutoffs": cutoffs}, indent=2),
            encoding="utf-8",
        )
        t = curves.get("trades")
        if t is not None and not t.empty:
            t.to_parquet(trades_dir / f"iter_{iter_id:04d}.parquet",
                         compression="zstd", index=False)
    return equity_path, cutoffs


def _stitched_oos_returns(curves: dict | None, wf_curves: list[dict] | None) -> pd.Series:
    """Concatenate OOS returns across whatever windows ran."""
    if wf_curves:
        parts = [c["oos_returns"] for c in wf_curves if c.get("oos_returns") is not None]
        if parts:
            return pd.concat(parts).sort_index()
    elif curves is not None and curves.get("oos_returns") is not None:
        return curves["oos_returns"]
    return pd.Series(dtype="float64")


def _compute_research_stats(oos_returns: pd.Series, oos_summary: dict,
                            train_oos_pairs: list[tuple[float, float]],
                            iter_id: int, tf: str | None) -> dict | None:
    """Bootstrap p-values + haircut Sharpe + session overfit + trial summary.

    Mirrors runner.iterate.run_one's research_stats block so the
    dashboard's research panel renders identically for stat-arb iters.
    """
    if oos_returns is None or len(oos_returns.dropna()) < 30:
        return None
    try:
        from harness import bootstrap as _boot
        from harness import multiple_testing as _mt
        from harness import pbo as _pbo
        from harness.metrics import _resolve_periods_per_year

        pv = _boot.both_pvalues(oos_returns, n_boot=1000, tf=tf)
        ppy = _resolve_periods_per_year(oos_returns.dropna().index, tf)
        hl = _mt.haircut_sharpe(
            sharpe_ann=oos_summary.get("sharpe", 0.0),
            n_periods=int(len(oos_returns.dropna())),
            n_trials=iter_id,
            periods_per_year=ppy,
        )
        train_sh = [p[0] for p in train_oos_pairs]
        oos_sh = [p[1] for p in train_oos_pairs]
        session_overfit = _pbo.session_overfit_stats(train_sh, oos_sh)
        ts_sum = _mt.trial_sharpe_summary(oos_sh)
        return {
            "bootstrap": pv,
            "haircut_sharpe": hl,
            "session_overfit": session_overfit,
            "trial_sharpes": ts_sum,
        }
    except Exception as e:
        traceback.print_exception(type(e), e, e.__traceback__)
        return None


def _compute_dsr(oos_returns: pd.Series, runs: Path, iter_id: int,
                 cur_oos_sharpe: float, tf: str | None) -> float:
    """Deflated Sharpe: pulls prior-iter Sharpes from history.jsonl for tighter
    variance estimate; falls back to unit-variance trials if n<2."""
    try:
        from harness.stats import deflated_sharpe
    except ImportError:
        return 0.0
    if len(oos_returns.dropna()) < 30:
        return 0.0
    trial_sharpes: list[float] = []
    history_path = runs / "history.jsonl"
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
    trial_sharpes.append(float(cur_oos_sharpe or 0.0))
    return float(deflated_sharpe(
        oos_returns,
        n_trials=iter_id,
        trial_sharpes=trial_sharpes if len(trial_sharpes) >= 2 else None,
        tf=tf,
    ))


def _gather_train_oos_pairs(runs: Path, cur_train_sharpe: float | None,
                            cur_oos_sharpe: float | None) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    history_path = runs / "history.jsonl"
    if history_path.exists():
        with history_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    tr = (row.get("metrics_train") or {}).get("sharpe")
                    os_ = (row.get("metrics_oos") or {}).get("sharpe")
                    if tr is not None and os_ is not None:
                        pairs.append((float(tr), float(os_)))
                except Exception:
                    pass
    if cur_train_sharpe is not None and cur_oos_sharpe is not None:
        pairs.append((float(cur_train_sharpe), float(cur_oos_sharpe)))
    return pairs


def run_one(strategy_dir: Path, cfg: IterationConfig, note: str = "") -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    runs = _runs_dir(strategy_dir)
    strategy_file = strategy_dir / "strategy.py"
    if not strategy_file.exists():
        raise FileNotFoundError(strategy_file)

    iter_id = _next_iter_id(runs)
    shutil.copy2(strategy_file, runs / "last_attempt.py")

    if cfg.tf is None:
        mod_for_tf = sa_bt.load_statarb_strategy(strategy_dir)
        cfg = dataclasses.replace(cfg, tf=getattr(mod_for_tf, "DEFAULT_TF", "1h"))

    started = datetime.now(timezone.utc).isoformat()

    # ---- audit pre-flight ----
    audit_block, audit_err, audit_summary = _run_audit(runs, strategy_dir, cfg)
    if audit_block:
        finished = datetime.now(timezone.utc).isoformat()
        best_now = _load_best(runs)
        best_file = runs / "best_strategy.py"
        if best_file.exists() and best_now is not None:
            shutil.copy2(best_file, strategy_file)
            verdict = "LOOKAHEAD_BUG"
        else:
            verdict = "LOOKAHEAD_BUG_NO_BASELINE"
        row = {
            "iter": iter_id, "started": started, "finished": finished,
            "verdict": verdict, "composite": None,
            "best_before": (best_now or {}).get("composite"),
            "params": None, "metrics_oos": {}, "metrics_train": {},
            "walk_forward": None, "statarb": None,
            "dsr": 0.0, "research_stats": None,
            "audit": audit_summary, "env": env_mod.capture(),
            "note": note, "error": audit_err,
            "mode": "statarb",
        }
        _append_history(runs, row)
        return {
            "iter": iter_id, "verdict": verdict, "composite": None,
            "best_before": (best_now or {}).get("composite"),
            "error": audit_err, "audit": audit_summary,
        }

    # ---- backtest ----
    error = None
    try:
        result = sa_bt.run_statarb(
            strategy_dir, cfg.period_start, cfg.period_end,
            tf=cfg.tf, walk_windows=cfg.walk_windows,
            embargo=cfg.embargo, lookback=cfg.lookback,
            return_curves=True, seed_hint=iter_id,
            walk_workers=cfg.walk_workers,
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        result = {"main": {"train": {}, "oos": {}, "statarb": {}}}
    finished = datetime.now(timezone.utc).isoformat()

    # ---- curves (equity + trades parquets) ----
    curves = result.pop("curves", None) if isinstance(result, dict) else None
    wf_curves = None
    if isinstance(result.get("walk_forward"), dict):
        wf_curves = result["walk_forward"].pop("curves", None)
    equity_path, cutoffs = _write_curves(curves, wf_curves, runs, iter_id)

    # ---- basket lifecycle parquet ----
    wf_windows_for_baskets = (result.get("walk_forward") or {}).get("windows") or []
    baskets_path = _write_basket_events(
        result.get("main") or {}, wf_windows_for_baskets, runs, iter_id,
    )

    # ---- composite ----
    refit_bars = int((result.get("params") or {}).get("refit_freq_bars", 168))
    wf_windows = (result.get("walk_forward") or {}).get("windows") or []
    if wf_windows and not error:
        composite, wf_agg = aggregate_wf_statarb_composite(
            wf_windows,
            refit_freq_bars=refit_bars,
            stability_penalty=cfg.stability_penalty,
            dd_penalty=cfg.dd_penalty,
            min_trades=cfg.min_trades,
            low_trades_penalty=cfg.low_trades_penalty,
            min_time_in_position=cfg.min_time_in_position,
            time_in_position_penalty=cfg.time_in_position_penalty,
            survival_threshold=cfg.survival_threshold,
            survival_weight=cfg.survival_weight,
            half_life_weight=cfg.half_life_weight,
            target_half_life_ratio=cfg.target_half_life_ratio,
        )
        oos = result.get("main", {}).get("oos", {}) or {}
        statarb_main = result.get("main", {}).get("statarb", {}) or {}
    else:
        oos = result.get("main", {}).get("oos", {}) or {}
        statarb_main = result.get("main", {}).get("statarb", {}) or {}
        wf_agg = None
        if oos and not error:
            composite = statarb_composite_score(
                oos, statarb_main, refit_freq_bars=refit_bars,
                dd_penalty=cfg.dd_penalty,
                min_trades=cfg.min_trades,
                low_trades_penalty=cfg.low_trades_penalty,
                min_time_in_position=cfg.min_time_in_position,
                time_in_position_penalty=cfg.time_in_position_penalty,
                survival_threshold=cfg.survival_threshold,
                survival_weight=cfg.survival_weight,
                half_life_weight=cfg.half_life_weight,
                target_half_life_ratio=cfg.target_half_life_ratio,
            )
        else:
            composite = float("-inf")

    # ---- DSR + research_stats ----
    oos_returns_concat = _stitched_oos_returns(curves, wf_curves) if not error \
                                                                  else pd.Series(dtype="float64")
    dsr_value = 0.0
    research_stats = None
    if not error:
        dsr_value = _compute_dsr(oos_returns_concat, runs, iter_id,
                                  oos.get("sharpe", 0.0), cfg.tf)
        cur_train = (result.get("main") or {}).get("train", {}).get("sharpe")
        train_oos_pairs = _gather_train_oos_pairs(runs, cur_train, oos.get("sharpe"))
        research_stats = _compute_research_stats(
            oos_returns_concat, oos, train_oos_pairs, iter_id, cfg.tf,
        )

    # ---- keep / revert ----
    best = _load_best(runs)
    best_score = best["composite"] if best else float("-inf")
    keep = composite > best_score + cfg.epsilon and error is None

    if keep:
        new_best = {
            "iter": iter_id, "composite": composite,
            "params": result.get("params"), "symbols": result.get("symbols"),
            "tf": result.get("tf"), "period": result.get("period"),
            "metrics": result.get("main"),
            "walk_forward": result.get("walk_forward"),
            "wf_aggregate": wf_agg,
            "dsr": dsr_value,
            "env": env_mod.capture(),
            "note": note, "saved_at": finished,
        }
        _save_best(runs, new_best, strategy_file)
        verdict = "KEEP"
    else:
        best_file = runs / "best_strategy.py"
        if best_file.exists() and best is not None:
            shutil.copy2(best_file, strategy_file)
            verdict = "REVERT"
        else:
            verdict = "KEEP_NO_BASELINE" if error is None else "ERROR"
            if error is None:
                new_best = {
                    "iter": iter_id, "composite": composite,
                    "params": result.get("params"), "symbols": result.get("symbols"),
                    "tf": result.get("tf"), "period": result.get("period"),
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

    # ---- tearsheet ----
    if verdict in ("KEEP", "BASELINE") and equity_path is not None:
        try:
            ts_dir = runs / "tearsheets"
            ts_dir.mkdir(exist_ok=True)
            eq_df = pd.read_parquet(equity_path)
            trades_pq = runs / "trades" / f"iter_{iter_id:04d}.parquet"
            tr_df = pd.read_parquet(trades_pq) if trades_pq.exists() else None
            baskets_pq = runs / "baskets" / f"iter_{iter_id:04d}.parquet"
            ba_df = pd.read_parquet(baskets_pq) if baskets_pq.exists() else None
            history_so_far: list[dict] = []
            if (runs / "history.jsonl").exists():
                with (runs / "history.jsonl").open("r", encoding="utf-8") as fh:
                    for ln in fh:
                        try:
                            history_so_far.append(json.loads(ln))
                        except Exception:
                            pass
            iter_data_for_ts = {
                "iter": iter_id, "verdict": verdict,
                "composite": composite, "dsr": dsr_value,
                "params": result.get("params"), "symbols": result.get("symbols"),
                "period": result.get("period"), "tf": result.get("tf"),
                "metrics": result.get("main"),
                "wf_aggregate": wf_agg,
                "env": env_mod.capture(),
                # Stat-arb-specific extras — read by tearsheet if present, else ignored.
                "statarb": statarb_main,
                "mode": "statarb",
            }
            tearsheet_mod.render_to_file(
                iter_data_for_ts, eq_df, tr_df, history_so_far,
                ts_dir / f"iter_{iter_id:04d}.html",
                basket_events_df=ba_df,
            )
        except Exception as e:
            traceback.print_exception(type(e), e, e.__traceback__)

    row = {
        "iter": iter_id, "started": started, "finished": finished,
        "verdict": verdict, "composite": composite,
        "best_before": best_score,
        "params": result.get("params"),
        "metrics_oos": oos,
        "metrics_train": result.get("main", {}).get("train", {}),
        "statarb": statarb_main,
        "walk_forward": result.get("walk_forward"),
        "walk_forward_aggregate": wf_agg,
        "dsr": dsr_value,
        "research_stats": research_stats,
        "audit": audit_summary,
        "env": env_mod.capture(),
        "note": note, "error": error,
        "mode": "statarb",
    }
    _append_history(runs, row)

    summary = {
        "iter": iter_id, "verdict": verdict,
        "composite": (round(composite, 4) if composite != float("-inf") else None),
        "best_before": (round(best_score, 4) if best_score != float("-inf") else None),
        "oos_sharpe": round(oos.get("sharpe", 0.0), 4),
        "oos_max_dd": round(oos.get("max_dd", 0.0), 4),
        "oos_n_trades": oos.get("n_trades", 0),
        "oos_pct_time_in_position": (round(oos.get("pct_time_in_position"), 2)
                                      if oos.get("pct_time_in_position") is not None
                                      else None),
        "oos_total_return": (round(oos.get("total_return"), 4)
                              if oos.get("total_return") is not None else None),
        "dsr": round(dsr_value, 4),
        "survival_rate": statarb_main.get("survival_rate"),
        "median_half_life_bars": statarb_main.get("median_half_life_bars"),
        "n_basket_events": statarb_main.get("n_events"),
        "error": error,
    }
    if statarb_main.get("flags"):
        summary["flags"] = statarb_main["flags"]

    # Compact research highlights for the CLI line.
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
        if rs:
            summary["research"] = rs

    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--note", default="", help="One-sentence hypothesis being tested.")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--tf", default=None)
    ap.add_argument("--walk", type=int, default=4)
    ap.add_argument("--embargo", default="1D")
    ap.add_argument("--lookback", default="120D")
    ap.add_argument("--audit-mode", default="once", choices=["once", "always", "never"])
    ap.add_argument("--workers", type=int, default=1,
                    help="Walk-forward windows in parallel via "
                         "ProcessPoolExecutor (default 1 = sequential).")
    args = ap.parse_args()

    cfg = IterationConfig(
        period_start=args.start, period_end=args.end, tf=args.tf,
        walk_windows=args.walk, embargo=args.embargo, lookback=args.lookback,
        audit_mode=args.audit_mode, walk_workers=args.workers,
    )
    out = run_one(Path(args.strategy_dir), cfg, note=args.note)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
