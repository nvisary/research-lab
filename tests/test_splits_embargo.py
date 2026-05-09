"""Tests for embargo behavior in train_oos / walk_forward.

Embargo introduces a gap between train and OOS so that the first bars of
OOS — whose rolling indicators still contain train-era state — are
excluded from both slices' metrics. See harness/splits.py for rationale.
"""
from __future__ import annotations

import pandas as pd
import pytest

from harness.splits import Split, train_oos, walk_forward


PS = "2024-01-01"
PE = "2025-01-01"  # exactly 366 days (2024 is a leap year)


# --------------------------------------------------------------------------- #
# train_oos
# --------------------------------------------------------------------------- #
def test_train_oos_no_embargo_is_legacy_behavior():
    s = train_oos(PS, PE)
    assert s.train_end == s.oos_start, "without embargo, OOS starts at cutoff"
    assert s.train_start == pd.Timestamp(PS, tz="UTC")
    assert s.oos_end == pd.Timestamp(PE, tz="UTC")


def test_train_oos_zero_embargo_explicit_is_legacy_behavior():
    s = train_oos(PS, PE, embargo=pd.Timedelta(0))
    assert s.train_end == s.oos_start


def test_train_oos_with_embargo_creates_gap():
    s = train_oos(PS, PE, embargo="5D")
    assert s.oos_start == s.train_end + pd.Timedelta("5D")
    assert s.oos_end == pd.Timestamp(PE, tz="UTC")
    # Train slice itself is unchanged — embargo shrinks OOS, not train.
    expected_cutoff = (
        pd.Timestamp(PS, tz="UTC")
        + (pd.Timestamp(PE, tz="UTC") - pd.Timestamp(PS, tz="UTC")) * 0.75
    )
    assert s.train_end == expected_cutoff


def test_train_oos_embargo_accepts_string_and_timedelta():
    a = train_oos(PS, PE, embargo="1D")
    b = train_oos(PS, PE, embargo=pd.Timedelta(days=1))
    assert a == b


def test_train_oos_embargo_too_large_raises():
    # OOS slice = 25% of 366d ≈ 91.5d. Embargo of 100d eats it entirely.
    with pytest.raises(ValueError, match="embargo"):
        train_oos(PS, PE, embargo="100D")


# --------------------------------------------------------------------------- #
# walk_forward
# --------------------------------------------------------------------------- #
def test_walk_forward_no_embargo_is_legacy_behavior():
    splits = walk_forward(PS, PE, n_windows=4)
    for s in splits:
        assert s.train_end == s.oos_start


def test_walk_forward_with_embargo_each_window_has_gap():
    splits = walk_forward(PS, PE, n_windows=4, embargo="1D")
    assert len(splits) == 4
    for s in splits:
        assert s.oos_start == s.train_end + pd.Timedelta("1D")
        # Embargo zone is strictly inside the window.
        assert s.train_start < s.train_end < s.oos_start < s.oos_end


def test_walk_forward_windows_still_tile_the_period():
    splits = walk_forward(PS, PE, n_windows=4, embargo="1D")
    # train_starts must form a contiguous tiling regardless of embargo.
    assert splits[0].train_start == pd.Timestamp(PS, tz="UTC")
    assert splits[-1].oos_end == pd.Timestamp(PE, tz="UTC")
    for prev, nxt in zip(splits[:-1], splits[1:]):
        assert nxt.train_start == prev.oos_end


def test_walk_forward_embargo_too_large_for_window_raises():
    # Each window ≈ 91d, OOS slice ≈ 23d. 30d embargo blows out OOS.
    with pytest.raises(ValueError, match="embargo"):
        walk_forward(PS, PE, n_windows=4, embargo="30D")


# --------------------------------------------------------------------------- #
# Direct Split construction (used by the golden snapshot test) is unchanged.
# --------------------------------------------------------------------------- #
def test_split_dataclass_is_backward_compatible():
    """Code that builds Split directly with oos_start == train_end still works."""
    t = pd.Timestamp("2024-01-01", tz="UTC")
    s = Split(train_start=t, train_end=t + pd.Timedelta("90D"),
              oos_start=t + pd.Timedelta("90D"), oos_end=t + pd.Timedelta("120D"))
    assert s.train_end == s.oos_start
