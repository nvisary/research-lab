"""Two-level stat-arb backtest engine.

Architecture: rather than rewriting `harness.backtest.run_split` (which
already handles fees, slippage, funding, equity, metrics), we adapt the
stat-arb strategy contract (`find_structures` + `trade_basket`) into a
shim module that exposes the legacy `generate_signals(data, params)`
interface. The shim:

  1. Walks across the union of all data indices in rebalance steps of
     `refit_freq_bars`.
  2. At each rebalance date, calls `strategy.find_structures(train_data,
     params)` on the data prefix to discover candidate baskets.
  3. Manages a `BasketRegistry` — admits new baskets (with correlation
     filter), retires ones whose spread has broken stationarity.
  4. For each currently-active basket, calls `strategy.trade_basket`
     for the next rebalance window and decomposes the basket-level
     position into per-leg per-symbol positions via the basket's
     weights.
  5. Sums all baskets' contributions into a per-symbol position panel
     and returns it in long format.
  6. Sets `RAW_SIZING = True` so the harness interprets these as
     fractions of total equity. Capital is split by dividing each
     basket's allocation by `n_baskets_target` so that with a typical
     active population gross stays within ~100%.

The shim stashes the populated `BasketRegistry` on itself as
`_statarb_registry` so downstream diagnostics can read structure-level
survival statistics.

After the shim runs, we call `harness.backtest.run_split` on it — that
gives back the full equity / Sharpe / drawdown / trade ledger panel the
existing harness produces.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from datafeed.loader import load_many
from harness.backtest import run_split as base_run_split
from harness.costs import DEFAULT as DEFAULT_COSTS
from harness.splits import train_oos, walk_forward
from harness_statarb.diagnostics import build_flags
from harness_statarb.lifecycle import (
    BasketRegistry,
    events_to_dataframe,
    survival_rate as _survival_rate,
)
from harness_statarb.metrics import statarb_composite_score
from harness_statarb.structures import Basket, adf_pvalue


# --------------------------------------------------------------------------- #
# Strategy loading (stat-arb contract)
# --------------------------------------------------------------------------- #
def load_statarb_strategy(strategy_dir: Path):
    """Import strategies_statarb/<name>/strategy.py and validate the contract."""
    strategy_dir = Path(strategy_dir).resolve()
    file = strategy_dir / "strategy.py"
    if not file.exists():
        raise FileNotFoundError(f"No strategy.py in {strategy_dir}")
    spec = importlib.util.spec_from_file_location(
        f"statarb_strategy_{strategy_dir.name}", file
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    for attr in ("find_structures", "trade_basket", "DEFAULT_PARAMS"):
        if not hasattr(mod, attr):
            raise AttributeError(
                f"strategy.py missing required stat-arb attribute: {attr}"
            )
    return mod


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def basket_spread(
    basket: Basket,
    data: dict[str, pd.DataFrame],
    lo: pd.Timestamp | None = None,
    hi: pd.Timestamp | None = None,
    use_log: bool = True,
) -> pd.Series:
    """Compute the basket's spread (Σ_i w_i · price_i) over [lo, hi)."""
    parts = []
    for sym, w in basket.legs.items():
        if sym not in data:
            continue
        close = data[sym]["close"]
        if lo is not None:
            close = close[close.index >= lo]
        if hi is not None:
            close = close[close.index < hi]
        s = np.log(close) if use_log else close
        parts.append(w * s)
    if not parts:
        return pd.Series(dtype=float)
    df = pd.concat(parts, axis=1).dropna()
    return df.sum(axis=1)


