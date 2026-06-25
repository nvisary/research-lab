"""``TrainData`` — the train-only data handle every research tool receives.

This is the safety property of the whole research layer. A research tool gets
a ``TrainData`` and nothing else; ``TrainData`` only ever loads bars inside the
**train slice** ``[period_start, train_cutoff)`` — the exact same boundary
``runner.optimize`` uses — and is hard-capped before the holdout (2026). A tool
therefore *cannot* peek at the OOS tail that ``runner.iterate`` judges with, nor
at the holdout, even by accident: the data it can reach simply ends at the cutoff.

This mirrors METHODS.md §6.2 ("measure on train, let OOS judge blind"): EDA
informs the *hypothesis*; OOS stays a blind referee. If EDA could see OOS, you'd
be overfitting the choice of what to test — invisible to the lookahead audit.
"""
from __future__ import annotations

import functools

import pandas as pd

from datafeed.loader import load_funding, load_with_open_interest
from harness.splits import train_oos

# Same defaults as runner.iterate / runner.optimize.
DEFAULT_PERIOD_START = "2024-01-01"
DEFAULT_PERIOD_END = "2026-01-01"
# Holdout begins here; the train cutoff is hard-capped before it so no
# misconfiguration can drag EDA into the final exam.
HOLDOUT_START = "2026-01-01"


def train_window(period_start: str = DEFAULT_PERIOD_START,
                 period_end: str = DEFAULT_PERIOD_END,
                 oos_fraction: float = 0.25) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return ``(start, cutoff)`` — the train-only window EDA is allowed to see.

    ``cutoff`` is the single-split train/OOS boundary (default: first 75% of the
    iter period), clamped to never cross into the holdout.
    """
    split = train_oos(period_start, period_end, oos_fraction=oos_fraction)
    cutoff = split.train_end
    holdout = pd.Timestamp(HOLDOUT_START, tz="UTC")
    if cutoff > holdout:
        cutoff = holdout
    return pd.Timestamp(period_start, tz="UTC"), cutoff


class TrainData:
    """Lazy, cached, train-only accessor for OHLCV / funding for a strategy.

    All loads are clipped to ``[start, cutoff)``. Accessors default to the first
    symbol; pass ``symbol=`` to target another. Loaded frames are cached so a
    tool can call ``ohlcv()`` repeatedly for free.
    """

    def __init__(self, symbols: list[str], tf: str,
                 start: pd.Timestamp, cutoff: pd.Timestamp):
        if not symbols:
            raise ValueError("TrainData needs at least one symbol")
        self.symbols = list(symbols)
        self.tf = tf
        self.start = start
        self.cutoff = cutoff

    # -- introspection ----------------------------------------------------- #
    @property
    def window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return (self.start, self.cutoff)

    def _resolve(self, symbol: str | None) -> str:
        if symbol is None:
            return self.symbols[0]
        if symbol not in self.symbols:
            # Allow ad-hoc symbols too, but the common case is one of `symbols`.
            return symbol
        return symbol

    # -- data accessors (cached, train-clipped) ---------------------------- #
    @functools.lru_cache(maxsize=None)
    def ohlcv(self, symbol: str | None = None) -> pd.DataFrame:
        """OHLCV(+open_interest when present) for ``symbol`` over the train window."""
        sym = self._resolve(symbol)
        return load_with_open_interest(sym, self.start, self.cutoff, tf=self.tf)

    @functools.lru_cache(maxsize=None)
    def funding(self, symbol: str | None = None) -> pd.DataFrame:
        """Funding-rate history (column ``rate``) for ``symbol`` over the train window."""
        sym = self._resolve(symbol)
        return load_funding(sym, self.start, self.cutoff)

    def close(self, symbol: str | None = None) -> pd.Series:
        return self.ohlcv(symbol)["close"]

    def returns(self, symbol: str | None = None, log: bool = False) -> pd.Series:
        """Simple (default) or log per-bar close-to-close returns, NaNs dropped."""
        c = self.close(symbol)
        import numpy as np
        r = np.log(c).diff() if log else c.pct_change()
        return r.dropna()


def load_train_data(strategy_dir=None, *, symbols: list[str] | None = None,
                    tf: str | None = None,
                    period_start: str = DEFAULT_PERIOD_START,
                    period_end: str = DEFAULT_PERIOD_END,
                    oos_fraction: float = 0.25) -> TrainData:
    """Build a ``TrainData`` for a strategy (reads its DEFAULT_SYMBOLS/DEFAULT_TF)
    or from explicit ``symbols``/``tf``.
    """
    if strategy_dir is not None:
        from harness.backtest import load_strategy
        mod = load_strategy(strategy_dir)
        symbols = symbols or list(getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"]))
        tf = tf or getattr(mod, "DEFAULT_TF", "1h")
    symbols = symbols or ["BTCUSDT"]
    tf = tf or "1h"
    start, cutoff = train_window(period_start, period_end, oos_fraction)
    return TrainData(symbols, tf, start, cutoff)
