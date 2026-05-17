"""Time-series splits: train/OOS + walk-forward windows.

Embargo
-------
Both `train_oos` and `walk_forward` accept an optional `embargo` (a
``pd.Timedelta``) that introduces a gap between the train and OOS slices.
The first `embargo` worth of bars after `train_end` are dropped from BOTH
train and OOS metrics — they are neither used for fitting (logically) nor
counted toward the out-of-sample score.

Why: rolling indicators with lookback N "remember" their state across the
train/OOS boundary. The first N bars of OOS therefore mix train-era state
into their values, biasing OOS metrics toward train. Embargo > N
neutralizes that leak. López de Prado (AFML, ch. 7) uses this technique
under the name "purged + embargoed CV"; here we implement only the
embargo half because our setup has no overlapping labels (positions are
shifted by 1 bar, so there is no horizon to purge).

Sensible defaults: ~1% of the dataset length, or
2 × max_indicator_lookback if known. The default in this module is
``Timedelta(0)`` (off) so direct callers see no behavior change; CLI
entry points (``runner.iterate``) set their own non-zero default.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Split:
    """Train/OOS split with optional embargo gap.

    When ``oos_start > train_end``, bars in ``[train_end, oos_start)`` are
    the embargo zone — excluded from both train and OOS metrics by the
    backtest harness (which masks by ``< train_end`` for train and
    ``>= oos_start`` for OOS).
    """
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


def _as_timedelta(x) -> pd.Timedelta:
    if isinstance(x, pd.Timedelta):
        return x
    if x is None:
        return pd.Timedelta(0)
    return pd.Timedelta(x)


def train_oos(period_start: str, period_end: str, oos_fraction: float = 0.25,
              embargo: pd.Timedelta | str | None = None) -> Split:
    """Single train/OOS split with an optional embargo gap.

    The embargo zone shrinks OOS (not train): cutoff stays at the same
    fraction of the period, but ``oos_start`` is pushed forward. This
    keeps the train slice unambiguously "everything before the cutoff"
    and prevents an embargo from silently lengthening it.
    """
    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    cutoff = s + (e - s) * (1 - oos_fraction)
    emb = _as_timedelta(embargo)
    oos_start = cutoff + emb
    if oos_start >= e:
        raise ValueError(
            f"embargo {emb} consumes the entire OOS slice "
            f"({cutoff} → {e}); reduce embargo or oos_fraction"
        )
    return Split(s, cutoff, oos_start, e)


def walk_forward(period_start: str, period_end: str, n_windows: int = 4,
                 oos_fraction: float = 0.25,
                 embargo: pd.Timedelta | str | None = None,
                 expanding: bool = False) -> list[Split]:
    """Walk-forward windows over ``[period_start, period_end)``.

    Two modes — both produce ``n_windows`` Splits whose OOS slices tile
    the period; they differ in how train slices are sized:

      ``expanding=False`` (default, legacy behaviour): each window is a
        disjoint tile of length ``total / n_windows``. Window i's train
        slice is roughly ``(1 - oos_fraction) × total / n_windows`` long.
        Total period split into n equally-sized non-overlapping chunks.

      ``expanding=True``: each window's ``train_start = period_start``
        (no rolling-off), so train grows from window to window while
        OOS continues to tile. Window 0 looks identical between modes;
        window i in expanding mode has a train slice ~``(1 + i × (1 -
        oos_fraction)) × total / n_windows`` long. This matches the
        live operator workflow — when you ship, you have all of history,
        not just the last ``total / n`` of it.

    OOS slices and embargo behave identically in both modes.
    """
    s = pd.Timestamp(period_start, tz="UTC")
    e = pd.Timestamp(period_end, tz="UTC")
    total = e - s
    win = total / n_windows
    emb = _as_timedelta(embargo)
    out = []
    for i in range(n_windows):
        we = s + win * (i + 1)
        cutoff_local = s + win * i + (we - (s + win * i)) * (1 - oos_fraction)
        oos_start = cutoff_local + emb
        if oos_start >= we:
            raise ValueError(
                f"embargo {emb} consumes the entire OOS slice of window {i+1}/"
                f"{n_windows} (cutoff {cutoff_local} → {we}, window length {win}); "
                f"reduce embargo, n_windows, or oos_fraction"
            )
        train_start = s if expanding else (s + win * i)
        out.append(Split(train_start, cutoff_local, oos_start, we))
    return out
