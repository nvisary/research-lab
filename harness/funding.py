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

        # Assign each funding event to the latest bar at or before the event's
        # timestamp. merge_asof(direction='backward') matches each row in the
        # left frame to the largest right-frame key satisfying right_key
        # <= left_key. Compared to bar_index.searchsorted(side='left'), this
        # is unambiguous when the event falls between two bars or shares a
        # boundary with one (e.g. funding ts 08:00:00.500 with 5m bars at
        # 08:00 / 08:05 — the event happens during the 08:00 bar's window).
        events = pd.DataFrame({"funding_ts": f.index, "rate": f["rate"].values})
        bars = pd.DataFrame({
            "bar_ts": bar_index,
            "notional": asset_value[col].fillna(0.0).values,
        })
        joined = pd.merge_asof(
            events.sort_values("funding_ts"),
            bars.sort_values("bar_ts"),
            left_on="funding_ts",
            right_on="bar_ts",
            direction="backward",
        ).dropna(subset=["bar_ts"])
        if joined.empty:
            continue

        joined["cashflow"] = joined["notional"] * joined["rate"]
        per_bar = joined.groupby("bar_ts")["cashflow"].sum()
        out = out.add(per_bar, fill_value=0.0)

    return out.reindex(bar_index, fill_value=0.0)


def adjust_equity(raw_equity: pd.Series, cashflows: pd.Series) -> pd.Series:
    """raw_equity_t - cumsum(funding_cashflow_t)."""
    cf = cashflows.reindex(raw_equity.index, fill_value=0.0)
    return raw_equity - cf.cumsum()
