"""Cross-symbol × cross-period robustness sweep.

Sandbox / robustness-check tool. Takes a strategy and runs it across a
matrix of (symbol, period) cells, persisting per-cell metrics + equity
curves + a cross-symbol correlation matrix. Read-only relative to the
iter loop: NEVER writes to runs/history.jsonl, runs/best.json, or
program.md.

Typical usage:

    # cross-asset robustness on top-30 liquid symbols over each calendar year
    uv run python -m runner.sweep strategies/<name> \
        --top 30 --periods 2024 2025 2026 --tag "robustness-v1"

    # full universe, train + holdout splits, with coverage filter
    uv run python -m runner.sweep strategies/<name> \
        --all-symbols-covered --periods train holdout

    # discovery
    uv run python -m runner.sweep --list-symbols

Outputs are written to ``strategies/<name>/sweeps/<sweep_id>/``:
    manifest.json        — sweep parameters + strategy sha256
    summary.parquet      — long table, one row per (symbol, period)
    equity/<sym>__<period>.parquet
    correlations.parquet — N×N OOS-returns correlation matrix
    report.json          — pre-computed breadth / top-N / aggregates
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class SweepConfig:
    # Symbol selection — one of these populates `symbols`.
    symbols: list[str] = field(default_factory=list)
    all_symbols: bool = False
    all_symbols_covered: bool = False
    top_n: int | None = None
    coverage_min: float = 0.90      # used for all-symbols-covered
    # Period selection — list of presets / "YYYY-MM:YYYY-MM" / "YYYY".
    periods: list[str] = field(default_factory=lambda: ["2024", "2025", "2026"])
    # Backtest knobs.
    tf: str | None = None
    walk_windows: int = 1           # 1 = single train/OOS split per cell.
    no_wf: bool = False             # if True, run a single uninterrupted backtest
                                    # over the whole cell period (no train/OOS).
    cost_model: str = "static"
    embargo: str = "1D"
    lookback: str = "60D"
    parallel: int | None = None     # None = min(8, cpu_count)
    tag: str = ""


# --------------------------------------------------------------------------- #
# Symbol & period resolution
# --------------------------------------------------------------------------- #
def _available_symbols() -> list[str]:
    from datafeed.loader import available_symbols
    return available_symbols()


def resolve_symbols(cfg: SweepConfig, period_bounds: tuple[str, str] | None = None
                    ) -> list[str]:
    """Resolve the symbol set per SweepConfig.

    Resolution priority:
      1. cfg.symbols (explicit) — used as-is, after validation.
      2. cfg.top_n              — top-N by quote volume over period_bounds.
      3. cfg.all_symbols_covered — all symbols whose bars cover ≥ coverage_min
                                   of period_bounds.
      4. cfg.all_symbols         — every directory under data/.../1m/.
    """
    avail = set(_available_symbols())
    if cfg.symbols:
        missing = [s for s in cfg.symbols if s not in avail]
        if missing:
            raise SystemExit(f"unknown symbols: {missing}")
        return list(cfg.symbols)
    if cfg.top_n:
        if period_bounds is None:
            raise SystemExit("--top requires a period to rank over")
        from datafeed.universe import top_by_volume
        ps, pe = period_bounds
        return top_by_volume(ps, pe, n=cfg.top_n, tf="1h")
    if cfg.all_symbols_covered:
        if period_bounds is None:
            raise SystemExit("--all-symbols-covered requires a period")
        from datafeed.universe import alive_during
        ps, pe = period_bounds
        return alive_during(ps, pe, min_coverage=cfg.coverage_min)
    if cfg.all_symbols:
        return sorted(avail)
    raise SystemExit("no symbol selection provided; "
                     "use --symbols / --top / --all-symbols / --all-symbols-covered")


def resolve_periods(specs: list[str]) -> list[tuple[str, str, str]]:
    """Parse a list of period specs into (label, start, end) tuples.

    Accepted forms:
      - "YYYY"             → that calendar year
      - "YYYY-MM:YYYY-MM"  → custom range, inclusive start / exclusive end
      - "train"            → 2024-01-01 .. 2026-01-01
      - "holdout"          → 2026-01-01 .. 2026-05-01
    """
    out: list[tuple[str, str, str]] = []
    for s in specs:
        s = s.strip()
        if s == "train":
            out.append(("train", "2024-01-01", "2026-01-01"))
        elif s == "holdout":
            out.append(("holdout", "2026-01-01", "2026-05-01"))
        elif s.isdigit() and len(s) == 4:
            y = int(s)
            out.append((s, f"{y}-01-01", f"{y + 1}-01-01"))
        elif ":" in s:
            a, b = s.split(":", 1)
            # Allow YYYY-MM or YYYY-MM-DD on either side.
            ps = a if len(a) == 10 else f"{a}-01"
            pe = b if len(b) == 10 else f"{b}-01"
            out.append((s, ps, pe))
        else:
            raise SystemExit(f"unparseable period spec: {s!r}")
    return out


def encompassing_period_bounds(periods: list[tuple[str, str, str]]
                                ) -> tuple[str, str]:
    """Min start, max end across a list of (label, start, end)."""
    if not periods:
        raise SystemExit("no periods to sweep")
    starts = sorted(p[1] for p in periods)
    ends = sorted(p[2] for p in periods)
    return starts[0], ends[-1]


# --------------------------------------------------------------------------- #
# Per-cell worker
# --------------------------------------------------------------------------- #
def _run_cell(args: dict) -> dict:
    """Worker: run one (symbol, period) cell.

    Returns a flat dict with metrics + paths to persisted artefacts.
    Designed to be picklable and run in a separate process.
    """
    strategy_dir = args["strategy_dir"]
    symbol = args["symbol"]
    label = args["label"]
    ps = args["period_start"]
    pe = args["period_end"]
    tf = args["tf"]
    walk = args["walk_windows"]
    no_wf = args["no_wf"]
    cost_model = args["cost_model"]
    embargo = args["embargo"]
    lookback = args["lookback"]
    out_dir = Path(args["out_dir"])

    t0 = time.time()
    error: str | None = None
    metrics: dict = {}
    train_metrics: dict = {}
    wf_summary: dict = {}
    equity_path: str | None = None
    oos_returns_path: str | None = None

    try:
        # Silence harness stdout chatter; aggregate logs from main process.
        with contextlib.redirect_stdout(io.StringIO()):
            from harness import backtest as bt
            from harness.costs import CostModel

            cost_kwargs = {
                "static": {},
                "spread": {"use_dynamic_spread": True},
                "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
            }[cost_model]
            costs = CostModel(**cost_kwargs)

            if no_wf:
                # Single uninterrupted backtest over the whole cell. We still
                # use train_oos() under the hood, but request only the main
                # split so the OOS slice equals the full period — by passing
                # walk_windows=0 and treating "main" as the whole window.
                # Simplest hack: call run_split directly with a synthetic split.
                from harness.splits import Split
                from harness.backtest import load_strategy, run_split
                mod = load_strategy(Path(strategy_dir))
                params = dict(getattr(mod, "DEFAULT_PARAMS", {}))
                ps_ts = pd.Timestamp(ps, tz="UTC")
                pe_ts = pd.Timestamp(pe, tz="UTC")
                # No train/OOS split — train slice is empty (zero-length),
                # OOS spans the whole period.
                split = Split(train_start=ps_ts, train_end=ps_ts,
                              oos_start=ps_ts, oos_end=pe_ts)
                w = run_split(mod, params, [symbol], split, tf=tf, costs=costs,
                              return_curves=True, lookback=lookback)
                metrics = w.get("oos", {}) or {}
                train_metrics = {}
                # Persist equity curve.
                eq = w.get("equity")
                bench = w.get("benchmark")
                oos_ret = w.get("oos_returns")
                if eq is not None and not eq.empty:
                    eq_df = pd.DataFrame({
                        "timestamp": eq.index,
                        "equity": eq.values,
                        "benchmark": (bench.reindex(eq.index).values
                                       if bench is not None
                                       else np.full(len(eq), np.nan)),
                    })
                    fp = out_dir / "equity" / f"{symbol}__{label}.parquet"
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    eq_df.to_parquet(fp, compression="zstd", index=False)
                    equity_path = fp.relative_to(out_dir).as_posix()
                if oos_ret is not None and not oos_ret.empty:
                    rp = out_dir / "oos_returns" / f"{symbol}__{label}.parquet"
                    rp.parent.mkdir(parents=True, exist_ok=True)
                    pd.DataFrame({
                        "timestamp": oos_ret.index,
                        "ret": oos_ret.values,
                    }).to_parquet(rp, compression="zstd", index=False)
                    oos_returns_path = rp.relative_to(out_dir).as_posix()
                wf_summary = {"mode": "no-wf", "n_windows": 1}
            else:
                res = bt.run(
                    strategy_dir, ps, pe,
                    symbols=[symbol], tf=tf,
                    walk_windows=walk,
                    embargo=embargo,
                    costs=costs,
                    lookback=lookback,
                    return_curves=True,
                )
                main = res.get("main") or {}
                metrics = main.get("oos") or {}
                train_metrics = main.get("train") or {}

                curves = res.get("curves")
                wf_curves = (res.get("walk_forward") or {}).get("curves")
                wf_windows = (res.get("walk_forward") or {}).get("windows") or []

                # Aggregate WF if present.
                if wf_windows:
                    oos_blocks = [w.get("oos", {}) for w in wf_windows]
                    sharpes = [b.get("sharpe", 0.0) for b in oos_blocks]
                    metrics = {
                        "sharpe": float(np.mean(sharpes)),
                        "sharpe_min": float(np.min(sharpes)) if sharpes else 0.0,
                        "max_dd": float(min(b.get("max_dd", 0.0) for b in oos_blocks)),
                        "n_trades": int(np.mean([b.get("n_trades", 0) for b in oos_blocks])),
                        "total_return": float(np.mean(
                            [b.get("total_return", 0.0) for b in oos_blocks])),
                        "pct_time_in_position": float(np.mean(
                            [b.get("pct_time_in_position", 0.0) for b in oos_blocks])),
                        "profit_factor": float(np.mean(
                            [b.get("profit_factor", 0.0) or 0.0 for b in oos_blocks])),
                        "expectancy": float(np.mean(
                            [b.get("expectancy", 0.0) or 0.0 for b in oos_blocks])),
                        "information_ratio": float(np.mean(
                            [b.get("information_ratio", 0.0) or 0.0 for b in oos_blocks])),
                        "cvar_95": float(min(
                            (b.get("cvar_95", 0.0) or 0.0) for b in oos_blocks)),
                        "max_participation_pct": float(max(
                            (b.get("max_participation_pct", 0.0) or 0.0) for b in oos_blocks)),
                    }
                    wf_summary = {
                        "mode": "wf",
                        "n_windows": len(wf_windows),
                        "per_window_sharpe": sharpes,
                    }

                # Persist equity curve (concatenate WF windows if present).
                eq_path = out_dir / "equity" / f"{symbol}__{label}.parquet"
                eq_path.parent.mkdir(parents=True, exist_ok=True)
                if wf_curves:
                    frames = []
                    for i, c in enumerate(wf_curves):
                        eq = c.get("equity")
                        if eq is None or eq.empty:
                            continue
                        b = c.get("benchmark")
                        frames.append(pd.DataFrame({
                            "timestamp": eq.index,
                            "equity": eq.values,
                            "benchmark": (b.reindex(eq.index).values if b is not None
                                          else np.full(len(eq), np.nan)),
                            "window": i,
                        }))
                    if frames:
                        pd.concat(frames, ignore_index=True).to_parquet(
                            eq_path, compression="zstd", index=False)
                        equity_path = eq_path.relative_to(out_dir).as_posix()
                elif curves is not None and curves.get("equity") is not None:
                    eq = curves["equity"]
                    b = curves.get("benchmark")
                    pd.DataFrame({
                        "timestamp": eq.index,
                        "equity": eq.values,
                        "benchmark": (b.reindex(eq.index).values if b is not None
                                      else np.full(len(eq), np.nan)),
                    }).to_parquet(eq_path, compression="zstd", index=False)
                    equity_path = eq_path.relative_to(out_dir).as_posix()

                # Persist concatenated OOS returns for correlation matrix.
                oos_frames = []
                if wf_curves:
                    for c in wf_curves:
                        r = c.get("oos_returns")
                        if r is not None and not r.empty:
                            oos_frames.append(r)
                elif curves is not None and curves.get("oos_returns") is not None:
                    oos_frames.append(curves["oos_returns"])
                if oos_frames:
                    rr = pd.concat(oos_frames).sort_index()
                    rp = out_dir / "oos_returns" / f"{symbol}__{label}.parquet"
                    rp.parent.mkdir(parents=True, exist_ok=True)
                    pd.DataFrame({
                        "timestamp": rr.index,
                        "ret": rr.values,
                    }).to_parquet(rp, compression="zstd", index=False)
                    oos_returns_path = rp.relative_to(out_dir).as_posix()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exception(type(e), e, e.__traceback__)

    duration = time.time() - t0
    row = {
        "symbol": symbol,
        "period": label,
        "period_start": ps,
        "period_end": pe,
        "sharpe": metrics.get("sharpe"),
        "max_dd": metrics.get("max_dd"),
        "n_trades": metrics.get("n_trades"),
        "total_return": metrics.get("total_return"),
        "pct_time_in_position": metrics.get("pct_time_in_position"),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy": metrics.get("expectancy"),
        "information_ratio": metrics.get("information_ratio"),
        "cvar_95": metrics.get("cvar_95"),
        "max_participation_pct": metrics.get("max_participation_pct"),
        "train_sharpe": train_metrics.get("sharpe"),
        "wf": wf_summary,
        "equity_path": equity_path,
        "oos_returns_path": oos_returns_path,
        "duration_s": round(duration, 2),
        "error": error,
    }
    return row


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _to_float(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def compute_report(summary: pd.DataFrame) -> dict:
    """Per-period breadth + top/bottom-K + cross-period stability + globals."""
    if summary.empty:
        return {"per_period": [], "per_symbol": [], "global": {}}

    per_period: list[dict] = []
    for period, g in summary.groupby("period", sort=False):
        valid_sh = g["sharpe"].dropna()
        # Breadth across non-error cells only.
        cells_ok = g[g["error"].isna() | (g["error"] == "")]
        n = int(len(cells_ok))
        n_pos_sharpe = int((cells_ok["sharpe"] > 0).sum()) if n else 0
        n_pos_ret = int((cells_ok["total_return"] > 0).sum()) if n else 0
        top5 = g.dropna(subset=["sharpe"]).nlargest(5, "sharpe")[
            ["symbol", "sharpe", "max_dd", "total_return"]
        ].to_dict(orient="records")
        bot5 = g.dropna(subset=["sharpe"]).nsmallest(5, "sharpe")[
            ["symbol", "sharpe", "max_dd", "total_return"]
        ].to_dict(orient="records")
        per_period.append({
            "period": period,
            "n_cells": int(len(g)),
            "n_cells_ok": n,
            "n_errors": int(len(g) - n),
            "pct_sharpe_positive": (n_pos_sharpe / n) if n else None,
            "pct_return_positive": (n_pos_ret / n) if n else None,
            "median_sharpe": _to_float(valid_sh.median()) if not valid_sh.empty else None,
            "mean_sharpe": _to_float(valid_sh.mean()) if not valid_sh.empty else None,
            "iqr_sharpe": (_to_float(valid_sh.quantile(0.75) - valid_sh.quantile(0.25))
                            if len(valid_sh) >= 4 else None),
            "median_max_dd": _to_float(g["max_dd"].median()),
            "median_total_return": _to_float(g["total_return"].median()),
            "top": top5,
            "bottom": bot5,
        })

    per_symbol: list[dict] = []
    for symbol, g in summary.groupby("symbol", sort=False):
        valid_sh = g["sharpe"].dropna()
        n_periods = int(len(g))
        n_pos = int((g["sharpe"] > 0).sum())
        per_symbol.append({
            "symbol": symbol,
            "n_periods": n_periods,
            "pct_positive_periods": (n_pos / n_periods) if n_periods else None,
            "mean_sharpe": _to_float(valid_sh.mean()) if not valid_sh.empty else None,
            "min_sharpe": _to_float(valid_sh.min()) if not valid_sh.empty else None,
            "max_sharpe": _to_float(valid_sh.max()) if not valid_sh.empty else None,
            "mean_total_return": _to_float(g["total_return"].mean()),
            "worst_max_dd": _to_float(g["max_dd"].min()),
        })
    # Rank symbols by mean Sharpe.
    per_symbol.sort(key=lambda r: (r["mean_sharpe"] is None,
                                    -(r["mean_sharpe"] or 0.0)))

    valid_sh_all = summary["sharpe"].dropna()
    glob = {
        "n_cells": int(len(summary)),
        "n_errors": int(summary["error"].notna().sum() if "error" in summary
                         else 0),
        "median_sharpe": _to_float(valid_sh_all.median()) if not valid_sh_all.empty else None,
        "mean_sharpe": _to_float(valid_sh_all.mean()) if not valid_sh_all.empty else None,
        "pct_sharpe_positive": (
            float((valid_sh_all > 0).sum()) / len(valid_sh_all)
            if len(valid_sh_all) else None
        ),
    }
    return {"per_period": per_period, "per_symbol": per_symbol, "global": glob}


def compute_correlation_matrix(out_dir: Path, summary: pd.DataFrame
                                ) -> pd.DataFrame | None:
    """Symbol × symbol correlation of concatenated OOS returns (across periods).

    Useful for spotting "this strategy is just BTC beta": if every symbol's
    OOS-returns correlate ≥ 0.9 with BTC's, there's no diversification.
    """
    by_symbol: dict[str, pd.Series] = {}
    for symbol, g in summary.groupby("symbol", sort=False):
        parts = []
        for _, row in g.iterrows():
            p = row.get("oos_returns_path")
            if not p:
                continue
            try:
                df = pd.read_parquet(out_dir / p)
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                parts.append(df.set_index("timestamp")["ret"])
            except Exception:
                pass
        if parts:
            by_symbol[symbol] = pd.concat(parts).sort_index()
    if len(by_symbol) < 2:
        return None
    # Align on common timestamps, fill rest with 0 (no exposure = 0 return).
    df = pd.DataFrame(by_symbol).sort_index().fillna(0.0)
    if df.shape[0] < 30:
        return None
    return df.corr()


# --------------------------------------------------------------------------- #
# Sweep driver
# --------------------------------------------------------------------------- #
def _new_sweep_id(tag: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{ts}__{tag}" if tag else ts


def _strategy_sha256(strategy_dir: Path) -> str:
    h = hashlib.sha256()
    h.update((strategy_dir / "strategy.py").read_bytes())
    return h.hexdigest()


def run_sweep(strategy_dir: Path, cfg: SweepConfig) -> dict:
    strategy_dir = Path(strategy_dir).resolve()
    if not (strategy_dir / "strategy.py").exists():
        raise FileNotFoundError(strategy_dir / "strategy.py")

    # Resolve TF.
    tf = cfg.tf
    if tf is None:
        from harness.backtest import load_strategy
        try:
            mod = load_strategy(strategy_dir)
            tf = getattr(mod, "DEFAULT_TF", "1h")
        except Exception:
            tf = "1h"

    # Resolve periods.
    periods = resolve_periods(cfg.periods)
    bounds = encompassing_period_bounds(periods)

    # Resolve symbols.
    print(f"[sweep] resolving symbols ...", flush=True)
    symbols = resolve_symbols(cfg, period_bounds=bounds)
    print(f"[sweep] {len(symbols)} symbols × {len(periods)} periods = "
          f"{len(symbols) * len(periods)} cells", flush=True)

    sweep_id = _new_sweep_id(cfg.tag)
    out_dir = strategy_dir / "sweeps" / sweep_id
    (out_dir / "equity").mkdir(parents=True, exist_ok=True)
    (out_dir / "oos_returns").mkdir(parents=True, exist_ok=True)

    manifest = {
        "sweep_id": sweep_id,
        "strategy": strategy_dir.name,
        "strategy_sha256": _strategy_sha256(strategy_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tag": cfg.tag,
        "tf": tf,
        "walk_windows": (0 if cfg.no_wf else cfg.walk_windows),
        "no_wf": cfg.no_wf,
        "cost_model": cfg.cost_model,
        "embargo": cfg.embargo,
        "lookback": cfg.lookback,
        "symbols": symbols,
        "periods": [{"label": l, "start": s, "end": e} for l, s, e in periods],
        "coverage_min": cfg.coverage_min,
        "selection_mode": (
            "explicit" if cfg.symbols else
            f"top_{cfg.top_n}" if cfg.top_n else
            "all_symbols_covered" if cfg.all_symbols_covered else
            "all_symbols" if cfg.all_symbols else
            "?"
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )

    # Build per-cell args.
    cells = []
    for sym in symbols:
        for label, ps, pe in periods:
            cells.append({
                "strategy_dir": str(strategy_dir),
                "symbol": sym,
                "label": label,
                "period_start": ps,
                "period_end": pe,
                "tf": tf,
                "walk_windows": cfg.walk_windows,
                "no_wf": cfg.no_wf,
                "cost_model": cfg.cost_model,
                "embargo": cfg.embargo,
                "lookback": cfg.lookback,
                "out_dir": str(out_dir),
            })

    # Parallel execution.
    workers = cfg.parallel or min(8, os.cpu_count() or 4)
    print(f"[sweep] running with {workers} worker(s) ...", flush=True)

    rows: list[dict] = []
    started = time.time()
    progress_path = out_dir / "progress.json"

    def _write_progress(done: int, total: int):
        try:
            progress_path.write_text(json.dumps({
                "done": done, "total": total,
                "elapsed_s": round(time.time() - started, 1),
            }), encoding="utf-8")
        except Exception:
            pass

    _write_progress(0, len(cells))

    if workers == 1:
        for i, c in enumerate(cells, 1):
            row = _run_cell(c)
            rows.append(row)
            _write_progress(i, len(cells))
            print(f"[sweep] {i}/{len(cells)}  {row['symbol']} {row['period']}  "
                  f"sharpe={row['sharpe']}  err={row['error']}", flush=True)
    else:
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_cell, c): c for c in cells}
            done = 0
            for fut in cf.as_completed(futs):
                done += 1
                try:
                    row = fut.result()
                except Exception as e:
                    c = futs[fut]
                    row = {
                        "symbol": c["symbol"], "period": c["label"],
                        "period_start": c["period_start"],
                        "period_end": c["period_end"],
                        "error": f"worker crash: {type(e).__name__}: {e}",
                    }
                rows.append(row)
                _write_progress(done, len(cells))
                print(f"[sweep] {done}/{len(cells)}  {row['symbol']} "
                      f"{row['period']}  sharpe={row.get('sharpe')}  "
                      f"err={row.get('error')}", flush=True)

    # Persist summary.
    summary = pd.DataFrame(rows)
    # Stable column ordering.
    preferred = [
        "symbol", "period", "period_start", "period_end",
        "sharpe", "max_dd", "n_trades", "total_return",
        "pct_time_in_position", "profit_factor", "expectancy",
        "information_ratio", "cvar_95", "max_participation_pct",
        "train_sharpe", "duration_s", "equity_path", "oos_returns_path",
        "error",
    ]
    cols = [c for c in preferred if c in summary.columns] + [
        c for c in summary.columns if c not in preferred and c != "wf"
    ]
    summary[cols].to_parquet(out_dir / "summary.parquet",
                              compression="zstd", index=False)
    # JSON form for easy serving — wf dict stays as a string.
    summary_json = summary[cols].copy()
    if "n_trades" in summary_json.columns:
        summary_json["n_trades"] = summary_json["n_trades"].where(
            summary_json["n_trades"].notna(), None
        )
    (out_dir / "summary.json").write_text(
        json.dumps(summary_json.to_dict(orient="records"),
                   indent=2, default=str), encoding="utf-8"
    )

    # Aggregates.
    report = compute_report(summary)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    # Correlation matrix.
    corr = compute_correlation_matrix(out_dir, summary)
    if corr is not None:
        corr.to_parquet(out_dir / "correlations.parquet", compression="zstd")

    # Update manifest with completion data.
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["duration_s"] = round(time.time() - started, 1)
    manifest["n_cells"] = len(cells)
    manifest["n_errors"] = int(summary["error"].notna().sum())
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )

    # CLI summary.
    glob = report["global"]
    print("", flush=True)
    print(f"[sweep] done in {manifest['duration_s']}s — "
          f"{manifest['n_cells']} cells, {manifest['n_errors']} errors",
          flush=True)
    print(f"[sweep] median Sharpe={glob.get('median_sharpe')}  "
          f"pct positive={glob.get('pct_sharpe_positive')}", flush=True)
    for pp in report["per_period"]:
        print(f"[sweep]   {pp['period']:<10}  "
              f"breadth={pp.get('pct_sharpe_positive')}  "
              f"median_sharpe={pp.get('median_sharpe')}  "
              f"median_dd={pp.get('median_max_dd')}", flush=True)
    print(f"[sweep] artefacts: {out_dir}", flush=True)
    return {"sweep_id": sweep_id, "out_dir": str(out_dir),
            "manifest": manifest, "report": report}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("strategy_dir", nargs="?",
                    help="Path to strategies/<name>/ (omit only with --list-symbols).")
    sel = ap.add_argument_group("symbol selection (choose one)")
    sel.add_argument("--symbols", nargs="*", default=None,
                     help="Explicit list, e.g. BTCUSDT ETHUSDT")
    sel.add_argument("--symbols-file", default=None,
                     help="One symbol per line.")
    sel.add_argument("--all-symbols", action="store_true",
                     help="Every directory under data/.../1m/ (~174 syms).")
    sel.add_argument("--all-symbols-covered", action="store_true",
                     help="--all-symbols filtered by --coverage-min over the "
                          "encompassing period span.")
    sel.add_argument("--top", type=int, default=None,
                     help="Top-N by quote volume over the encompassing period.")
    sel.add_argument("--coverage-min", type=float, default=0.90,
                     help="Coverage threshold for --all-symbols-covered.")

    per = ap.add_argument_group("periods")
    per.add_argument("--periods", nargs="*",
                     default=["2024", "2025", "2026"],
                     help="Mix of presets ('2024' / 'train' / 'holdout') and "
                          "ranges ('2024-06:2025-06'). Default: 2024 2025 2026.")

    bt = ap.add_argument_group("backtest")
    bt.add_argument("--tf", default=None,
                    help="If omitted, read strategy DEFAULT_TF, fall back to 1h.")
    bt.add_argument("--wf", type=int, default=1,
                    help="Walk-forward windows per cell. Default: 1 "
                         "(single train/OOS). Use --no-wf for one uninterrupted "
                         "backtest over the whole cell period.")
    bt.add_argument("--no-wf", action="store_true",
                    help="One uninterrupted backtest per cell — no train/OOS split.")
    bt.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static")
    bt.add_argument("--embargo", default="1D")
    bt.add_argument("--lookback", default="60D")
    bt.add_argument("--parallel", type=int, default=None,
                    help="Worker processes. Default: min(8, cpu_count).")
    bt.add_argument("--tag", default="",
                    help="Free-form label appended to sweep_id.")

    util = ap.add_argument_group("utilities")
    util.add_argument("--list-symbols", action="store_true",
                      help="Print all available symbols and exit.")

    args = ap.parse_args()

    # UTF-8 stdout on Windows so unicode in stats prints correctly.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if args.list_symbols:
        for s in _available_symbols():
            print(s)
        return

    if not args.strategy_dir:
        ap.error("strategy_dir is required (omit only with --list-symbols)")

    symbols: list[str] = []
    if args.symbols:
        symbols = list(args.symbols)
    elif args.symbols_file:
        symbols = [
            line.strip() for line in
            Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    selection_flags = [bool(symbols), bool(args.top), args.all_symbols,
                       args.all_symbols_covered]
    if sum(selection_flags) != 1:
        ap.error("provide exactly one of: --symbols / --symbols-file / "
                 "--top / --all-symbols / --all-symbols-covered")

    cfg = SweepConfig(
        symbols=symbols,
        all_symbols=args.all_symbols,
        all_symbols_covered=args.all_symbols_covered,
        top_n=args.top,
        coverage_min=args.coverage_min,
        periods=list(args.periods),
        tf=args.tf,
        walk_windows=args.wf,
        no_wf=args.no_wf,
        cost_model=args.cost_model,
        embargo=args.embargo,
        lookback=args.lookback,
        parallel=args.parallel,
        tag=args.tag,
    )

    out = run_sweep(Path(args.strategy_dir), cfg)
    # Print JSON so callers (job runner) can parse.
    print(json.dumps({
        "sweep_id": out["sweep_id"],
        "out_dir": out["out_dir"],
    }, indent=2))


if __name__ == "__main__":
    main()
