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
        n_iters = 0
        if hist_path.exists():
            n_iters = sum(1 for _ in hist_path.open(encoding="utf-8") if _.strip())
        out.append({
            "name": p.name,
            "description": _extract_description(p / "strategy.py"),
            "best_composite": (best or {}).get("composite"),
            "best_iter": (best or {}).get("iter"),
            "n_iters": n_iters,
        })
    return _sanitize(out)


@app.get("/api/strategies/{name}")
def api_strategy(name: str):
    d = _strategy_dir(name)
    program = (d / "program.md").read_text(encoding="utf-8") if (d / "program.md").exists() else ""
    code = (d / "strategy.py").read_text(encoding="utf-8")
    return _sanitize({
        "name": name,
        "description": _extract_description(d / "strategy.py"),
        "best": _load_best(name),
        "history": _load_history(name),
        "program_md": program,
        "strategy_py": code,
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

    Capped at 1000 rows in the response to keep the dashboard fast; the
    full ledger remains on disk at runs/trades/iter_NNNN.parquet.
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
    rows = df.head(1000)

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
