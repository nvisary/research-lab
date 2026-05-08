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
    cutoff = None
    cp = d / "runs" / "equity" / f"iter_{iter_id:04d}.json"
    if cp.exists():
        try:
            cutoff = json.loads(cp.read_text())["split_cutoff"]
        except Exception:
            pass
    return _sanitize({
        "iter": iter_id,
        "timestamp": [t.isoformat() if hasattr(t, "isoformat") else str(t)
                      for t in pd.to_datetime(df["timestamp"], utc=True)],
        "equity": df["equity"].tolist(),
        "benchmark": df["benchmark"].tolist(),
        "split_cutoff": cutoff,
    })


class IterateRequest(BaseModel):
    start: str = "2025-01-01"
    end: str = "2025-02-01"
    tf: str = "1h"
    walk: int = 0
    note: str = ""


@app.post("/api/strategies/{name}/iterate")
def api_iterate(name: str, body: IterateRequest):
    _strategy_dir(name)
    cmd = [
        sys.executable, "-m", "runner.iterate",
        f"strategies/{name}",
        "--start", body.start,
        "--end", body.end,
        "--tf", body.tf,
        "--walk", str(body.walk),
        "--note", body.note,
    ]
    job = _start_job(cmd)
    return job.to_json()


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
