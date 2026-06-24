"""FastAPI JSON API + static prod-build serving for the researchlab dashboard.

Dev:  run Vite (`cd frontend && npm run dev`) on :5173 — it proxies /api to :8000.
Prod: `cd frontend && npm run build` then `uv run uvicorn web.app:app --port 8000`.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
STRATS = ROOT / "strategies"
DIST = ROOT / "frontend" / "dist"


def _extract_description(strategy_py: Path) -> str | None:
    """Read the DESCRIPTION module-level constant from a strategy.py
    without importing the module. Returns None if absent or unparseable.

    Looks for assignments like ``DESCRIPTION = "..."`` or
    ``DESCRIPTION = ("..." "...")`` (string-concat literals).
    """
    import ast
    try:
        tree = ast.parse(strategy_py.read_text(encoding="utf-8"))
    except Exception:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "DESCRIPTION":
            continue
        try:
            v = ast.literal_eval(node.value)
        except Exception:
            return None
        return v if isinstance(v, str) else None
    return None

app = FastAPI(title="researchlab")

# Vite dev server runs on :5173, FastAPI on :8000 — allow CORS in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Job runner
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, jid: str, cmd: list[str]):
        self.id = jid
        self.cmd = cmd
        self.status = "pending"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.lines: deque[str] = deque(maxlen=2000)
        self.proc: subprocess.Popen | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "cmd": self.cmd,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "tail": list(self.lines)[-200:],
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _run_job(job: Job) -> None:
    try:
        job.status = "running"
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        job.proc = subprocess.Popen(
            job.cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=creationflags,
        )
        assert job.proc.stdout is not None
        for line in job.proc.stdout:
            job.lines.append(line.rstrip("\n"))
        job.proc.wait()
        job.exit_code = job.proc.returncode
        job.status = "done" if job.exit_code == 0 else "failed"
    except Exception as e:
        job.lines.append(f"[runner error] {type(e).__name__}: {e}")
        job.status = "failed"
        job.exit_code = -1
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()


def _start_job(cmd: list[str]) -> Job:
    jid = uuid.uuid4().hex[:8]
    job = Job(jid, cmd)
    with JOBS_LOCK:
        JOBS[jid] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _strategy_dir(name: str) -> Path:
    p = STRATS / name
    if not p.exists() or not (p / "strategy.py").exists():
        raise HTTPException(404, f"strategy '{name}' not found")
    return p


def _load_history(strategy: str) -> list[dict]:
    p = _strategy_dir(strategy) / "runs" / "history.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _load_best(strategy: str) -> dict | None:
    p = _strategy_dir(strategy) / "runs" / "best.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _history_summary(hist_path: Path) -> tuple[int, str | None, float | None, int | None]:
    n_iters = 0
    first_started: str | None = None
    best_pnl: float | None = None
    best_pnl_iter: int | None = None
    if not hist_path.exists():
        return n_iters, first_started, best_pnl, best_pnl_iter
    with hist_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            n_iters += 1
            row = None
            if first_started is None:
                try:
                    row = json.loads(line)
                    first_started = row.get("started")
                except Exception:
                    pass
            if row is None:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
            pnl = ((row.get("wf_aggregate") or {}).get("stitched_full_return"))
            if pnl is None:
                pnl = ((row.get("wf_aggregate") or {}).get("stitched_oos_return"))
            if pnl is None:
                pnl = ((row.get("metrics_oos") or {}).get("total_return"))
            if isinstance(pnl, (int, float)) and math.isfinite(float(pnl)):
                pnl_f = float(pnl)
                if best_pnl is None or pnl_f > best_pnl:
                    best_pnl = pnl_f
                    best_pnl_iter = row.get("iter")
    return n_iters, first_started, best_pnl, best_pnl_iter


def _stitched_total_return(strategy: str, iter_id: int) -> dict | None:
    """Read the saved equity parquet for an iter and stitch per-window
    returns into one continuous 24mo equity. Returns total compounded
    return + per-year compounded return. Used by the trust-verdict
    block — answers the "if you ran this strategy non-stop on the full
    24mo, what's the equity at end?" question that composite hides.

    Returns None if the equity parquet is missing or empty.
    """
    p = _strategy_dir(strategy) / "runs" / "equity" / f"iter_{iter_id:04d}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp")
    if "window" in df.columns:
        rets = []
        for _, g in df.groupby("window"):
            g = g.sort_values("timestamp")
            r = g["equity"].pct_change().fillna(0.0)
            rets.append(pd.Series(r.values, index=g["timestamp"]))
        rets_concat = pd.concat(rets).sort_index()
    else:
        rets_concat = df.set_index("timestamp")["equity"].pct_change().fillna(0.0)
    if rets_concat.empty:
        return None
    total = float((1.0 + rets_concat).prod() - 1.0)
    # Per-year compounded.
    by_year: dict[int, float] = {}
    for y, grp in rets_concat.groupby(rets_concat.index.year):
        by_year[int(y)] = float((1.0 + grp).prod() - 1.0)
    monthly = ((1.0 + rets_concat).resample("MS").prod() - 1.0).dropna()
    n_positive_months = int((monthly > 0).sum())
    n_months = int(len(monthly))
    return {
        "total_return": total,
        "year_returns": [{"year": y, "return": r} for y, r in sorted(by_year.items())],
        "n_months": n_months,
        "n_positive_months": n_positive_months,
        "pct_positive_months": (n_positive_months / n_months) if n_months else None,
    }


def _compute_trust_verdict(strategy: str, best: dict | None,
                           history: list[dict]) -> dict | None:
    """Single honest verdict on whether `best` is likely real edge or
    selection-bias artefact. Combines four independent checks:

      1. Permutation p-value (from latest research_stats with permutation)
         — does the strategy depend on bar chronology? p≈1 means shuffling
         the timeline doesn't break it = no real time-series edge.
      2. BHY-haircut Sharpe (Harvey-Liu-Zhu correction for N trials)
         — raw Sharpe minus the selection-bias tax. < 0.5 means most of
         the apparent edge is the "best of N" effect.
      3. Train/OOS sign agreement across WF windows. Both negative is
         honest (strategy bleeds in this regime, both slices agree).
         Both positive is honest. Train negative + OOS positive in all
         windows is the calendar-bias / selection-on-OOS signature.
      4. Stitched 24mo total return. Composite is OOS-only (~25% of
         period); stitched is the full account. Negative + composite
         positive = OOS-only edge that vanishes on real money.

    Returns None when best is missing. Otherwise:
      {
        level: "green" | "yellow" | "red",
        label: ...,
        checks: [{name, passed, value, threshold, note}],
        headline_sharpe: {raw, bhy, haircut_pct},
        sign_agreement: {agree, total, train_signs, oos_signs},
        stitched: {total_return, year_returns, n_months, pct_positive_months},
      }
    """
    if not best:
        return None

    # --- Check 3: train/OOS sign agreement across WF windows. ---
    wf = best.get("walk_forward") or {}
    windows = wf.get("windows") or []
    train_signs: list[str] = []
    oos_signs: list[str] = []
    agree_count = 0
    for w in windows:
        ts = ((w.get("train") or {}).get("sharpe"))
        os_ = ((w.get("oos") or {}).get("sharpe"))
        if ts is None or os_ is None:
            train_signs.append("?")
            oos_signs.append("?")
            continue
        t_sign = "+" if ts > 0 else ("-" if ts < 0 else "0")
        o_sign = "+" if os_ > 0 else ("-" if os_ < 0 else "0")
        train_signs.append(t_sign)
        oos_signs.append(o_sign)
        if t_sign == o_sign and t_sign != "?":
            agree_count += 1
    sign_agreement = {
        "agree": agree_count,
        "total": len(windows),
        "train_signs": train_signs,
        "oos_signs": oos_signs,
    }
    # Pass: ≥ 3/4 (or ≥ 75% for other counts) — honest WF behaviour.
    sign_passed: bool | None
    if not windows:
        sign_passed = None
    else:
        sign_passed = agree_count / len(windows) >= 0.75

    # --- Checks 1 & 2: permutation p + BHY haircut Sharpe. ---
    # Pull from history's latest research_stats block.
    perm_p: float | None = None
    raw_sharpe: float | None = None
    bhy_sharpe: float | None = None
    bhy_haircut_pct: float | None = None
    for row in reversed(history):
        rs = row.get("research_stats") or {}
        if not rs:
            continue
        boot = rs.get("bootstrap") or {}
        perm = boot.get("permutation") or {}
        if perm and perm.get("p_values"):
            perm_p = perm["p_values"].get("sharpe")
        hc = (rs.get("haircut_sharpe") or {})
        if hc:
            raw_sharpe = (hc.get("raw") or {}).get("sharpe")
            bhy = hc.get("bhy") or {}
            bhy_sharpe = bhy.get("sharpe")
            bhy_haircut_pct = bhy.get("haircut_pct")
        if perm_p is not None or bhy_sharpe is not None:
            break

    perm_passed: bool | None
    if perm_p is None:
        perm_passed = None
    else:
        # Pass: p < 0.10 (block-bootstrap style, generous).
        perm_passed = perm_p < 0.10

    bhy_passed: bool | None
    if bhy_sharpe is None:
        bhy_passed = None
    else:
        bhy_passed = bhy_sharpe > 0.5

    # --- Check 4: stitched 24mo total return. ---
    stitched = _stitched_total_return(strategy, int(best.get("iter") or 0))
    stitched_passed: bool | None
    if stitched is None:
        stitched_passed = None
    else:
        stitched_passed = stitched["total_return"] >= 0.0

    checks = [
        {
            "name": "Permutation p-value",
            "passed": perm_passed,
            "value": perm_p,
            "threshold": "< 0.10",
            "note": (
                "Strategy depends on real time-series structure"
                if perm_passed is True else
                "Shuffling bar order doesn't break the 'edge' — it's not chronological"
                if perm_passed is False else
                "Not yet computed (run iterate to populate permutation bootstrap)"
            ),
        },
        {
            "name": "BHY-haircut Sharpe",
            "passed": bhy_passed,
            "value": bhy_sharpe,
            "threshold": "> 0.5",
            "note": (
                f"Raw {raw_sharpe:.2f} survives {(bhy_haircut_pct or 0) * 100:.0f}% selection-bias haircut"
                if bhy_passed is True and raw_sharpe is not None else
                f"Raw {raw_sharpe:.2f} → BHY {bhy_sharpe:.2f} after correcting for trials selection"
                if bhy_sharpe is not None and raw_sharpe is not None else
                "Not yet computed"
            ),
        },
        {
            "name": "Train↔OOS sign agreement",
            "passed": sign_passed,
            "value": (agree_count, len(windows)),
            "threshold": "≥ 75% of windows",
            "note": (
                f"{agree_count}/{len(windows)} WF windows have matching train & OOS direction"
                if sign_passed is not None else
                "No walk-forward windows"
            ),
        },
        {
            "name": "Stitched 24mo total return",
            "passed": stitched_passed,
            "value": (stitched or {}).get("total_return"),
            "threshold": "≥ 0",
            "note": (
                "Strategy made money over the full train+val period (not just OOS slices)"
                if stitched_passed is True else
                "Strategy LOST money over full period — composite captures only OOS slices"
                if stitched_passed is False else
                "Equity parquet missing"
            ),
        },
    ]

    n_passed = sum(1 for c in checks if c["passed"] is True)
    n_failed = sum(1 for c in checks if c["passed"] is False)
    n_known = n_passed + n_failed

    # Verdict logic:
    #   green:  ALL known checks passed AND ≥ 3 known checks (need evidence)
    #   red:    ≥ 2 checks failed (independent failures)
    #   yellow: otherwise — mixed signals, incomplete, or single failure
    if n_failed >= 2:
        level = "red"
        label = "NOISE-FIT — do not trust composite"
    elif n_failed == 0 and n_known >= 3:
        level = "green"
        label = "REAL EDGE SIGNAL — ready for holdout"
    else:
        level = "yellow"
        label = "MIXED — investigate before holdout"

    return {
        "level": level,
        "label": label,
        "checks": checks,
        "headline_sharpe": {
            "raw": raw_sharpe,
            "bhy": bhy_sharpe,
            "haircut_pct": bhy_haircut_pct,
        },
        "sign_agreement": sign_agreement,
        "stitched": stitched,
    }


def _sanitize(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #
@app.get("/api/strategies")
def api_strategies():
    if not STRATS.exists():
        return []
    out = []
    for p in sorted(STRATS.iterdir()):
        if not p.is_dir() or not (p / "strategy.py").exists():
            continue
        best = None
        bp = p / "runs" / "best.json"
        if bp.exists():
            try:
                best = json.loads(bp.read_text(encoding="utf-8"))
            except Exception:
                pass
        hist_path = p / "runs" / "history.jsonl"
        n_iters, first_started, best_pnl, best_pnl_iter = _history_summary(hist_path)
        out.append({
            "name": p.name,
            "description": _extract_description(p / "strategy.py"),
            "best_composite": (best or {}).get("composite"),
            "best_iter": (best or {}).get("iter"),
            "best_pnl": best_pnl,
            "best_pnl_iter": best_pnl_iter,
            "n_iters": n_iters,
            "first_started": first_started,
        })
    out.sort(
        key=lambda row: (
            row.get("best_pnl") is None,
            -(row.get("best_pnl") or 0.0),
            row["name"],
        )
    )
    return _sanitize(out)


@app.get("/api/strategies/{name}")
def api_strategy(name: str):
    d = _strategy_dir(name)
    program = (d / "program.md").read_text(encoding="utf-8") if (d / "program.md").exists() else ""
    code = (d / "strategy.py").read_text(encoding="utf-8")
    best = _load_best(name)
    history = _load_history(name)
    trust = _compute_trust_verdict(name, best, history)
    return _sanitize({
        "name": name,
        "description": _extract_description(d / "strategy.py"),
        "best": best,
        "history": history,
        "program_md": program,
        "strategy_py": code,
        "trust_verdict": trust,
    })


@app.get("/api/strategies/{name}/equity/{iter_id}")
def api_equity(name: str, iter_id: int):
    d = _strategy_dir(name)
    p = d / "runs" / "equity" / f"iter_{iter_id:04d}.parquet"
    if not p.exists():
        raise HTTPException(404, f"no equity for iter {iter_id}")
    df = pd.read_parquet(p)

    cutoffs: list[str] = []
    cp = d / "runs" / "equity" / f"iter_{iter_id:04d}.json"
    if cp.exists():
        try:
            j = json.loads(cp.read_text())
            cutoffs = j.get("split_cutoffs") or ([j["split_cutoff"]] if j.get("split_cutoff") else [])
        except Exception:
            pass

    has_windows = "window" in df.columns
    windows = []
    groups = df.groupby("window") if has_windows else [(0, df)]
    for w, g in groups:
        g = g.sort_values("timestamp")
        ts = pd.to_datetime(g["timestamp"], utc=True)
        item = {
            "window": int(w),
            "timestamp": [t.isoformat() for t in ts],
            "equity": g["equity"].tolist(),
            "benchmark": g["benchmark"].tolist(),
            "split_cutoff": cutoffs[int(w)] if int(w) < len(cutoffs) else None,
        }
        windows.append(item)

    # Back-compat fields (single-window callers can keep using these for the
    # first window without checking has_windows).
    head = windows[0] if windows else {}
    return _sanitize({
        "iter": iter_id,
        "windows": windows,
        "n_windows": len(windows),
        # legacy fields, mirror the first window
        "timestamp": head.get("timestamp", []),
        "equity": head.get("equity", []),
        "benchmark": head.get("benchmark", []),
        "split_cutoff": head.get("split_cutoff"),
    })


@app.get("/api/strategies/{name}/monthly-returns/{iter_id}")
def api_monthly_returns(name: str, iter_id: int):
    """Monthly compounded returns for an iter, suitable for a heatmap.

    Computed on the SAVED equity curve (post-funding-adjusted, since
    that's what the harness writes). For walk-forward iters the
    equity parquet has a `window` column; we compound across windows
    in chronological order so the heatmap shows the strategy's full
    realised return path.

    Response shape:
      years: [2024, 2025, 2026]
      months: [1..12]                (always)
      data: [[ret_2024_jan, ret_2024_feb, ..., None for missing], ...]
      Where each cell is a fraction (0.05 = +5%) or null.
    """
    d = _strategy_dir(name)
    p = d / "runs" / "equity" / f"iter_{iter_id:04d}.parquet"
    if not p.exists():
        raise HTTPException(404, f"no equity for iter {iter_id}")
    df = pd.read_parquet(p)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp")

    if "window" in df.columns:
        # Stitch windows: each window's equity is rebased to its own
        # init_cash. To get a continuous "if you ran this strategy
        # forever" curve, compound returns across windows by chaining
        # the percentage paths.
        rets = []
        for _, g in df.groupby("window"):
            g = g.sort_values("timestamp")
            r = g["equity"].pct_change().fillna(0.0)
            rets.append(pd.Series(r.values, index=g["timestamp"]))
        rets_concat = pd.concat(rets).sort_index()
    else:
        rets_concat = df.set_index("timestamp")["equity"].pct_change().fillna(0.0)

    # Build a synthetic continuous equity, compound it, then resample.
    # Per-month compounded return computed from per-bar returns, NOT
    # via pct_change() of monthly-last:
    #   - pct_change-of-last drops the first month entirely (NaN first
    #     value silently removed by .dropna()) — Jan 2024 was missing.
    #   - (last / first) - 1 within each month loses the transition bar
    #     between months (end-of-A → start-of-B) — slight undercounting.
    # Using prod(1 + r) on the per-bar returns over each month assigns
    # every bar to exactly one month, no gap or double-count, and the
    # first month is included naturally.
    monthly = ((1.0 + rets_concat).resample("MS").prod() - 1.0).dropna()

    if monthly.empty:
        return _sanitize({
            "iter": iter_id, "years": [], "months": list(range(1, 13)), "data": [],
        })

    pivot = pd.DataFrame({
        "ret": monthly.values,
        "year": monthly.index.year,
        "month": monthly.index.month,
    }).pivot(index="year", columns="month", values="ret").reindex(columns=range(1, 13))

    years = [int(y) for y in pivot.index.tolist()]
    data: list[list] = []
    for y in pivot.index:
        row = []
        for m in range(1, 13):
            v = pivot.loc[y, m] if m in pivot.columns else float("nan")
            if pd.isna(v):
                row.append(None)
            else:
                row.append(float(v))
        data.append(row)

    # Year-summary column: full-year compounded return where the year is
    # complete-ish (>= 6 months of data). Useful for the "% per year if
    # set-and-forget" question.
    year_returns: list = []
    for y in years:
        row_vals = pivot.loc[y].dropna()
        if len(row_vals) == 0:
            year_returns.append(None)
        else:
            yr = float((1.0 + row_vals).prod() - 1.0)
            year_returns.append(yr)

    return _sanitize({
        "iter": iter_id,
        "years": years,
        "months": list(range(1, 13)),
        "data": data,
        "year_returns": year_returns,
        "n_months": int(len(monthly)),
    })


class IterateRequest(BaseModel):
    start: str = "2024-01-01"
    end: str = "2026-01-01"
    # None means defer to strategy.py:DEFAULT_TF. Hard-coded "1h" here was
    # a real bug — it silently overrode 4h/15m strategies, breaking the
    # entire run because vol_lookback / bars_per_day were calibrated for
    # the strategy's intended TF.
    tf: str | None = None
    walk: int = 4
    expanding_wf: bool = False
    note: str = ""


@app.post("/api/strategies/{name}/iterate")
def api_iterate(name: str, body: IterateRequest):
    _strategy_dir(name)
    cmd = [
        sys.executable, "-m", "runner.iterate",
        f"strategies/{name}",
        "--start", body.start,
        "--end", body.end,
        "--walk", str(body.walk),
        "--note", body.note,
    ]
    # Only forward --tf when explicitly provided. Otherwise iterate.py reads
    # DEFAULT_TF from strategy.py.
    if body.tf:
        cmd.extend(["--tf", body.tf])
    if body.expanding_wf:
        cmd.append("--expanding-wf")
    job = _start_job(cmd)
    return job.to_json()


@app.get("/api/strategies/{name}/tearsheet/{iter_id}")
def api_tearsheet(name: str, iter_id: int):
    d = _strategy_dir(name)
    p = d / "runs" / "tearsheets" / f"iter_{iter_id:04d}.html"
    if not p.exists():
        raise HTTPException(404, f"no tearsheet for iter {iter_id}")
    return FileResponse(p, media_type="text/html")


@app.get("/api/strategies/{name}/trades/{iter_id}")
def api_trades(name: str, iter_id: int):
    """Return trade ledger for an iteration plus quick summary stats.

    Capped at 50000 rows in the response — enough for multi-symbol baskets
    over multi-year periods (24 symbols * 24 months * ~500 trades = ~10k).
    The full ledger remains on disk at runs/trades/iter_NNNN.parquet.
    """
    d = _strategy_dir(name)
    p = d / "runs" / "trades" / f"iter_{iter_id:04d}.parquet"
    if not p.exists():
        raise HTTPException(404, f"no trades for iter {iter_id}")
    df = pd.read_parquet(p)
    n_total = len(df)

    def _summarize(sub: pd.DataFrame) -> dict:
        n = len(sub)
        if n == 0 or "pnl_quote" not in sub.columns:
            return {"n_trades": int(n)}
        wins = sub[sub["pnl_quote"] > 0]
        losses = sub[sub["pnl_quote"] < 0]
        return {
            "n_trades": int(n),
            "n_wins": int(len(wins)),
            "n_losses": int(len(losses)),
            "win_rate": float(len(wins) / n) if n else 0.0,
            "avg_win": float(wins["pnl_quote"].mean()) if len(wins) else 0.0,
            "avg_loss": float(losses["pnl_quote"].mean()) if len(losses) else 0.0,
            "payoff_ratio": (float(wins["pnl_quote"].mean() / -losses["pnl_quote"].mean())
                             if len(wins) and len(losses) else 0.0),
            "total_pnl": float(sub["pnl_quote"].sum()),
            "median_duration_hours": float(sub.get("duration_hours", pd.Series([0.0])).median()),
        }

    summary = _summarize(df)
    # Per-side breakdown so the user can see whether longs and shorts
    # contributed equally to PnL or one side carried the strategy.
    if "direction" in df.columns and not df.empty:
        long_df = df[df["direction"].astype(str).str.lower().str.startswith("long")]
        short_df = df[df["direction"].astype(str).str.lower().str.startswith("short")]
        summary["long"] = _summarize(long_df)
        summary["short"] = _summarize(short_df)

    head = df.sort_values("pnl_quote", ascending=False, na_position="last").head(20) \
        if "pnl_quote" in df.columns else df.head(20)
    tail = df.sort_values("pnl_quote", ascending=True, na_position="last").head(20) \
        if "pnl_quote" in df.columns else df.tail(20)
    rows = df.head(50000)

    def _to_records(frame: pd.DataFrame) -> list[dict]:
        out_rows = []
        for r in frame.to_dict(orient="records"):
            for k, v in list(r.items()):
                if isinstance(v, pd.Timestamp):
                    r[k] = v.isoformat()
            out_rows.append(r)
        return out_rows

    return _sanitize({
        "iter": iter_id,
        "summary": summary,
        "rows": _to_records(rows),
        "row_count_total": n_total,
        "top_winners": _to_records(head),
        "top_losers": _to_records(tail),
    })


class HoldoutRequest(BaseModel):
    start: str = "2026-01-01"
    end: str = "2026-05-01"
    # tf=None means "read DEFAULT_TF from strategy.py". Hard-coded "1h" here
    # was a real bug — it silently overrode strategies that declared 4h/15m/etc.
    # and the holdout report came back as nonsense.
    tf: str | None = None


@app.post("/api/strategies/{name}/holdout")
def api_holdout(name: str, body: HoldoutRequest):
    _strategy_dir(name)
    cmd = [
        sys.executable, "-m", "runner.holdout",
        f"strategies/{name}",
        "--start", body.start,
        "--end", body.end,
    ]
    if body.tf:
        cmd.extend(["--tf", body.tf])
    return _start_job(cmd).to_json()


@app.get("/api/strategies/{name}/holdout")
def api_holdout_report(name: str):
    """Return the most recent holdout report (matched to current best.json iter, if any)."""
    d = _strategy_dir(name)
    holdout_dir = d / "runs" / "holdout"
    if not holdout_dir.exists():
        return None
    reports = sorted(holdout_dir.glob("holdout_iter_*.json"))
    if not reports:
        return None
    latest = reports[-1]
    rep = json.loads(latest.read_text(encoding="utf-8"))
    parquet = latest.with_suffix(".parquet")
    curve = None
    if parquet.exists():
        df = pd.read_parquet(parquet)
        curve = {
            "timestamp": [t.isoformat() for t in pd.to_datetime(df["timestamp"], utc=True)],
            "equity": df["equity"].tolist(),
            "benchmark": df["benchmark"].tolist(),
        }
    return _sanitize({"report": rep, "equity": curve})


_TF_LADDER = [
    ("1min", 1), ("5min", 5), ("15min", 15), ("30min", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("8h", 480),
    ("12h", 720), ("1D", 1440),
]
_MAX_BARS = 5000


def _coarsen_tf(tf: str, start: str, end: str) -> str:
    """Pick the smallest tf in the ladder whose bar count over [start,end)
    is <= _MAX_BARS, never finer than the requested ``tf``. Falls back to
    the coarsest available if even 1D exceeds the cap (it won't for any
    realistic period). Unknown tfs pass through unchanged.
    """
    try:
        period_minutes = (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60.0
    except Exception:
        return tf
    if period_minutes <= 0:
        return tf
    requested_idx = next((i for i, (name, _) in enumerate(_TF_LADDER) if name == tf), None)
    if requested_idx is None:
        return tf
    for name, minutes in _TF_LADDER[requested_idx:]:
        if period_minutes / minutes <= _MAX_BARS:
            return name
    return _TF_LADDER[-1][0]


@app.get("/api/data/ohlcv")
def api_ohlcv(symbol: str, start: str, end: str, tf: str = "1h"):
    """OHLCV bars for a symbol over [start, end), resampled to ``tf``.

    Used by the PriceChart component to overlay trade markers on the
    actual asset price (not equity). Volume is included so the UI can
    optionally annotate participation. Response is capped at 5000 bars;
    if the requested period × tf would exceed that, the tf is auto-
    coarsened to the smallest one that fits, and the actual tf used is
    returned in the ``tf`` field of the payload (may differ from the
    requested one).
    """
    from datafeed.loader import load
    effective_tf = _coarsen_tf(tf, start, end)
    try:
        df = load(symbol, start, end, tf=effective_tf)
    except Exception as e:
        raise HTTPException(500, f"loader error: {type(e).__name__}: {e}")
    if df.empty:
        raise HTTPException(404, f"no data for {symbol} {start}..{end} {effective_tf}")

    return _sanitize({
        "symbol": symbol,
        "tf": effective_tf,
        "tf_requested": tf,
        "start": start,
        "end": end,
        "n_bars": int(len(df)),
        "timestamp": [t.isoformat() for t in df.index],
        "open": df["open"].astype(float).tolist(),
        "high": df["high"].astype(float).tolist(),
        "low": df["low"].astype(float).tolist(),
        "close": df["close"].astype(float).tolist(),
        "volume": df["volume"].astype(float).tolist(),
    })


class PortfolioComponentBody(BaseModel):
    strategy: str
    capital: float
    tf: str | None = None


class PortfolioRequest(BaseModel):
    components: list[PortfolioComponentBody]
    start: str = "2024-01-01"
    end: str = "2026-01-01"
    embargo: str | None = "1D"
    lookback: str | None = "60D"
    cost_model: str = "static"


@app.post("/api/portfolio/run")
def api_portfolio_run(body: PortfolioRequest):
    """Run a multi-strategy portfolio with operator-specified capital
    allocations. Returns combined equity, per-strategy contributions,
    portfolio metrics, and the strategies' return-correlation matrix.

    Each component is backtested independently (Path B) with its own
    capital — no shared cash pool, no position aggregation. Realistic
    for sub-strategies running on separate sub-accounts.

    Slow on first call (re-runs each strategy backtest, ~30s each).
    No cache — rerun cost is acceptable for the small number of
    strategies a researcher typically combines.
    """
    from runner.portfolio import run_portfolio, PortfolioComponent
    if not body.components:
        raise HTTPException(400, "at least one component required")
    components = [
        PortfolioComponent(strategy=c.strategy, capital=c.capital, tf=c.tf)
        for c in body.components
    ]
    for c in components:
        if not (STRATS / c.strategy).exists():
            raise HTTPException(404, f"unknown strategy: {c.strategy}")
        if c.capital <= 0:
            raise HTTPException(400, f"capital must be > 0 (got {c.capital} for {c.strategy})")

    try:
        rep = run_portfolio(
            components,
            period_start=body.start,
            period_end=body.end,
            embargo=body.embargo,
            lookback=body.lookback,
            cost_model=body.cost_model,
        )
    except Exception as e:
        raise HTTPException(500, f"portfolio run failed: {type(e).__name__}: {e}")

    return _sanitize(rep)


@app.get("/api/portfolio/strategies")
def api_portfolio_list_strategies():
    """List strategies available for portfolio composition. Each entry
    includes minimal metadata from best.json so the UI can show iter
    number / composite without an extra fetch per strategy."""
    if not STRATS.exists():
        return []
    out = []
    for d in sorted(p for p in STRATS.iterdir() if p.is_dir()):
        best_path = d / "runs" / "best.json"
        meta = {"name": d.name, "best_iter": None, "best_composite": None,
                "tf": None, "symbols": []}
        if best_path.exists():
            try:
                b = json.loads(best_path.read_text(encoding="utf-8"))
                meta["best_iter"] = b.get("iter")
                meta["best_composite"] = b.get("composite")
                meta["tf"] = b.get("tf")
                meta["symbols"] = b.get("symbols", [])
            except Exception:
                pass
        out.append(meta)
    return out


# --------------------------------------------------------------------------- #
# Research-integrity endpoints — bootstrap p-values, haircut Sharpe, PBO.
# --------------------------------------------------------------------------- #
@app.get("/api/strategies/{name}/research-stats")
def api_research_stats(name: str):
    """Session-level research-integrity dashboard data.

    Aggregates across the whole iter history:
      - trial Sharpe summary (histogram + expected-max-under-null)
      - session overfit stats (IS↔OOS Spearman/slope/logit_overfit)
      - latest iter's bootstrap p-values (block + permutation)
      - latest iter's Harvey-Liu haircut Sharpe (Bonferroni/Holm/BHY)

    Source of truth: history.jsonl entries' `research_stats` block,
    which is populated by runner.iterate.run_one. For sessions iterated
    before this endpoint existed, returns whatever subset is available
    (older rows lack research_stats — those iters are silently skipped).
    """
    from harness import multiple_testing as _mt
    from harness import pbo as _pbo

    history = _load_history(name)
    if not history:
        return _sanitize({"history_present": False})

    # Pull all (train_sharpe, oos_sharpe) pairs (across the full history).
    train_sh: list[float] = []
    oos_sh: list[float] = []
    for row in history:
        tr = (row.get("metrics_train") or {}).get("sharpe")
        os_ = (row.get("metrics_oos") or {}).get("sharpe")
        if tr is not None and os_ is not None:
            train_sh.append(float(tr))
            oos_sh.append(float(os_))

    trial_summary = _mt.trial_sharpe_summary(oos_sh)
    session_overfit = (_pbo.session_overfit_stats(train_sh, oos_sh)
                       if len(train_sh) >= 4 else None)

    # Latest iter's persisted research_stats (already includes block
    # bootstrap + haircut). Fall back to the most recent row that
    # has the block populated.
    latest_rs = None
    latest_iter = None
    for row in reversed(history):
        rs = row.get("research_stats")
        if rs and rs.get("bootstrap"):
            latest_rs = rs
            latest_iter = row.get("iter")
            break

    # Per-iter p-values trajectory for plotting over the session.
    per_iter = []
    for row in history:
        rs = row.get("research_stats") or {}
        block = ((rs.get("bootstrap") or {}).get("block") or {})
        pvals = block.get("p_values") or {}
        bhy = (rs.get("haircut_sharpe") or {}).get("bhy") or {}
        per_iter.append({
            "iter": row.get("iter"),
            "verdict": row.get("verdict"),
            "composite": row.get("composite"),
            "oos_sharpe": (row.get("metrics_oos") or {}).get("sharpe"),
            "dsr": row.get("dsr"),
            "p_sharpe_block": pvals.get("sharpe"),
            "p_max_dd_block": pvals.get("max_dd"),
            "bhy_sharpe": bhy.get("sharpe"),
            "bhy_haircut_pct": bhy.get("haircut_pct"),
        })

    return _sanitize({
        "history_present": True,
        "n_iters": len(history),
        "trial_sharpes": trial_summary,
        "session_overfit": session_overfit,
        "latest": {"iter": latest_iter, "research_stats": latest_rs},
        "per_iter": per_iter,
    })


@app.get("/api/strategies/{name}/cpcv")
def api_cpcv_latest(name: str):
    """Latest CPCV report for the strategy. None if never run."""
    d = _strategy_dir(name)
    cpcv_dir = d / "runs" / "cpcv"
    if not cpcv_dir.exists():
        return None
    reports = sorted(cpcv_dir.glob("cpcv_*.json"))
    if not reports:
        return None
    latest = reports[-1]
    rep = json.loads(latest.read_text(encoding="utf-8"))
    # Per-path table (sharpes & IS/OOS) for the IS-vs-OOS scatter plot.
    parquet = latest.with_suffix(".parquet")
    paths_table = None
    if parquet.exists():
        df = pd.read_parquet(parquet)
        paths_table = df.to_dict(orient="records")
    return _sanitize({"report": rep, "paths": paths_table,
                      "report_file": latest.name})


@app.get("/api/strategies/{name}/cpcv/list")
def api_cpcv_list(name: str):
    """List all CPCV report files for this strategy (most-recent first)."""
    d = _strategy_dir(name)
    cpcv_dir = d / "runs" / "cpcv"
    if not cpcv_dir.exists():
        return []
    reports = sorted(cpcv_dir.glob("cpcv_*.json"), reverse=True)
    out = []
    for p in reports:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "file": p.name,
                "iter": r.get("iter"),
                "ran_at": r.get("ran_at"),
                "n_paths": r.get("n_paths"),
                "n_groups": r.get("n_groups"),
                "k_test": r.get("k_test"),
                "median_sharpe": (r.get("summary") or {}).get("median_sharpe"),
                "overfit_verdict": r.get("overfit_verdict"),
                "spearman_is_oos": (r.get("overfit") or {}).get("spearman_is_oos"),
            })
        except Exception:
            pass
    return _sanitize(out)


class CpcvRequest(BaseModel):
    start: str = "2024-01-01"
    end: str = "2026-01-01"
    tf: str | None = None
    n_groups: int = 10
    k_test: int = 2
    embargo: str | None = "1D"
    cost_model: str = "static"


@app.post("/api/strategies/{name}/cpcv")
def api_cpcv_run(name: str, body: CpcvRequest):
    """Kick off a CPCV run as a background job. Returns the job descriptor
    immediately; poll /api/jobs/{id} for completion and then /api/cpcv
    for the report."""
    _strategy_dir(name)
    cmd = [
        sys.executable, "-m", "runner.cpcv",
        f"strategies/{name}",
        "--start", body.start,
        "--end", body.end,
        "--n-groups", str(body.n_groups),
        "--k-test", str(body.k_test),
        "--cost-model", body.cost_model,
    ]
    if body.embargo:
        cmd.extend(["--embargo", body.embargo])
    if body.tf:
        cmd.extend(["--tf", body.tf])
    return _start_job(cmd).to_json()


# --------------------------------------------------------------------------- #
# Forward-test endpoints — post-holdout / live-paper analogue + drift.
# --------------------------------------------------------------------------- #
class ForwardRequest(BaseModel):
    start: str | None = None
    end: str | None = None
    tf: str | None = None
    lookback: str | None = "60D"


@app.post("/api/strategies/{name}/forward/run")
def api_forward_run(name: str, body: ForwardRequest):
    """Kick off runner.forward as a background job."""
    _strategy_dir(name)
    cmd = [
        sys.executable, "-m", "runner.forward",
        f"strategies/{name}",
        "--lookback", body.lookback or "60D",
    ]
    if body.start:
        cmd.extend(["--start", body.start])
    if body.end:
        cmd.extend(["--end", body.end])
    if body.tf:
        cmd.extend(["--tf", body.tf])
    return _start_job(cmd).to_json()


@app.get("/api/strategies/{name}/forward")
def api_forward_latest(name: str):
    """Return the latest forward-test report + equity curve for ``name``.

    Source: ``runs/forward/latest.json`` points to the canonical report.
    Falls back to the most-recent ``forward_*.json`` on disk.
    """
    d = _strategy_dir(name)
    fwd_dir = d / "runs" / "forward"
    if not fwd_dir.exists():
        return None
    latest_ptr = fwd_dir / "latest.json"
    target: Path | None = None
    if latest_ptr.exists():
        try:
            ptr = json.loads(latest_ptr.read_text(encoding="utf-8"))
            candidate = fwd_dir / ptr.get("file", "")
            if candidate.exists():
                target = candidate
        except Exception:
            pass
    if target is None:
        reports = sorted(fwd_dir.glob("forward_*.json"))
        if not reports:
            return None
        target = reports[-1]

    rep = json.loads(target.read_text(encoding="utf-8"))
    parquet = target.with_suffix(".parquet")
    curve = None
    if parquet.exists():
        df = pd.read_parquet(parquet)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        curve = {
            "timestamp": [t.isoformat() for t in df["timestamp"]],
            "equity": df["equity"].astype(float).tolist(),
            "benchmark": df["benchmark"].astype(float).tolist(),
        }
        if "rolling_sharpe_30d" in df.columns:
            curve["rolling_sharpe_30d"] = [
                None if pd.isna(v) else float(v)
                for v in df["rolling_sharpe_30d"]
            ]
    return _sanitize({"report": rep, "equity": curve,
                      "report_file": target.name})


@app.get("/api/strategies/{name}/forward/list")
def api_forward_list(name: str):
    """All forward reports for the strategy (most-recent first), summary only."""
    d = _strategy_dir(name)
    fwd_dir = d / "runs" / "forward"
    if not fwd_dir.exists():
        return []
    out = []
    for p in sorted(fwd_dir.glob("forward_*.json"), reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            drift = r.get("drift") or {}
            out.append({
                "file": p.name,
                "ran_at": r.get("ran_at"),
                "period": r.get("period"),
                "iter": r.get("iter"),
                "snapshot_used": r.get("snapshot_used"),
                "forward_sharpe": drift.get("forward_sharpe"),
                "forward_max_dd": drift.get("forward_max_dd"),
                "forward_psr": drift.get("forward_psr"),
                "flag": drift.get("flag"),
            })
        except Exception:
            pass
    return _sanitize(out)


@app.get("/api/forward/summary")
def api_forward_summary():
    """Cross-strategy snapshot: each strategy's latest forward flag.
    Suitable for a top-level "what's drifting" overview."""
    if not STRATS.exists():
        return []
    out = []
    for d in sorted(p for p in STRATS.iterdir() if p.is_dir()):
        if d.name.startswith("_") or not (d / "strategy.py").exists():
            continue
        fwd_dir = d / "runs" / "forward"
        meta: dict[str, Any] = {
            "name": d.name, "has_forward": False, "flag": None,
            "ran_at": None, "forward_sharpe": None,
            "backtest_sharpe": None, "backtest_sharpe_ci_lo": None,
            "backtest_sharpe_ci_hi": None,
        }
        if fwd_dir.exists():
            reports = sorted(fwd_dir.glob("forward_*.json"), reverse=True)
            if reports:
                try:
                    r = json.loads(reports[0].read_text(encoding="utf-8"))
                    drift = r.get("drift") or {}
                    meta.update({
                        "has_forward": True,
                        "flag": drift.get("flag"),
                        "ran_at": r.get("ran_at"),
                        "forward_sharpe": drift.get("forward_sharpe"),
                        "backtest_sharpe": drift.get("backtest_sharpe"),
                        "backtest_sharpe_ci_lo": drift.get("backtest_sharpe_ci_lo"),
                        "backtest_sharpe_ci_hi": drift.get("backtest_sharpe_ci_hi"),
                        "consecutive_below_ci_days": drift.get("consecutive_below_ci_days"),
                        "period": r.get("period"),
                    })
                except Exception:
                    pass
        out.append(meta)
    return _sanitize(out)


# --------------------------------------------------------------------------- #
# Sweep endpoints — cross-symbol × cross-period robustness matrix.
# --------------------------------------------------------------------------- #
class SweepRequest(BaseModel):
    # Symbol selection — pick exactly one mode.
    symbols: list[str] | None = None
    all_symbols: bool = False
    all_symbols_covered: bool = False
    top_n: int | None = None
    coverage_min: float = 0.90
    # Periods (list of presets / "YYYY-MM:YYYY-MM").
    periods: list[str] = ["2024", "2025", "2026"]
    # Backtest knobs.
    tf: str | None = None
    wf: int = 1
    no_wf: bool = False
    cost_model: str = "static"
    embargo: str = "1D"
    lookback: str = "60D"
    parallel: int | None = None
    tag: str = ""


@app.get("/api/symbols")
def api_symbols():
    """List symbols available on disk, plus a coarse coverage summary
    over the 2024-01 .. 2026-05 span — drives the sweep symbol picker.
    """
    from datafeed.loader import available_symbols, DATA_ROOT
    out = []
    for sym in available_symbols():
        d = DATA_ROOT / sym
        try:
            months = sorted(p.stem for p in d.glob("*.parquet"))
        except Exception:
            months = []
        out.append({
            "symbol": sym,
            "n_months": len(months),
            "first_month": months[0] if months else None,
            "last_month": months[-1] if months else None,
        })
    return _sanitize(out)


@app.post("/api/strategies/{name}/sweep")
def api_sweep_run(name: str, body: SweepRequest):
    """Kick off runner.sweep as a background job. Returns the job handle —
    poll /api/jobs/{jid} for progress and parse the JSON tail for sweep_id."""
    _strategy_dir(name)
    cmd = [sys.executable, "-m", "runner.sweep", f"strategies/{name}"]

    # Symbol selection — backend mirrors CLI exclusivity.
    selection_count = sum([
        bool(body.symbols), body.all_symbols,
        body.all_symbols_covered, bool(body.top_n),
    ])
    if selection_count != 1:
        raise HTTPException(400, "provide exactly one of: symbols / "
                                 "all_symbols / all_symbols_covered / top_n")

    if body.symbols:
        cmd.append("--symbols")
        cmd.extend(body.symbols)
    elif body.all_symbols:
        cmd.append("--all-symbols")
    elif body.all_symbols_covered:
        cmd.append("--all-symbols-covered")
        cmd.extend(["--coverage-min", str(body.coverage_min)])
    elif body.top_n:
        cmd.extend(["--top", str(body.top_n)])

    if body.periods:
        cmd.append("--periods")
        cmd.extend(body.periods)
    if body.tf:
        cmd.extend(["--tf", body.tf])
    if body.no_wf:
        cmd.append("--no-wf")
    else:
        cmd.extend(["--wf", str(body.wf)])
    cmd.extend(["--cost-model", body.cost_model,
                "--embargo", body.embargo,
                "--lookback", body.lookback])
    if body.parallel:
        cmd.extend(["--parallel", str(body.parallel)])
    if body.tag:
        cmd.extend(["--tag", body.tag])

    return _start_job(cmd).to_json()


@app.get("/api/strategies/{name}/sweep/list")
def api_sweep_list(name: str):
    """List all completed sweeps for a strategy, newest first."""
    d = _strategy_dir(name)
    sweeps_dir = d / "sweeps"
    if not sweeps_dir.exists():
        return []
    out = []
    for sub in sorted([p for p in sweeps_dir.iterdir() if p.is_dir()],
                       reverse=True):
        mf = sub / "manifest.json"
        if not mf.exists():
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Lightweight headline metrics from report.json if present.
        rep_path = sub / "report.json"
        glob = None
        if rep_path.exists():
            try:
                glob = (json.loads(rep_path.read_text(encoding="utf-8"))
                        or {}).get("global")
            except Exception:
                pass
        # Progress info for in-flight sweeps.
        prog_path = sub / "progress.json"
        progress = None
        if prog_path.exists():
            try:
                progress = json.loads(prog_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        out.append({
            "sweep_id": m.get("sweep_id") or sub.name,
            "created_at": m.get("created_at"),
            "finished_at": m.get("finished_at"),
            "tag": m.get("tag"),
            "tf": m.get("tf"),
            "n_symbols": len(m.get("symbols") or []),
            "n_periods": len(m.get("periods") or []),
            "n_cells": m.get("n_cells"),
            "n_errors": m.get("n_errors"),
            "duration_s": m.get("duration_s"),
            "selection_mode": m.get("selection_mode"),
            "cost_model": m.get("cost_model"),
            "strategy_sha256": m.get("strategy_sha256"),
            "global": glob,
            "progress": progress,
        })
    return _sanitize(out)


def _sweep_dir(name: str, sweep_id: str) -> Path:
    d = _strategy_dir(name) / "sweeps" / sweep_id
    if not d.exists():
        raise HTTPException(404, f"sweep '{sweep_id}' not found for '{name}'")
    return d


@app.get("/api/strategies/{name}/sweep/{sweep_id}")
def api_sweep_get(name: str, sweep_id: str):
    """Full sweep payload: manifest + report + summary rows."""
    d = _sweep_dir(name, sweep_id)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    report = None
    rep_path = d / "report.json"
    if rep_path.exists():
        report = json.loads(rep_path.read_text(encoding="utf-8"))
    summary: list[dict] = []
    sum_path = d / "summary.parquet"
    if sum_path.exists():
        try:
            df = pd.read_parquet(sum_path)
            summary = df.to_dict(orient="records")
        except Exception:
            pass
    prog_path = d / "progress.json"
    progress = None
    if prog_path.exists():
        try:
            progress = json.loads(prog_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _sanitize({
        "manifest": manifest,
        "report": report,
        "summary": summary,
        "progress": progress,
    })


@app.get("/api/strategies/{name}/sweep/{sweep_id}/equity")
def api_sweep_equity(name: str, sweep_id: str, symbol: str, period: str):
    """Equity curve for one (symbol, period) cell, downsampled for plotting."""
    d = _sweep_dir(name, sweep_id)
    fp = d / "equity" / f"{symbol}__{period}.parquet"
    if not fp.exists():
        raise HTTPException(404, "equity curve not found for that cell")
    df = pd.read_parquet(fp)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp")
    # Downsample for the UI.
    max_pts = 3000
    if len(df) > max_pts:
        step = (len(df) // max_pts) + 1
        df = df.iloc[::step]
    curve = {
        "timestamp": [t.isoformat() for t in df["timestamp"]],
        "equity": df["equity"].astype(float).tolist(),
        "benchmark": [None if pd.isna(v) else float(v)
                       for v in df["benchmark"]],
    }
    if "window" in df.columns:
        curve["window"] = df["window"].astype(int).tolist()
    return _sanitize(curve)


@app.get("/api/strategies/{name}/sweep/{sweep_id}/correlations")
def api_sweep_correlations(name: str, sweep_id: str):
    """N×N OOS-returns correlation matrix across symbols (None if not computed)."""
    d = _sweep_dir(name, sweep_id)
    fp = d / "correlations.parquet"
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    return _sanitize({
        "symbols": [str(s) for s in df.columns],
        "matrix": [[None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
                     else float(v)
                     for v in row]
                    for row in df.values],
    })


# --------------------------------------------------------------------------- #
# Feature store endpoints — list / metadata / preview / coverage.
# --------------------------------------------------------------------------- #
@app.get("/api/features")
def api_features_list():
    """All registered features with metadata."""
    import features as _feat
    out = []
    for name in _feat.list_features():
        try:
            out.append(_feat.feature_meta(name))
        except Exception:
            pass
    return _sanitize(out)


@app.get("/api/features/{name}/coverage")
def api_feature_coverage(name: str):
    """What's already cached on disk for ``name`` — list of (symbol, tf, months)."""
    import features as _feat
    try:
        return _sanitize(_feat.coverage_table(name))
    except KeyError:
        raise HTTPException(404, f"unknown feature: {name}")


@app.get("/api/features/{name}/preview")
def api_feature_preview(name: str, symbol: str = "BTCUSDT",
                        start: str = "2025-01-01",
                        end: str = "2025-04-01",
                        tf: str = "1h"):
    """Compute a feature over [start, end) and return a downsampled
    series suitable for plotting. Cached if previously requested.
    """
    import features as _feat
    try:
        series = _feat.compute(name, symbol, start, end, tf=tf, use_cache=True)
    except KeyError:
        raise HTTPException(404, f"unknown feature: {name}")
    except Exception as e:
        raise HTTPException(500, f"compute failed: {type(e).__name__}: {e}")
    if series.empty:
        return _sanitize({
            "name": name, "symbol": symbol, "tf": tf,
            "start": start, "end": end,
            "timestamp": [], "values": [], "n_points": 0,
        })
    # Hard-cap plotting points; downsample uniformly if too dense.
    max_pts = 2000
    n = len(series)
    if n > max_pts:
        step = (n // max_pts) + 1
        series = series.iloc[::step]
    quantiles = series.dropna().quantile([0.05, 0.25, 0.5, 0.75, 0.95]).tolist() \
        if not series.dropna().empty else [None] * 5
    return _sanitize({
        "name": name, "symbol": symbol, "tf": tf,
        "start": start, "end": end,
        "timestamp": [t.isoformat() for t in series.index],
        "values": [None if pd.isna(v) else float(v) for v in series.values],
        "n_points": int(len(series)),
        "quantiles_05_25_50_75_95": quantiles,
    })


# --------------------------------------------------------------------------- #
# Meta-labeler endpoint — surfaces the per-iter classifier report from
# history.jsonl. Returns None for strategies that don't export META_LABELER.
# --------------------------------------------------------------------------- #
@app.get("/api/strategies/{name}/meta")
def api_strategy_meta(name: str):
    """Latest meta-labeler report for the strategy's current best iter.

    Returns:
      - None if no META_LABELER is configured or no report yet.
      - { "iter": N, "meta": [...] | {...} } where meta is either a
        per-window list (walk-forward) or a single dict (single split).
    """
    d = _strategy_dir(name)
    best = _load_best(name)
    if not best:
        return None
    target_iter = best.get("iter")
    # Walk back through history for the row matching iter; meta_labeler
    # is stored on the row itself by runner.iterate.
    history = _load_history(name)
    row = None
    for h in reversed(history):
        if h.get("iter") == target_iter:
            row = h
            break
    if row is None:
        return None
    meta = row.get("meta_labeler")
    if meta is None:
        return None
    # Aggregate per-window list into a summary card for the UI.
    if isinstance(meta, list):
        windows = meta
        agg = {
            "per_window": meta,
            "n_windows": len(meta),
            "all_ok": all((w or {}).get("status") == "ok" for w in meta),
            "mean_train_accuracy": (
                float(np.mean([(w or {}).get("train_accuracy", 0.0)
                               for w in meta if (w or {}).get("status") == "ok"]))
                if any((w or {}).get("status") == "ok" for w in meta) else None
            ),
            "any_skipped": any((w or {}).get("status") == "skipped" for w in meta),
        }
        return _sanitize({"iter": target_iter, "meta": agg})
    return _sanitize({"iter": target_iter, "meta": meta})


# --------------------------------------------------------------------------- #
# Multi-strategy hypothesis tests (Reality Check / SPA / Romano-Wolf).
# Reports are repo-level (not under any single strategy) — they answer the
# "is the BEST of N strategies significantly > 0?" question.
# --------------------------------------------------------------------------- #
MULTISTRAT_DIR = ROOT / "runs" / "_multistrat"


def _list_multistrat_reports() -> list[Path]:
    if not MULTISTRAT_DIR.exists():
        return []
    return sorted(MULTISTRAT_DIR.glob("multistrat_*.json"), reverse=True)


@app.get("/api/multistrat")
def api_multistrat_latest():
    """Latest multi-strategy report. None if never run.

    Returns the full report JSON plus an inlined per-strategy daily-returns
    table (capped) so the UI can plot per-strategy equity / scatter without
    a second fetch.
    """
    reports = _list_multistrat_reports()
    if not reports:
        return None
    latest = reports[0]
    rep = json.loads(latest.read_text(encoding="utf-8"))
    parquet = latest.with_suffix(".parquet")
    daily_returns = None
    if parquet.exists():
        df = pd.read_parquet(parquet)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp")
        # Build cumulative equity curves (1$ → ...) for plotting.
        curves: dict[str, list] = {}
        for col in df.columns:
            if col == "timestamp":
                continue
            r = df[col].fillna(0.0)
            curves[col] = (1.0 + r).cumprod().tolist()
        daily_returns = {
            "timestamp": [t.isoformat() for t in df["timestamp"]],
            "returns": {c: df[c].tolist() for c in df.columns if c != "timestamp"},
            "equity_curves": curves,
        }
    return _sanitize({
        "report": rep,
        "daily_returns": daily_returns,
        "report_file": latest.name,
    })


@app.get("/api/multistrat/list")
def api_multistrat_list():
    """All multistrat reports (most-recent first), summary only."""
    out = []
    for p in _list_multistrat_reports():
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            tests = r.get("tests") or {}
            rc = tests.get("reality_check") or {}
            spa_ = tests.get("spa") or {}
            rw = tests.get("romano_wolf") or []
            out.append({
                "file": p.name,
                "ran_at": r.get("ran_at"),
                "n_strategies_used": r.get("n_strategies_used"),
                "n_days": r.get("n_days"),
                "reality_check_p": rc.get("p_value"),
                "spa_p_consistent": spa_.get("p_value_consistent"),
                "n_reject_at_05": sum(1 for x in rw if x.get("reject_at_05")),
            })
        except Exception:
            pass
    return _sanitize(out)


class MultiStratRequest(BaseModel):
    strategies: list[str] | None = None
    n_boot: int = 1000
    block_size: int | None = None
    seed: int | None = None
    benchmark: float = 0.0
    join: str = "inner"


@app.post("/api/multistrat/run")
def api_multistrat_run(body: MultiStratRequest):
    """Kick off a multi-strategy test as a background job. Poll /api/jobs/{id}
    for completion, then GET /api/multistrat for the report.
    """
    cmd = [
        sys.executable, "-m", "runner.multistrat",
        "--n-boot", str(body.n_boot),
        "--benchmark", str(body.benchmark),
        "--join", body.join,
    ]
    if body.strategies:
        cmd.extend(["--strategies", ",".join(body.strategies)])
    if body.block_size is not None:
        cmd.extend(["--block-size", str(body.block_size)])
    if body.seed is not None:
        cmd.extend(["--seed", str(body.seed)])
    return _start_job(cmd).to_json()


@app.get("/api/multistrat/candidates")
def api_multistrat_candidates():
    """Strategies eligible to be included in a multistrat run, with
    quick metadata so the UI can show which have an OOS slice on disk.

    Source of truth: each strategy's runs/best.json + the existence of
    runs/equity/iter_<best>.parquet.
    """
    if not STRATS.exists():
        return []
    out = []
    for d in sorted(p for p in STRATS.iterdir() if p.is_dir()):
        if d.name.startswith("_") or not (d / "strategy.py").exists():
            continue
        best_path = d / "runs" / "best.json"
        meta: dict[str, Any] = {
            "name": d.name, "has_best": False,
            "best_iter": None, "composite": None,
            "equity_present": False,
        }
        if best_path.exists():
            try:
                b = json.loads(best_path.read_text(encoding="utf-8"))
                meta["has_best"] = True
                meta["best_iter"] = b.get("iter")
                meta["composite"] = b.get("composite")
                meta["tf"] = b.get("tf")
                if b.get("iter") is not None:
                    eq = d / "runs" / "equity" / f"iter_{int(b['iter']):04d}.parquet"
                    meta["equity_present"] = eq.exists()
            except Exception:
                pass
        out.append(meta)
    return _sanitize(out)


@app.get("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        return [j.to_json() for j in sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)]


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return j.to_json()


@app.delete("/api/jobs/{jid}")
def api_kill(jid: str):
    j = JOBS.get(jid)
    if not j or not j.proc:
        raise HTTPException(404)
    if j.status == "running":
        try:
            if os.name == "nt":
                j.proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                j.proc.terminate()
        except Exception:
            pass
    return j.to_json()


# --------------------------------------------------------------------------- #
# Production: serve frontend/dist (built by `npm run build`)
# --------------------------------------------------------------------------- #
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa_fallback(path: str):
        # Serve static files directly when present, otherwise fall back to index.html
        # so client-side routing (react-router) works on hard-reload of any URL.
        candidate = DIST / path
        if path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
