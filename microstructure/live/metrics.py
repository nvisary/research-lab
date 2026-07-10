"""The metrics registry — the polygon's extension point.

Each metric is a tiny ``SymbolState -> formatted string`` function registered
with ``@metric``. The UI builds one table column per registered metric, in
registration order, so **adding a new live indicator is one function here** —
no UI changes needed.

Return a plain string (``"-"`` when data isn't ready yet). Keep formatting
compact; the table columns are narrow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from microstructure.live.state import SymbolState


@dataclass
class Metric:
    key: str
    label: str          # column header
    fn: Callable[[SymbolState], str]
    width: int = 10


REGISTRY: list[Metric] = []


def metric(key: str, label: str, width: int = 10):
    def deco(fn: Callable[[SymbolState], str]) -> Callable[[SymbolState], str]:
        REGISTRY.append(Metric(key, label, fn, width))
        return fn
    return deco


def _sign(x: float) -> str:
    return f"{x:+,.1f}"


# ---- price / book ----------------------------------------------------------
@metric("price", "price", 11)
def _price(s: SymbolState) -> str:
    v = s.price()
    return f"{v:,.2f}" if v is not None else "-"


@metric("spread", "spr bp", 8)
def _spread(s: SymbolState) -> str:
    v = s.spread_bps()
    return f"{v:.2f}" if v is not None else "-"


@metric("imb5", "imb5", 7)
def _imb(s: SymbolState) -> str:
    v = s.imbalance(5)
    return f"{v:+.2f}" if v is not None else "-"


# ---- order flow (trades) ---------------------------------------------------
@metric("cvd_win", "CVDw", 10)
def _cvd_w(s: SymbolState) -> str:
    return _sign(s.cvd_window())


@metric("cvd_sess", "CVDsess", 11)
def _cvd_s(s: SymbolState) -> str:
    return _sign(s.cvd_session())


@metric("buy_pct", "buy%", 6)
def _buy(s: SymbolState) -> str:
    v = s.buy_frac_window()
    return f"{100*v:.0f}" if v is not None else "-"


@metric("tps", "tr/s", 6)
def _tps(s: SymbolState) -> str:
    return f"{s.trades_per_s():.1f}"


# ---- open interest + regime ------------------------------------------------
@metric("oi", "OI", 11)
def _oi(s: SymbolState) -> str:
    v = s.oi()
    return f"{v:,.0f}" if v is not None else "-"


@metric("doi_win", "dOIw", 10)
def _doi(s: SymbolState) -> str:
    v = s.doi_window()
    return _sign(v) if v is not None else "-"


@metric("regime", "regime", 12)
def _regime(s: SymbolState) -> str:
    return s.regime()


# ---- funding / premium -----------------------------------------------------
@metric("funding", "fund bp", 8)
def _funding(s: SymbolState) -> str:
    v = s.funding_rate()
    return f"{1e4*v:+.2f}" if v is not None else "-"


@metric("premium", "prem bp", 8)
def _premium(s: SymbolState) -> str:
    v = s.premium_bps()
    return f"{v:+.2f}" if v is not None else "-"


@metric("liq_win", "liq", 5)
def _liq(s: SymbolState) -> str:
    n = s.liq_count_window()
    return str(n) if n else "-"
