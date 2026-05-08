"""Time-series splits: train/OOS + walk-forward windows."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Split:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


def train_oos(period_start: str, period_end: str, oos_fraction: float = 0.25) -> Split:
    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    cutoff = s + (e - s) * (1 - oos_fraction)
    return Split(s, cutoff, cutoff, e)


def walk_forward(period_start: str, period_end: str, n_windows: int = 4,
                 oos_fraction: float = 0.25) -> list[Split]:
    """Sliding windows that together tile [start, end). Each window has its own train/OOS split."""
    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    total = e - s
    win = total / n_windows
    out = []
    for i in range(n_windows):
        ws = s + win * i
        we = s + win * (i + 1)
        cutoff = ws + (we - ws) * (1 - oos_fraction)
        out.append(Split(ws, cutoff, cutoff, we))
    return out
