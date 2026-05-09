"""Fixed backtest harness. Do not let the LLM agent edit this file.

The harness:
  1. Loads OHLCV for the given symbols/period.
  2. Calls strategy.generate_signals(data, params) to get target positions.
  3. Pushes positions through vectorbt with realistic costs to get equity & trades.
  4. Computes the standard metric panel.

Usage:
    python -m harness.backtest strategies/ema_pilot --start 2024-01-01 --end 2025-10-01
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from datafeed.loader import load_many
from harness import metrics as M
from harness.costs import DEFAULT as DEFAULT_COSTS
from harness.funding import adjust_equity, funding_cashflows
from harness.splits import Split, train_oos, walk_forward


# Approx 24h of bars per TF. Beyond this we treat the gap as data death
# (delisting, exchange outage, our download failed) and force position to 0.
STALE_BARS_BY_TF: dict[str, int] = {
    "1min": 1440, "5min": 288, "15min": 96, "30min": 48,
    "1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2, "1d": 1,
}


# --------------------------------------------------------------------------- #
# Strategy loading
# --------------------------------------------------------------------------- #
def load_strategy(strategy_dir: Path):
    """Import strategies/<name>/strategy.py as a module."""
    strategy_dir = Path(strategy_dir).resolve()
    file = strategy_dir / "strategy.py"
    if not file.exists():
        raise FileNotFoundError(f"No strategy.py in {strategy_dir}")
    spec = importlib.util.spec_from_file_location(f"strategy_{strategy_dir.name}", file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    for attr in ("generate_signals", "DEFAULT_PARAMS"):
        if not hasattr(mod, attr):
            raise AttributeError(f"strategy.py missing required attribute: {attr}")
    return mod


# --------------------------------------------------------------------------- #
# Signal → portfolio
# --------------------------------------------------------------------------- #
def _positions_to_wide(signals: pd.DataFrame, symbols: list[str],
                       index: pd.DatetimeIndex) -> pd.DataFrame:
    """Convert long-format [timestamp, symbol, position] into wide DataFrame
    (index=time, columns=symbols, values=target position in [-1,1])."""
    if signals.empty:
        return pd.DataFrame(0.0, index=index, columns=symbols)

    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    wide = s.pivot_table(index="timestamp", columns="symbol", values="position",
                         aggfunc="last")
    wide = wide.reindex(index=index, columns=symbols)
    wide = wide.ffill().fillna(0.0).clip(-1.0, 1.0)
    return wide


def _run_vectorbt(prices: pd.DataFrame, target_pos: pd.DataFrame,
                  costs=DEFAULT_COSTS, init_cash: float = 10_000.0):
    """Run a vectorbt portfolio from target position weights.

    `target_pos` columns must match `prices` columns. Equal-weight sizing across
    symbols (each column gets `init_cash / n_symbols` notional × position).
    """
    n = prices.shape[1]
    size = target_pos / n  # share of total equity per symbol
    pf = vbt.Portfolio.from_orders(
        close=prices,
        size=size,
        size_type="targetpercent",
        fees=costs.taker_fee,
        slippage=costs.slippage_bps * 1e-4,
        init_cash=init_cash,
        cash_sharing=True,
        group_by=True,
        freq=pd.infer_freq(prices.index) or "1min",
        call_seq="auto",
    )
    return pf


# --------------------------------------------------------------------------- #
# Main entry points
# --------------------------------------------------------------------------- #
def run_split(strategy_mod, params: dict, symbols: list[str], split: Split,
              tf: str = "1h", costs=DEFAULT_COSTS, return_curves: bool = False) -> dict:
    """Backtest a single train/OOS split. Returns {'train': metrics, 'oos': metrics, ...}.

    If `return_curves=True`, also returns 'equity' and 'benchmark' Series spanning
    the full train+OOS window (benchmark = equal-weight buy-and-hold).
    """
    data = load_many(symbols, split.train_start, split.oos_end, tf=tf)
    data = {s: df for s, df in data.items() if not df.empty}
    if not data:
        return {"train": {}, "oos": {}, "error": "no data"}

    raw_prices = pd.concat({s: df["close"] for s, df in data.items()}, axis=1)
    raw_prices = raw_prices.dropna(how="all")
    symbols_present = list(raw_prices.columns)

    # Bounded forward-fill: tolerate gaps up to ~24h, but treat anything
    # longer as a delisting / data outage. Past that horizon we force the
    # target position to 0 (clean exit at last known price). Without this
    # cap, an unbounded ffill keeps the position open at a stale price
    # forever, hiding the realistic force-close loss of a real delisting.
    stale_limit = STALE_BARS_BY_TF.get(tf, 24)
    bounded = raw_prices.ffill(limit=stale_limit)
    stale_mask = bounded.isna()                     # True after gap exceeds limit
    prices = raw_prices.ffill()                     # for vbt bookkeeping
    n_stale = int(stale_mask.sum().sum())

    signals = strategy_mod.generate_signals(data, params)
    target = _positions_to_wide(signals, symbols_present, prices.index)
    # Force flat on stale bars: closes any open position at the last known
    # price and prevents re-entry while data is still missing.
    target = target.where(~stale_mask.reindex_like(target).fillna(False), 0.0)

    pf = _run_vectorbt(prices, target, costs=costs)

    try:
        trade_records = pf.trades.records_readable
        entry_times = pd.to_datetime(trade_records["Entry Timestamp"], utc=True)
    except Exception:
        entry_times = pd.Series(dtype="datetime64[ns, UTC]")

    # Funding adjustment: subtract cumulative funding cashflows from equity.
    # The harness uses adjusted-equity returns for ALL metrics; raw equity is
    # kept around only for diagnostics in return_curves.
    raw_equity_full = pf.value()
    if costs.apply_funding:
        try:
            asset_value = pf.asset_value(group_by=False)
        except Exception:
            asset_value = prices * 0.0  # vectorbt API drift fallback: no adjustment
        fcf = funding_cashflows(asset_value, split.train_start, split.oos_end)
        adj_equity_full = adjust_equity(raw_equity_full, fcf)
    else:
        fcf = pd.Series(0.0, index=raw_equity_full.index)
        adj_equity_full = raw_equity_full

    adj_returns_full = adj_equity_full.pct_change().fillna(0.0)

    # Equal-weight buy-and-hold benchmark on the same portfolio bars. Computed
    # unconditionally (cheap) so per-slice summary() can return Sharpe-vs-bench
    # alpha. Single-symbol strategies see this as that symbol's b&h Sharpe;
    # multi-symbol strategies see the equal-weighted basket's b&h Sharpe.
    bench = (prices / prices.iloc[0]).mean(axis=1) * float(adj_equity_full.iloc[0])

    out = {}
    for label, lo, hi in [("train", split.train_start, split.train_end),
                           ("oos", split.oos_start, split.oos_end)]:
        mask = (adj_equity_full.index >= lo) & (adj_equity_full.index < hi)
        equity = adj_equity_full[mask]
        rets = adj_returns_full[mask]
        positions = target[mask]
        n_trades = int(((entry_times >= lo) & (entry_times < hi)).sum()) if len(entry_times) else 0
        out[label] = M.summary(equity, rets, positions, n_trades=n_trades, tf=tf,
                                benchmark=bench[mask])

    if return_curves:
        out["equity"] = adj_equity_full
        out["raw_equity"] = raw_equity_full
        out["funding_cashflow"] = fcf
        out["benchmark"] = bench
        out["split_cutoff"] = split.train_end

        # OOS returns slice — used by iterate.py to compute DSR/PSR/CI on the
        # same series as composite, post-funding-adjustment.
        oos_mask = (adj_equity_full.index >= split.oos_start) & \
                   (adj_equity_full.index < split.oos_end)
        out["oos_returns"] = adj_returns_full[oos_mask]

        # Standardized trade ledger for the dashboard / per-iter analysis.
        try:
            tr = pf.trades.records_readable.copy()
            if not tr.empty:
                rename = {
                    "Entry Timestamp": "entry_time",
                    "Exit Timestamp": "exit_time",
                    "Avg Entry Price": "entry_price",
                    "Avg Exit Price": "exit_price",
                    "Size": "size",
                    "Direction": "direction",
                    "PnL": "pnl_quote",
                    "Return": "return_pct",
                    "Column": "symbol",
                }
                tr = tr.rename(columns={k: v for k, v in rename.items() if k in tr.columns})
                tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
                tr["exit_time"] = pd.to_datetime(tr["exit_time"], utc=True)
                tr["duration_hours"] = (tr["exit_time"] - tr["entry_time"]).dt.total_seconds() / 3600.0
                if "symbol" in tr.columns:
                    tr["symbol"] = tr["symbol"].apply(
                        lambda c: c[-1] if isinstance(c, tuple) else c)
                # Tag the slice each trade belongs to for window-aware analysis.
                tr["slice"] = pd.Series(
                    np.where(tr["entry_time"] < split.train_end, "train", "oos"),
                    index=tr.index,
                )
                keep_cols = [c for c in [
                    "entry_time", "exit_time", "symbol", "direction",
                    "size", "entry_price", "exit_price",
                    "pnl_quote", "return_pct", "duration_hours", "slice",
                ] if c in tr.columns]
                out["trades"] = tr[keep_cols].reset_index(drop=True)
        except Exception:
            out["trades"] = pd.DataFrame()
    return out


def run(strategy_dir: str | Path, period_start: str, period_end: str,
        symbols: list[str] | None = None, tf: str = "1h",
        params: dict | None = None, walk_windows: int = 0,
        return_curves: bool = False,
        embargo: str | pd.Timedelta | None = None) -> dict:
    """Top-level: train/OOS split (and optionally walk-forward), return aggregated metrics.

    ``embargo`` injects a gap between train and OOS in every split (single
    and walk-forward). Accepts ``pd.Timedelta`` or any string parseable by
    it ("1d", "12h", "144min"). ``None`` / 0 = no embargo (legacy behavior).
    See ``harness/splits.py`` for rationale.
    """
    strategy_dir = Path(strategy_dir)
    mod = load_strategy(strategy_dir)
    p = dict(mod.DEFAULT_PARAMS)
    if params:
        p.update(params)
    if symbols is None:
        symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])

    main_split = train_oos(period_start, period_end, embargo=embargo)
    main = run_split(mod, p, symbols, main_split, tf=tf, return_curves=return_curves)

    curves = None
    if return_curves and "equity" in main:
        curves = {
            "equity": main.pop("equity"),
            "benchmark": main.pop("benchmark"),
            "split_cutoff": main.pop("split_cutoff"),
            "raw_equity": main.pop("raw_equity", None),
            "funding_cashflow": main.pop("funding_cashflow", None),
            "oos_returns": main.pop("oos_returns", None),
            "trades": main.pop("trades", None),
        }

    result = {
        "strategy": strategy_dir.name,
        "params": p,
        "symbols": symbols,
        "tf": tf,
        "period": [period_start, period_end],
        "main": main,
    }
    if curves is not None:
        result["curves"] = curves
    if walk_windows > 1:
        windows = []
        wf_curves: list[dict] = []
        wf_splits = walk_forward(period_start, period_end, n_windows=walk_windows,
                                 embargo=embargo)
        for i, sp in enumerate(wf_splits):
            print(f"[wf] window {i+1}/{len(wf_splits)} "
                  f"({sp.train_start.date()} -> {sp.oos_end.date()}) running...",
                  flush=True)
            w = run_split(mod, p, symbols, sp, tf=tf, return_curves=return_curves)
            oos_sh = (w.get("oos") or {}).get("sharpe", 0.0)
            print(f"[wf] window {i+1}/{len(wf_splits)} done -- OOS Sharpe {oos_sh:+.3f}",
                  flush=True)
            if return_curves and "equity" in w:
                wf_curves.append({
                    "equity": w.pop("equity"),
                    "benchmark": w.pop("benchmark"),
                    "split_cutoff": w.pop("split_cutoff"),
                    "raw_equity": w.pop("raw_equity", None),
                    "funding_cashflow": w.pop("funding_cashflow", None),
                    "oos_returns": w.pop("oos_returns", None),
                    "trades": w.pop("trades", None),
                })
            windows.append(w)
        result["walk_forward"] = {"windows": windows}
        if return_curves and wf_curves:
            result["walk_forward"]["curves"] = wf_curves
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir", help="Path to strategies/<name>/")
    ap.add_argument("--start", default="2024-01-01",
                    help="Period start (YYYY-MM-DD). Same default as runner.iterate.")
    ap.add_argument("--end", default="2025-10-01",
                    help="Period end (YYYY-MM-DD, exclusive).")
    ap.add_argument("--period", default=None,
                    help="DEPRECATED. Backwards-compat alias for --start:--end "
                         "(format YYYY-MM-DD:YYYY-MM-DD or just YYYY).")
    ap.add_argument("--tf", default=None,
                    help="If omitted, read strategy.py:DEFAULT_TF, fall back to '1h'.")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--walk", type=int, default=0)
    ap.add_argument("--embargo", default=None,
                    help="Gap between train and OOS in each split, parseable "
                         "as pd.Timedelta (e.g. '1D', '12h', '144min'). "
                         "Default: no embargo. See harness/splits.py.")
    args = ap.parse_args()

    if args.period:
        if ":" in args.period:
            ps, pe = args.period.split(":")
        else:
            y = int(args.period)
            ps, pe = f"{y}-01-01", f"{y + 1}-01-01"
    else:
        ps, pe = args.start, args.end

    tf = args.tf
    if tf is None:
        mod = load_strategy(Path(args.strategy_dir))
        tf = getattr(mod, "DEFAULT_TF", "1h")

    res = run(args.strategy_dir, ps, pe, symbols=args.symbols, tf=tf,
              walk_windows=args.walk, embargo=args.embargo)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
