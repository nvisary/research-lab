"""Fixed backtest harness. Do not let the LLM agent edit this file.

The harness:
  1. Loads OHLCV for the given symbols/period.
  2. Calls strategy.generate_signals(data, params) to get target positions.
  3. Pushes positions through vectorbt with realistic costs to get equity & trades.
  4. Computes the standard metric panel.

Usage:
    python -m harness.backtest strategies/ema_pilot --period 2025
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

    prices = pd.concat({s: df["close"] for s, df in data.items()}, axis=1).ffill()
    prices = prices.dropna(how="all")
    symbols_present = list(prices.columns)

    signals = strategy_mod.generate_signals(data, params)
    target = _positions_to_wide(signals, symbols_present, prices.index)

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

    out = {}
    for label, lo, hi in [("train", split.train_start, split.train_end),
                           ("oos", split.oos_start, split.oos_end)]:
        mask = (adj_equity_full.index >= lo) & (adj_equity_full.index < hi)
        equity = adj_equity_full[mask]
        rets = adj_returns_full[mask]
        positions = target[mask]
        n_trades = int(((entry_times >= lo) & (entry_times < hi)).sum()) if len(entry_times) else 0
        out[label] = M.summary(equity, rets, positions, n_trades=n_trades)

    if return_curves:
        bench = (prices / prices.iloc[0]).mean(axis=1) * float(adj_equity_full.iloc[0])
        out["equity"] = adj_equity_full
        out["raw_equity"] = raw_equity_full
        out["funding_cashflow"] = fcf
        out["benchmark"] = bench
        out["split_cutoff"] = split.train_end
    return out


def run(strategy_dir: str | Path, period_start: str, period_end: str,
        symbols: list[str] | None = None, tf: str = "1h",
        params: dict | None = None, walk_windows: int = 0,
        return_curves: bool = False) -> dict:
    """Top-level: train/OOS split (and optionally walk-forward), return aggregated metrics."""
    strategy_dir = Path(strategy_dir)
    mod = load_strategy(strategy_dir)
    p = dict(mod.DEFAULT_PARAMS)
    if params:
        p.update(params)
    if symbols is None:
        symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT"])

    main_split = train_oos(period_start, period_end)
    main = run_split(mod, p, symbols, main_split, tf=tf, return_curves=return_curves)

    curves = None
    if return_curves and "equity" in main:
        curves = {
            "equity": main.pop("equity"),
            "benchmark": main.pop("benchmark"),
            "split_cutoff": main.pop("split_cutoff"),
            "raw_equity": main.pop("raw_equity", None),
            "funding_cashflow": main.pop("funding_cashflow", None),
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
        wf = []
        for sp in walk_forward(period_start, period_end, n_windows=walk_windows):
            wf.append(run_split(mod, p, symbols, sp, tf=tf))
        oos_sharpes = [w["oos"].get("sharpe", 0.0) for w in wf]
        result["walk_forward"] = {
            "windows": wf,
            "median_oos_sharpe": float(np.median(oos_sharpes)) if oos_sharpes else 0.0,
        }
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir", help="Path to strategies/<name>/")
    ap.add_argument("--period", default="2025", help="Year (e.g. 2025) or YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--walk", type=int, default=0)
    args = ap.parse_args()

    if ":" in args.period:
        ps, pe = args.period.split(":")
    else:
        y = int(args.period)
        ps, pe = f"{y}-01-01", f"{y + 1}-01-01"

    res = run(args.strategy_dir, ps, pe, symbols=args.symbols, tf=args.tf,
              walk_windows=args.walk)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
