"""Reproducibility metadata: capture enough environment context with each
result so the same iteration can be replayed (or at least diagnosed) months
later when libraries have moved on.

Captured fields:
  python:   "3.13.7"
  packages: {pandas, numpy, vectorbt, scipy, ccxt, pyarrow}  (versions only)
  git_sha:  current HEAD if available, else None
  git_dirty: bool — True if working tree has uncommitted changes
  dataset_snapshot: latest mtime of any kline parquet under DATA_ROOT,
                    so we know how recent the price data was when this
                    iter ran (does not record full content hashes — too
                    expensive on a 195-symbol × 28-month set).
  generated_at: ISO timestamp at capture time.

Captured ONCE per process via a small TTL cache to keep iterate() fast.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

_CACHE: dict | None = None
_CACHE_BUILT_AT: float = 0.0
_CACHE_TTL_SEC = 300


def _pkg_version(name: str) -> str | None:
    try:
        mod = importlib.import_module(name)
        return getattr(mod, "__version__", None)
    except Exception:
        return None


def _git(cmd: list[str], cwd: Path) -> str | None:
    try:
        r = subprocess.run(["git", *cmd], cwd=str(cwd), capture_output=True,
                           text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _kline_root() -> Path:
    from datafeed.loader import DATA_ROOT
    return DATA_ROOT


def _dataset_snapshot(root: Path) -> str | None:
    """Latest mtime across the kline parquet tree, ISO-formatted UTC."""
    if not root.exists():
        return None
    latest = 0.0
    try:
        for p in root.rglob("*.parquet"):
            mt = p.stat().st_mtime
            if mt > latest:
                latest = mt
    except Exception:
        return None
    if latest == 0.0:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()


def capture(refresh: bool = False) -> dict:
    """Return a dict with reproducibility metadata. Cached for ~5 minutes
    per process so calling it on every history append is cheap."""
    global _CACHE, _CACHE_BUILT_AT
    now = time.time()
    if not refresh and _CACHE is not None and (now - _CACHE_BUILT_AT) < _CACHE_TTL_SEC:
        return _CACHE

    root = Path(__file__).resolve().parents[1]
    sha = _git(["rev-parse", "HEAD"], root)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    dirty = _git(["status", "--porcelain"], root)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": f"{__import__('sys').version_info.major}."
                  f"{__import__('sys').version_info.minor}."
                  f"{__import__('sys').version_info.micro}",
        "packages": {
            pkg: _pkg_version(pkg)
            for pkg in ("pandas", "numpy", "vectorbt", "scipy", "ccxt", "pyarrow")
        },
        "git": {
            "sha": sha,
            "branch": branch,
            "dirty": bool(dirty.strip()) if dirty is not None else None,
        },
        "dataset_snapshot": _dataset_snapshot(_kline_root()),
    }
    _CACHE = out
    _CACHE_BUILT_AT = now
    return out
