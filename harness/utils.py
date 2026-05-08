"""Strategy-side utilities.

Currently exports:
  resample_higher — multi-timeframe helper that automatically applies the
  one-bar shift required to keep higher-TF signals lookahead-safe.

These helpers are imported BY strategies. They are part of the public
contract: edits to them can break user code, so treat them as semver.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd


def resample_higher(
    df: pd.DataFrame,
    freq: str,
    agg: Mapping[str, str],
    *,
    target_index: pd.Index | None = None,
    shift_bars: int = 1,
) -> pd.DataFrame:
    """Resample ``df`` to a coarser ``freq`` (e.g. '30min', '4h') in a way
    that's safe to consume at a finer decision frequency.

    The naive call ``df.resample('30min').agg({'close': 'last'})`` is unsafe
    because pandas labels a bar by its left edge with default options:
    the bar at '09:30' contains 5m candles in [09:30, 10:00), and its 'close'
    column equals the 09:55 5m close. Using ``df30.close.loc['09:30']`` to
    decide the position at the 5m bar 09:30 is a 25-minute lookahead.

    This helper:
      1. resamples with the same default labelling,
      2. applies ``.shift(shift_bars)`` to advance the higher-TF series so each
         bar's "current value" comes from the previous COMPLETED higher-TF bar,
      3. optionally reindexes back to the finer ``target_index`` with
         forward-fill so consumers can multiply / mask / compare directly
         against the original df.

    Parameters
    ----------
    df :
        Source frame at the strategy's decision frequency (the harness TF).
        Must have a tz-aware DatetimeIndex.
    freq :
        Target coarser pandas offset alias, e.g. '15min', '30min', '4h', '1d'.
    agg :
        Mapping of column name -> aggregation method ('first', 'last', 'max',
        'min', 'sum', 'mean', etc.). Same dict you'd pass to ``DataFrameGroupBy.agg``.
    target_index :
        If provided, return a frame reindexed to this index with ffill. Pass
        ``df.index`` to align the higher-TF signal back onto the decision grid.
    shift_bars :
        Number of higher-TF bars to advance. Default 1 — the minimum needed
        to avoid using the bar that contains "now". Set to 0 only if you know
        what you are doing (e.g. you're computing on already-shifted values).

    Returns
    -------
    DataFrame with columns matching ``agg.keys()``. Indexed by either the
    higher-TF labels (if ``target_index`` is None) or by ``target_index``.

    Examples
    --------
    >>> # 5m -> 30m trend gate, safe for use at the 5m decision bar
    >>> df30 = resample_higher(df, "30min", {"close": "last"}, target_index=df.index)
    >>> trend_up = df30["close"] > df30["close"].rolling(20).mean()
    """
    higher = df.resample(freq).agg(dict(agg))
    if shift_bars > 0:
        higher = higher.shift(shift_bars)
    if target_index is not None:
        higher = higher.reindex(target_index, method="ffill")
    return higher
