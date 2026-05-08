"""Funding-rate PnL adjustment for Bybit perp backtests.

vectorbt knows nothing about perp funding. We compute funding cash flows
post-hoc, given:
  - per-symbol asset value (notional × position direction) at each bar,
  - funding rates loaded from disk via datafeed.loader.load_funding.

Sign convention:
  funding_cashflow_t = +position_notional_t * funding_rate_t
  adjusted_equity_t  = raw_equity_t - cumsum(funding_cashflow_t)

i.e. a long with a positive funding rate pays out (equity drops);
a short with a positive funding rate gets paid (equity rises).
"""
from __future__ import annotations

import pandas as pd

from datafeed.loader import load_funding


def funding_cashflows(asset_value: pd.DataFrame, start, end) -> pd.Series:
    """Per-bar funding PnL drag, summed across all symbols.

    Parameters
    ----------
    asset_value : DataFrame
        Wide DataFrame, index = portfolio bar timestamps (tz-aware UTC),
        columns = symbol names, values = signed position notional (USDT).
        Long positions positive, shorts negative, zero when flat.
    start, end : timestamp-like
        Loader window. Funding events outside this range are ignored.

    Returns
    -------
    Series indexed identically to `asset_value`. Values are 0 except on
    bars that contain a funding event, where the value equals
    `Σ_symbols position_notional × funding_rate`. Adding this Series to
    a cumsum and subtracting from raw equity gives funding-adjusted equity.
    """
    out = pd.Series(0.0, index=asset_value.index, name="funding_cashflow")
    if asset_value.empty:
        return out

    bar_index = asset_value.index

    for col in asset_value.columns:
        # vectorbt cash_sharing+group_by yields MultiIndex columns (group, symbol).
        # Take the last level as the on-disk symbol name.
        sym = col[-1] if isinstance(col, tuple) else col
        f = load_funding(sym, start, end)
        if f.empty:
            continue

        # Restrict funding events to those that fall within the bar range.
        f = f[(f.index >= bar_index[0]) & (f.index <= bar_index[-1])]
        if f.empty:
            continue

        # Snap each funding event onto the next portfolio bar at-or-after the
        # event time. searchsorted with side='left' gives that index.
        positions = bar_index.searchsorted(f.index, side="left")
        valid = positions < len(bar_index)
        positions = positions[valid]
        rates = f["rate"].values[valid]
        bar_ts = bar_index[positions]

        # Notional held at the assigned bar; long positive, short negative.
        notionals = asset_value[col].reindex(bar_ts).fillna(0.0).values
        contrib = pd.Series(notionals * rates, index=bar_ts).groupby(level=0).sum()
        out = out.add(contrib, fill_value=0.0)

    return out.reindex(bar_index, fill_value=0.0)


def adjust_equity(raw_equity: pd.Series, cashflows: pd.Series) -> pd.Series:
    """raw_equity_t - cumsum(funding_cashflow_t)."""
    cf = cashflows.reindex(raw_equity.index, fill_value=0.0)
    return raw_equity - cf.cumsum()
