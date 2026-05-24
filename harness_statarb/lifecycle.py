"""Basket lifecycle management.

A `BasketRegistry` is the in-memory state of a stat-arb backtest:
which baskets are currently being traded, when each was opened, when
it was retired (and why). Drives:

- The discovery step ("don't open a new basket too correlated to a
  live one")
- The retire step ("close baskets whose spread has stopped reverting")
- Diagnostics ("what fraction of baskets survived ≥ 50% of their planned
  OOS window before being retired?")

The registry does NOT compute spreads itself — the caller passes a
spread time series alongside each basket when proposing it (typically
the residual returned by `engle_granger` / `johansen` / `pca_decompose`).
This keeps the registry agnostic to how baskets are constructed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

import numpy as np
import pandas as pd

from harness_statarb.structures import Basket


@dataclass
class LifecycleEvent:
    """One open/close transition for a basket."""

    basket_id: str
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp | None = None        # None = still active
    close_reason: str | None = None              # "broken_stationarity", "retired_for_refit", "end_of_period", ...
    planned_lifespan_bars: int = 0                # how long we *intended* to hold (refit cadence)
    fit_stats: dict = field(default_factory=dict)


class BasketRegistry:
    """In-memory store of currently active baskets + lifecycle events.

    Invariants:
      - `active` keys are unique basket ids.
      - Every retire() call appends a LifecycleEvent with a non-None close_at.
      - Correlation filter rejects baskets whose fit-time spread has
        |corr| > corr_threshold with any already-active basket's spread.
    """

    def __init__(self, corr_threshold: float = 0.8):
        self.corr_threshold = float(corr_threshold)
        self._active: dict[str, Basket] = {}
        self._spreads: dict[str, pd.Series] = {}      # fit-time spread, indexed by time
        self._opened_at: dict[str, pd.Timestamp] = {}
        self._events: list[LifecycleEvent] = []

    # ----- query -----
    def __contains__(self, basket_id: str) -> bool:
        return basket_id in self._active

    def __iter__(self) -> Iterator[Basket]:
        return iter(self._active.values())

    def __len__(self) -> int:
        return len(self._active)

    def active(self) -> list[Basket]:
        return list(self._active.values())

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    def get(self, basket_id: str) -> Basket | None:
        return self._active.get(basket_id)

    def events(self) -> list[LifecycleEvent]:
        return list(self._events)

    # ----- mutate -----
    def propose(
        self,
        basket: Basket,
        spread_at_fit: pd.Series,
        opened_at: pd.Timestamp,
        planned_lifespan_bars: int,
    ) -> tuple[bool, str]:
        """Try to add `basket` to the active set.

        Returns (accepted, reason). Reasons for rejection:
          "duplicate_id"            — id already active
          "too_correlated:<other>"  — |corr| > threshold vs already-live basket
          "empty_spread"            — spread has no usable values
        """
        if basket.id in self._active:
            return False, "duplicate_id"
        s = pd.Series(spread_at_fit).dropna()
        if len(s) < 10:
            return False, "empty_spread"
        for other_id, other_spread in self._spreads.items():
            aligned = pd.concat(
                [s.rename("new"), other_spread.rename("other")], axis=1
            ).dropna()
            if len(aligned) < 10:
                continue
            corr = float(aligned["new"].corr(aligned["other"]))
            if not np.isnan(corr) and abs(corr) > self.corr_threshold:
                return False, f"too_correlated:{other_id}"
        self._active[basket.id] = basket
        self._spreads[basket.id] = s
        self._opened_at[basket.id] = opened_at
        self._events.append(LifecycleEvent(
            basket_id=basket.id,
            opened_at=opened_at,
            planned_lifespan_bars=int(planned_lifespan_bars),
            fit_stats=dict(basket.fit_stats),
        ))
        return True, "accepted"

    def retire(
        self,
        basket_id: str,
        closed_at: pd.Timestamp,
        reason: str,
    ) -> bool:
        """Remove a basket from the active set, record the closing event.

        Returns True if the basket was active, False if not found.
        """
        if basket_id not in self._active:
            return False
        del self._active[basket_id]
        self._spreads.pop(basket_id, None)
        self._opened_at.pop(basket_id, None)
        # Find the most recent OPEN event for this id (no closed_at yet) and close it.
        for ev in reversed(self._events):
            if ev.basket_id == basket_id and ev.closed_at is None:
                ev.closed_at = closed_at
                ev.close_reason = reason
                break
        return True

    def retire_all(self, closed_at: pd.Timestamp, reason: str = "end_of_period") -> None:
        for bid in list(self._active.keys()):
            self.retire(bid, closed_at, reason)


def events_to_dataframe(
    events: Iterable[LifecycleEvent],
    legs_by_id: dict[str, dict[str, float]],
    bars_per_unit: int,
) -> pd.DataFrame:
    """Flatten lifecycle events into a queryable DataFrame.

    Columns:
      basket_id, opened_at, closed_at, close_reason,
      planned_lifespan_bars, realized_lifespan_bars,
      target_symbol, adf_pvalue, half_life, beta, n_legs,
      legs_json   (JSON-encoded {symbol: weight} so parquet is happy)

    `legs_by_id` maps basket_id → legs dict; the registry doesn't store
    legs on the event (only on the live Basket), so the caller passes
    the mapping built up over the run.
    """
    import json as _json
    rows = []
    for ev in events:
        legs = legs_by_id.get(ev.basket_id, {})
        fs = ev.fit_stats or {}
        opened = ev.opened_at
        closed = ev.closed_at
        if closed is not None:
            realized_minutes = (closed - opened).total_seconds() / 60.0
            realized_bars = realized_minutes / max(bars_per_unit, 1)
        else:
            realized_bars = None
        rows.append({
            "basket_id": ev.basket_id,
            "opened_at": opened,
            "closed_at": closed,
            "close_reason": ev.close_reason,
            "planned_lifespan_bars": int(ev.planned_lifespan_bars),
            "realized_lifespan_bars": (float(realized_bars)
                                        if realized_bars is not None else None),
            "target_symbol": fs.get("target_symbol"),
            "adf_pvalue": (float(fs["adf_pvalue"]) if "adf_pvalue" in fs else None),
            "half_life": (float(fs["half_life"])
                          if "half_life" in fs and fs["half_life"] is not None
                          and np.isfinite(fs["half_life"]) else None),
            "beta": (float(fs["beta"]) if "beta" in fs else None),
            "n_legs": len(legs),
            "legs_json": _json.dumps(
                {k: round(float(v), 8) for k, v in legs.items()}
            ),
        })
    return pd.DataFrame(rows)


def survival_rate(
    events: Iterable[LifecycleEvent],
    bars_per_unit: int,
    survival_threshold: float = 0.5,
) -> float:
    """Fraction of baskets that survived ≥ `survival_threshold` of their planned lifespan.

    `bars_per_unit` is the number of bars between `opened_at` and `closed_at`
    timestamps that maps to one unit of planned_lifespan_bars. The caller
    typically passes the bar duration in minutes (so a 1h TF basket with
    refit every 7 days has planned_lifespan_bars = 7*24, and bars_per_unit
    = 60 (= 1h in minutes)).

    A basket retired because the period ended counts as "survived" iff its
    realized lifespan exceeded the threshold.
    """
    events = list(events)
    if not events:
        return float("nan")
    survived = 0
    counted = 0
    for ev in events:
        if ev.closed_at is None:
            continue           # still active — skip
        if ev.planned_lifespan_bars <= 0:
            continue
        realized_minutes = (ev.closed_at - ev.opened_at).total_seconds() / 60.0
        realized_bars = realized_minutes / max(bars_per_unit, 1)
        ratio = realized_bars / float(ev.planned_lifespan_bars)
        if ratio >= survival_threshold:
            survived += 1
        counted += 1
    if counted == 0:
        return float("nan")
    return survived / counted
