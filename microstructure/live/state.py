"""Per-symbol live state for the realtime polygon.

A ``SymbolState`` consumes the exact row dicts the collector's watch loops build
(``schemas.trade_row`` / ``orderbook_row`` / ``ticker_row`` / ``liquidation_row``)
and maintains rolling, incremental microstructure metrics over a sliding time
window. It is deliberately lock-free: everything runs in one asyncio loop.

The four ``on_<stream>`` methods match the collector's canonical stream names so
a single generic tap (see ``engine.Tap``) can route rows by ``getattr``.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from microstructure.collector.stats import now_ms


class SymbolState:
    def __init__(self, symbol: str, window_s: float = 60.0) -> None:
        self.symbol = symbol
        self.window_s = window_s
        self.window_ms = int(window_s * 1000)

        # trade tape within the window: (ts_ms, side, amount, price, signed)
        self.trades: deque = deque()
        self.tape: deque = deque(maxlen=200)      # (ts_ms, side, amount, price) for display
        self.cvd_session_val: float = 0.0
        self.n_trades_session: int = 0

        # price history within window (from trades): (ts_ms, price)
        self.px_hist: deque = deque()
        # open-interest history within window: (ts_ms, oi)
        self.oi_hist: deque = deque()
        # liquidations within window: (ts_ms, side, price)
        self.liqs: deque = deque()
        self.n_liq_session: int = 0

        self.last_book: dict | None = None
        self.last_ticker: dict | None = None
        self.last_trade_ms: int | None = None
        self.last_book_ms: int | None = None

    # ---- ingest (called by the tap) --------------------------------------
    def on_trades(self, row: dict) -> None:
        ts = row.get("ts_local") or now_ms()
        side = row.get("side")
        amt = float(row.get("amount") or 0.0)
        px = row.get("price")
        signed = amt if side == "buy" else -amt
        self.cvd_session_val += signed
        self.n_trades_session += 1
        self.last_trade_ms = ts
        self.trades.append((ts, side, amt, px, signed))
        self.tape.append((ts, side, amt, px))
        if px is not None:
            self.px_hist.append((ts, px))

    def on_orderbook(self, row: dict) -> None:
        self.last_book = row
        self.last_book_ms = row.get("ts_local") or now_ms()

    def on_ticker(self, row: dict) -> None:
        self.last_ticker = row
        oi = row.get("open_interest")
        if oi is not None:
            self.oi_hist.append((row.get("ts_local") or now_ms(), float(oi)))

    def on_liquidations(self, row: dict) -> None:
        self.n_liq_session += 1
        self.liqs.append((row.get("ts_local") or now_ms(), row.get("side"), row.get("price")))

    # ---- eviction --------------------------------------------------------
    def _evict(self, now: int | None = None) -> None:
        now = now or now_ms()
        cut = now - self.window_ms
        for dq in (self.trades, self.px_hist, self.oi_hist, self.liqs):
            while dq and dq[0][0] < cut:
                dq.popleft()

    # ---- derived metrics (numbers; formatting lives in metrics.py) -------
    def price(self) -> float | None:
        if self.last_book is not None:
            m = self.mid()
            if m is not None:
                return m
        return self.px_hist[-1][1] if self.px_hist else None

    def mid(self) -> float | None:
        b = self.last_book
        if not b:
            return None
        bid, ask = b.get("bid_px_0"), b.get("ask_px_0")
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def spread_bps(self) -> float | None:
        b = self.last_book
        if not b:
            return None
        bid, ask = b.get("bid_px_0"), b.get("ask_px_0")
        if not bid or not ask:
            return None
        return 1e4 * (ask - bid) / ((ask + bid) / 2.0)

    def imbalance(self, levels: int = 5) -> float | None:
        b = self.last_book
        if not b:
            return None
        bsum = sum(b.get(f"bid_sz_{i}") or 0.0 for i in range(levels))
        asum = sum(b.get(f"ask_sz_{i}") or 0.0 for i in range(levels))
        if bsum + asum == 0:
            return None
        return (bsum - asum) / (bsum + asum)

    def cvd_session(self) -> float:
        return self.cvd_session_val

    def cvd_window(self) -> float:
        self._evict()
        return sum(t[4] for t in self.trades)

    def buy_frac_window(self) -> float | None:
        self._evict()
        buy = sum(t[2] for t in self.trades if t[1] == "buy")
        sell = sum(t[2] for t in self.trades if t[1] == "sell")
        tot = buy + sell
        return buy / tot if tot > 0 else None

    def trades_per_s(self) -> float:
        self._evict()
        return len(self.trades) / self.window_s if self.window_s else 0.0

    def oi(self) -> float | None:
        return self.oi_hist[-1][1] if self.oi_hist else None

    def doi_window(self) -> float | None:
        self._evict()
        if len(self.oi_hist) < 2:
            return None
        return self.oi_hist[-1][1] - self.oi_hist[0][1]

    def dprice_window(self) -> float | None:
        self._evict()
        if len(self.px_hist) < 2:
            return None
        return self.px_hist[-1][1] - self.px_hist[0][1]

    def regime(self) -> str:
        """Order-flow regime from sign(Δprice) × sign(ΔOI) over the window."""
        dp, doi = self.dprice_window(), self.doi_window()
        if dp is None or doi is None:
            return "-"
        if dp > 0 and doi > 0:
            return "new longs"
        if dp > 0 and doi <= 0:
            return "short cover"
        if dp <= 0 and doi > 0:
            return "new shorts"
        return "long exit"

    def funding_rate(self) -> float | None:
        return self.last_ticker.get("funding_rate") if self.last_ticker else None

    def premium_bps(self) -> float | None:
        """(mark - index) / index in bps — the live premium funding tracks."""
        tk = self.last_ticker
        if not tk:
            return None
        mark, index = tk.get("mark"), tk.get("index")
        if not mark or not index:
            return None
        return 1e4 * (mark - index) / index

    def liq_count_window(self) -> int:
        self._evict()
        return len(self.liqs)

    @staticmethod
    def secs_to_funding() -> float:
        """Seconds to the next 8h UTC funding stamp (00/08/16). Assumes 8h
        interval — true for BTC/ETH/SOL majors; shorter-interval contracts differ."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        next_h = ((hour // 8) + 1) * 8
        secs = (next_h - hour) * 3600 - now.minute * 60 - now.second
        return float(secs)