# --------------------------------------------------------------------------- #
# Shim — adapts stat-arb strategy to the legacy generate_signals contract
# --------------------------------------------------------------------------- #
class _StatArbShim:
    """A duck-typed `strategy_mod` consumable by `harness.backtest.run_split`."""

    def __init__(self, strategy_mod, params: dict):
        self._mod = strategy_mod
        self._params = dict(params)
        # Legacy harness reads these via getattr().
        self.DEFAULT_PARAMS = dict(strategy_mod.DEFAULT_PARAMS)
        self.RAW_SIZING = True
        self.MAX_POSITION = float(getattr(strategy_mod, "MAX_POSITION", 2.0))
        # Side channel — written by generate_signals, read by run_statarb.
        self._registry: BasketRegistry | None = None
        self._discovery_log: list[dict] = []
        # legs snapshot per basket id (needed after retire, when the
        # registry no longer holds the Basket object).
        self._legs_by_id: dict[str, dict[str, float]] = {}

    def generate_signals(self, data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
        # `params` here is what harness.backtest passes (its merged dict);
        # use it, not self._params. Callers of run_statarb pre-merge anyway.
        p = dict(params)

        # Strategy-level config.
        refit_freq_bars = int(p.get("refit_freq_bars", 168))   # default 7d on 1h
        fit_window_bars = int(p.get("fit_window_bars", 2160))  # default 90d on 1h
        n_baskets_target = max(1, int(p.get("n_baskets_target", 5)))
        corr_threshold = float(p.get("corr_threshold", 0.8))
        retire_adf = float(p.get("retire_adf_pvalue", 0.10))

        symbols = list(data.keys())
        # Build a unified, sorted UTC index across all symbols.
        idx_union: pd.DatetimeIndex = pd.DatetimeIndex(
            sorted(set().union(*[df.index for df in data.values() if not df.empty]))
        )
        if len(idx_union) == 0:
            return pd.DataFrame(columns=["timestamp", "symbol", "position"])

        registry = BasketRegistry(corr_threshold=corr_threshold)
        position_panel = pd.DataFrame(0.0, index=idx_union, columns=symbols)
        opened_at_idx: dict[str, int] = {}        # bid → integer position in idx_union

        # Rebalance boundaries: indices in idx_union at which we run
        # discovery + retire. First rebalance is at fit_window_bars so
        # the very first discovery has a full window of training data.
        n = len(idx_union)
        if n <= fit_window_bars:
            # Not enough data even for a single fit.
            self._registry = registry
            return pd.DataFrame(columns=["timestamp", "symbol", "position"])

        bounds: list[int] = []
        i = fit_window_bars
        while i < n:
            bounds.append(i)
            i += refit_freq_bars
        bounds.append(n)  # closing sentinel

        per_basket_capital = 1.0 / n_baskets_target

        for k in range(len(bounds) - 1):
            i = bounds[k]
            i_next = bounds[k + 1]
            rebalance_ts = idx_union[i]
            window_end_ts = idx_union[i_next - 1] if i_next <= n else idx_union[-1]
            train_lo = i - fit_window_bars

            # --- 1. Retire active baskets that broke stationarity OR
            # reached their planned lifespan (= refit cadence). The
            # latter implements clean rotation: at every refit boundary,
            # baskets close so the strategy gets a fair shot at re-fitting
            # (under a new id) without being blocked by the
            # duplicate-id check. ---
            for bid in list(registry.active_ids()):
                b = registry.get(bid)
                if b is None:
                    continue
                bars_open = i - opened_at_idx.get(bid, i)
                if bars_open >= refit_freq_bars:
                    registry.retire(bid, rebalance_ts, "refit_cycle")
                    opened_at_idx.pop(bid, None)
                    continue
                # Use the last ~fit_window/2 bars of available data for the
                # health check (recent OOS behavior).
                recent_lo_idx = max(0, i - fit_window_bars // 2)
                recent_spread = basket_spread(
                    b, data, lo=idx_union[recent_lo_idx], hi=rebalance_ts,
                )
                if len(recent_spread) < 30:
                    continue
                p_val = adf_pvalue(recent_spread)
                if p_val > retire_adf:
                    registry.retire(bid, rebalance_ts, "broken_stationarity")
                    opened_at_idx.pop(bid, None)

            # --- 2. Discovery on train slice [train_lo, i). ---
            train_data: dict[str, pd.DataFrame] = {
                s: df.loc[(df.index >= idx_union[train_lo]) & (df.index < rebalance_ts)]
                for s, df in data.items()
            }
            try:
                new_baskets = self._mod.find_structures(train_data, p) or []
            except Exception as exc:
                self._discovery_log.append({
                    "rebalance_ts": str(rebalance_ts),
                    "error": f"{type(exc).__name__}: {exc}",
                    "n_proposed": 0,
                    "n_accepted": 0,
                })
                new_baskets = []

            n_accepted = 0
            for b in new_baskets:
                if not isinstance(b, Basket):
                    # Strategies may return dicts — coerce.
                    b = Basket.from_dict(b)
                # Normalize so |Σ|w_i|| == 1 (gross of one full unit per basket).
                b = b.normalize_to_gross(1.0)
                spread = basket_spread(b, data, lo=idx_union[train_lo], hi=rebalance_ts)
                ok, _why = registry.propose(
                    b, spread,
                    opened_at=rebalance_ts,
                    planned_lifespan_bars=refit_freq_bars,
                )
                if ok:
                    n_accepted += 1
                    opened_at_idx[b.id] = i
                    self._legs_by_id[b.id] = dict(b.legs)
            self._discovery_log.append({
                "rebalance_ts": str(rebalance_ts),
                "n_proposed": int(len(new_baskets)),
                "n_accepted": int(n_accepted),
                "n_active_after": int(len(registry)),
            })

            # --- 3. Trade each active basket over [i, i_next). ---
            window_slice = idx_union[i:i_next]
            for b in registry.active():
                # Pass full historical data for the basket's legs up to the
                # end of this trading window, so trade_basket can build
                # rolling z-scores against past data.
                leg_data = {
                    s: df.loc[df.index < (idx_union[i_next - 1] + pd.Timedelta(microseconds=1))]
                    for s, df in data.items() if s in b.legs
                }
                try:
                    basket_pos = self._mod.trade_basket(
                        b, leg_data, p,
                        active_window=(rebalance_ts, window_end_ts),
                    )
                except TypeError:
                    # Allow strategies whose trade_basket has only 3 args.
                    basket_pos = self._mod.trade_basket(b, leg_data, p)
                except Exception as exc:
                    self._discovery_log.append({
                        "rebalance_ts": str(rebalance_ts),
                        "basket_id": b.id,
                        "trade_error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                if not isinstance(basket_pos, pd.Series):
                    basket_pos = pd.Series(basket_pos)
                # Restrict to this rebalance window only.
                basket_pos = basket_pos.reindex(window_slice).fillna(0.0)
                # Clip to [-1, +1] — basket-level position is fraction of basket capital.
                basket_pos = basket_pos.clip(-1.0, 1.0)
                # Decompose into per-symbol contributions.
                for sym, w in b.legs.items():
                    if sym not in symbols:
                        continue
                    position_panel.loc[window_slice, sym] += (
                        basket_pos.values * w * per_basket_capital
                    )

        # Final close — gives every still-open basket a closed_at timestamp
        # for survival_rate accounting.
        registry.retire_all(idx_union[-1], reason="end_of_period")
        self._registry = registry

        # Convert wide panel → long format expected by run_split.
        long_rows = []
        for sym in symbols:
            ser = position_panel[sym]
            long_rows.append(pd.DataFrame({
                "timestamp": ser.index,
                "symbol": sym,
                "position": ser.values,
            }))
        return pd.concat(long_rows, ignore_index=True) if long_rows else pd.DataFrame(
            columns=["timestamp", "symbol", "position"]
        )


# --------------------------------------------------------------------------- #
# Top-level worker — picklable for ProcessPoolExecutor
# --------------------------------------------------------------------------- #
# IMPORTANT: must be top-level (not a closure) so it survives pickling when
# dispatched to a worker process. Re-loads the strategy module inside the
# worker because module objects don't pickle.
def _run_window_worker(
    strategy_dir_str: str,
    params: dict,
    symbols: list[str],
    split,                              # harness.splits.Split (dataclass, picklable)
    tf: str,
    costs,                              # harness.costs.CostModel (dataclass)
    return_curves: bool,
    lookback,                           # str | pd.Timedelta | None
    seed_hint: int | None,
) -> dict:
    mod = load_statarb_strategy(Path(strategy_dir_str))
    shim = _StatArbShim(mod, params)
    res = base_run_split(
        shim, params, symbols, split,
        tf=tf, costs=costs, return_curves=return_curves,
        lookback=lookback, seed_hint=seed_hint,
    )
    # Attach lifecycle diagnostics.
    statarb_block: dict = {}
    bars_per_unit_map = {
        "1min": 1, "5min": 5, "15min": 15, "30min": 30,
        "1h": 60, "2h": 120, "4h": 240, "6h": 360,
        "8h": 480, "12h": 720, "1d": 1440,
    }
    bpu = bars_per_unit_map.get(tf, 60)
    if shim._registry is not None:
        events = shim._registry.events()
        statarb_block["survival_rate"] = _survival_rate(events, bars_per_unit=bpu)
        statarb_block["n_events"] = len(events)
        realized = []
        half_lives = []
        for ev in events:
            if ev.closed_at is None:
                continue
            mins = (ev.closed_at - ev.opened_at).total_seconds() / 60.0
            realized.append(mins / bpu)
            hl = ev.fit_stats.get("half_life")
            if hl is not None and np.isfinite(hl):
                half_lives.append(float(hl))
        statarb_block["median_realized_lifespan_bars"] = (
            float(np.median(realized)) if realized else None
        )
        statarb_block["median_half_life_bars"] = (
            float(np.median(half_lives)) if half_lives else None
        )
    statarb_block["discovery_log"] = shim._discovery_log
    refit_bars = int(params.get("refit_freq_bars", 168))
    statarb_block["flags"] = build_flags(statarb_block, refit_freq_bars=refit_bars)
    if shim._registry is not None:
        try:
            res["basket_events_df"] = events_to_dataframe(
                shim._registry.events(),
                shim._legs_by_id,
                bars_per_unit=bpu,
            )
        except Exception:
            res["basket_events_df"] = None
    try:
        comp = statarb_composite_score(
            res.get("oos") or {}, statarb_block,
            refit_freq_bars=refit_bars,
        )
    except Exception as exc:
        comp = float("-inf")
        statarb_block["composite_error"] = f"{type(exc).__name__}: {exc}"
    statarb_block["composite"] = comp
    res["statarb"] = statarb_block
    return res


# --------------------------------------------------------------------------- #
# Public entry — single train/OOS split or walk-forward
# --------------------------------------------------------------------------- #
def run_statarb(
    strategy_dir: str | Path,
    period_start: str,
    period_end: str,
    symbols: list[str] | None = None,
    tf: str | None = None,
    params: dict | None = None,
    walk_windows: int = 0,
    embargo: str | pd.Timedelta | None = None,
    costs=None,
    lookback: str | pd.Timedelta | None = None,
    seed_hint: int | None = None,
    return_curves: bool = False,
    walk_expanding: bool = False,
    walk_workers: int = 1,
) -> dict:
    """Top-level stat-arb backtest. Mirrors harness.backtest.run but with the
    two-stage strategy contract and lifecycle bookkeeping.

    `walk_workers` > 1 dispatches walk-forward windows to a
    ProcessPoolExecutor. Each window is a pure function of (strategy_dir,
    params, split) once data is on disk, so this is embarrassingly
    parallel. The main split is always run in-process (cheap, one shot).
    Memory caveat: each worker loads its own panel — at ~500MB per
    worker on a full universe × 24mo × 1h, 4 workers = ~2GB.
    """
    strategy_dir = Path(strategy_dir)
    mod = load_statarb_strategy(strategy_dir)
    p = dict(mod.DEFAULT_PARAMS)
    if params:
        p.update(params)
    if symbols is None:
        symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT", "ETHUSDT"])
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")
    if costs is None:
        costs = DEFAULT_COSTS
    strategy_dir_str = str(strategy_dir)

    main_split = train_oos(period_start, period_end, embargo=embargo)
    main_seed = (int(hash((int(seed_hint), -1)) & 0xFFFFFFFF)
                 if seed_hint is not None else None)
    main = _run_window_worker(
        strategy_dir_str, p, symbols, main_split, tf, costs,
        return_curves, lookback, main_seed,
    )

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
        "mode": "statarb",
        "params": p,
        "symbols": symbols,
        "tf": tf,
        "period": [period_start, period_end],
        "main": main,
    }
    if curves is not None:
        result["curves"] = curves
    if walk_windows > 1:
        wf_splits = walk_forward(period_start, period_end, n_windows=walk_windows,
                                 embargo=embargo, expanding=walk_expanding)
        windows: list[dict] = [None] * len(wf_splits)
        wf_curves: list[dict | None] = [None] * len(wf_splits)
        win_seeds = [
            (int(hash((int(seed_hint), i)) & 0xFFFFFFFF) if seed_hint is not None else None)
            for i in range(len(wf_splits))
        ]
        n_workers = max(1, int(walk_workers))

        def _ingest(i: int, w: dict) -> None:
            sp = wf_splits[i]
            oos_sh = (w.get("oos") or {}).get("sharpe", 0.0)
            sr = (w.get("statarb") or {}).get("survival_rate", float("nan"))
            print(f"[statarb-wf] window {i+1}/{len(wf_splits)} "
                  f"({sp.train_start.date()} -> {sp.oos_end.date()}) done -- "
                  f"OOS Sharpe {oos_sh:+.3f}, survival {sr}", flush=True)
            if return_curves and "equity" in w:
                wf_curves[i] = {
                    "equity": w.pop("equity"),
                    "benchmark": w.pop("benchmark"),
                    "split_cutoff": w.pop("split_cutoff"),
                    "raw_equity": w.pop("raw_equity", None),
                    "funding_cashflow": w.pop("funding_cashflow", None),
                    "oos_returns": w.pop("oos_returns", None),
                    "trades": w.pop("trades", None),
                }
            windows[i] = w

        if n_workers <= 1:
            for i, sp in enumerate(wf_splits):
                print(f"[statarb-wf] window {i+1}/{len(wf_splits)} "
                      f"({sp.train_start.date()} -> {sp.oos_end.date()}) running...",
                      flush=True)
                w = _run_window_worker(
                    strategy_dir_str, p, symbols, sp, tf, costs,
                    return_curves, lookback, win_seeds[i],
                )
                _ingest(i, w)
        else:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            print(f"[statarb-wf] dispatching {len(wf_splits)} windows to "
                  f"{n_workers} worker process(es)", flush=True)
            with ProcessPoolExecutor(max_workers=n_workers) as ex:
                fut_to_i = {
                    ex.submit(
                        _run_window_worker,
                        strategy_dir_str, p, symbols, sp, tf, costs,
                        return_curves, lookback, win_seeds[i],
                    ): i
                    for i, sp in enumerate(wf_splits)
                }
                for fut in as_completed(fut_to_i):
                    i = fut_to_i[fut]
                    w = fut.result()
                    _ingest(i, w)

        result["walk_forward"] = {"windows": windows}
        wf_curves_non_null = [c for c in wf_curves if c is not None]
        if return_curves and wf_curves_non_null:
            result["walk_forward"]["curves"] = wf_curves_non_null
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir", help="Path to strategies_statarb/<name>/")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--tf", default=None)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--walk", type=int, default=0)
    ap.add_argument("--embargo", default=None)
    ap.add_argument("--lookback", default=None)
    ap.add_argument("--workers", type=int, default=1,
                    help="Walk-forward windows in parallel via "
                         "ProcessPoolExecutor (default 1 = sequential).")
    args = ap.parse_args()

    res = run_statarb(
        args.strategy_dir, args.start, args.end,
        symbols=args.symbols, tf=args.tf,
        walk_windows=args.walk, embargo=args.embargo,
        lookback=args.lookback,
        walk_workers=args.workers,
    )
    # Drop curves before json dump.
    res.pop("curves", None)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
