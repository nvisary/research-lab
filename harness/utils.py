"""Strategy-side utilities.

Exports:
  resample_higher — multi-timeframe helper that automatically applies the
  one-bar shift required to keep higher-TF signals lookahead-safe.
  bars_per_day — convert a TF string ("1h", "4h", "5min") into the number
  of bars per day, useful for vol-target sizing.

These helpers are imported BY strategies. They are part of the public
contract: edits to them can break user code, so treat them as semver.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd


_BARS_PER_DAY_TABLE: dict[str, float] = {
    "1min":  1440.0,
    "5min":  288.0,
    "15min": 96.0,
    "30min": 48.0,
    "1h":    24.0,
    "2h":    12.0,
    "4h":    6.0,
    "6h":    4.0,
    "8h":    3.0,
    "12h":   2.0,
    "1d":    1.0,
}


def bars_per_day(tf: str) -> float:
    """Number of bars in 24h for a given timeframe alias.

    Useful for vol-target sizing (``realized_daily_vol = ret.rolling(N).std() *
    sqrt(bars_per_day(DEFAULT_TF))``) so a strategy that switches TF doesn't
    silently misuse the annualization factor.

    Falls back to ``pd.Timedelta`` parsing for non-canonical aliases.
    Returns 24.0 if unparseable, on the assumption that 1h is the most
    common decision TF.
    """
    if tf in _BARS_PER_DAY_TABLE:
        return _BARS_PER_DAY_TABLE[tf]
    try:
        secs = pd.Timedelta(tf).total_seconds()
        if secs > 0:
            return 86400.0 / secs
    except Exception:
        pass
    return 24.0


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
