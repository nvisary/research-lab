"""Fixed backtest harness. Do not let the LLM agent edit this file.

The harness:
  1. Loads OHLCV for the given symbols/period.
  2. Calls strategy.generate_signals(data, params) to get target positions.
  3. Pushes positions through vectorbt with realistic costs to get equity & trades.
  4. Computes the standard metric panel.

Usage:
    python -m harness.backtest strategies/ema_pilot --start 2024-01-01 --end 2026-01-01
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
from harness.costs import DEFAULT as DEFAULT_COSTS, build_slippage_matrix
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
def _build_standardized_trades(pf, train_end: pd.Timestamp) -> pd.DataFrame:
    """Pull pf.positions.records_readable, rename to project conventions,
    add `slice` (train/oos) tag based on entry time vs train_end.

    We use ``pf.positions`` (round-trip-level: one row per
    entry → fully-closed cycle) rather than ``pf.trades``
    (partial-fill-level: every size change books a separate row).
    With size-varying strategies (volatility-targeted, dynamic
    rebalance), pf.trades produces N×k records where k is the number
    of partial fills inside one logical round-trip — which inflates
    n_trades, distorts the low-trades-penalty in composite_score, and
    makes the trade ledger unreadable. pf.positions gives us one row
    per logical trade with summed PnL and weighted-average prices.

    Returns an empty DataFrame on any error or empty trade set —
    callers must handle.
    """
    try:
        tr = pf.positions.records_readable.copy()
    except Exception:
        # Fallback for older vectorbt: positions is missing, fall back
        # to trades and accept the partial-fill inflation.
        try:
            tr = pf.trades.records_readable.copy()
        except Exception:
            return pd.DataFrame()
    if tr.empty:
        return pd.DataFrame()
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
    if "entry_time" in tr.columns:
        tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
    if "exit_time" in tr.columns:
        tr["exit_time"] = pd.to_datetime(tr["exit_time"], utc=True)
        if "entry_time" in tr.columns:
            tr["duration_hours"] = (tr["exit_time"] - tr["entry_time"]).dt.total_seconds() / 3600.0
    if "symbol" in tr.columns:
        tr["symbol"] = tr["symbol"].apply(lambda c: c[-1] if isinstance(c, tuple) else c)
    if "entry_time" in tr.columns:
        tr["slice"] = pd.Series(
            np.where(tr["entry_time"] < train_end, "train", "oos"),
            index=tr.index,
        )
    return tr.reset_index(drop=True)


def _augment_trades_with_capacity(trades: pd.DataFrame,
                                   prices_wide: pd.DataFrame,
                                   volumes_wide: pd.DataFrame) -> pd.DataFrame:
    """Add capacity columns to a trade ledger.

    For each trade, compute:
      entry_notional_usd  = |size| * entry_price
      entry_daily_volume_usd = sum_t (price * volume) on the entry date
      participation_pct = entry_notional / entry_daily_volume_usd * 100

    Symbols / dates with missing volume yield NaN. The dashboard and
    metrics aggregator both tolerate NaN.

    Operates on a trade DataFrame already standardized by run_split's
    rename map (entry_time, entry_price, size, symbol). Pre-rename
    callers should use _augment_trades_records_with_capacity instead.
    """
    if trades is None or trades.empty:
        return trades
    out = trades.copy()
    if "entry_time" not in out.columns or "entry_price" not in out.columns:
        return out

    # Daily notional ($) per symbol
    daily_usd = (prices_wide * volumes_wide.reindex_like(prices_wide)).resample("1D").sum()

    entry_notional = (out["size"].abs() * out["entry_price"].abs()).astype(float)
    entry_dates = out["entry_time"].dt.floor("1D")
    syms = out["symbol"].astype(str)

    daily_lookups = []
    for d, s in zip(entry_dates, syms):
        try:
            v = float(daily_usd.loc[d, s])
        except (KeyError, TypeError):
            v = float("nan")
        daily_lookups.append(v)
    out["entry_notional_usd"] = entry_notional
    out["entry_daily_volume_usd"] = daily_lookups
    # Avoid div-by-zero / NaN noise: where volume is 0 or NaN, leave NaN.
    pct = entry_notional / pd.Series(daily_lookups, index=out.index)
    pct = pct.replace([float("inf"), float("-inf")], float("nan")) * 100.0
    out["participation_pct"] = pct
    return out


def _apply_meta_labeler(meta_spec, signals: pd.DataFrame,
                        data: dict[str, pd.DataFrame],
                        prices: pd.DataFrame,
                        split, tf: str) -> tuple[pd.DataFrame, dict]:
    """Train a meta-labeler on the train slice and modulate signals.

    Returns (modulated_signals_long_df, meta_report_dict).

    Implementation outline:
      1. For each symbol with a primary signal, load the declared
         features over the full data range.
      2. Build per-symbol triple-barrier events restricted to those
         that resolve fully before ``split.train_end`` (lookahead-safe).
      3. Concatenate events across symbols → single supervised set.
      4. Fit one MetaLabeler on the pooled set.
      5. Score every symbol's signals using its own features. Modulate
         per spec.mode ("scale" or "gate").
      6. Return modulated signals in the same long-format DataFrame
         schema the strategy produced.

    The whole pipeline degrades gracefully:
      - Missing features in cache → recomputed on the fly via the
        feature store's compute() (cached for next run).
      - Too few training events → raise, caught by caller which leaves
        the primary signal untouched.
    """
    from harness.meta import MetaLabeler, meta_modulate
    from features import compute as feat_compute
    import numpy as _np

    s = signals.copy()
    if s.empty:
        return signals, {"status": "skipped", "reason": "no primary signals"}
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)

    feature_names = list(meta_spec.features)
    if not feature_names:
        return signals, {"status": "skipped", "reason": "no features declared"}

    train_end = split.train_end
    period_start = prices.index.min()
    period_end = prices.index.max() + pd.Timedelta(days=1)

    # Per-symbol features (computed once, cached on disk by features.compute).
    per_sym_features: dict[str, pd.DataFrame] = {}
    per_sym_vol: dict[str, pd.Series] = {}
    for sym in data.keys():
        cols = {}
        for fname in feature_names:
            try:
                cols[fname] = feat_compute(
                    fname, sym, period_start, period_end, tf=tf,
                    use_cache=True,
                )
            except Exception as exc:
                cols[fname] = pd.Series(dtype="float64")
        feat_df = pd.concat(cols.values(), axis=1) if cols else pd.DataFrame()
        if not feat_df.empty:
            feat_df.columns = list(cols.keys())
            feat_df = feat_df.reindex(data[sym].index, method="ffill")
        per_sym_features[sym] = feat_df
        # Volatility series — used to scale triple-barrier widths.
        try:
            v = feat_compute(meta_spec.vol_feature, sym, period_start, period_end,
                             tf=tf, use_cache=True)
            v = v.reindex(data[sym].index, method="ffill")
        except Exception:
            # Fallback: rolling std of close returns at 30 bars.
            v = data[sym]["close"].pct_change().rolling(30, min_periods=30).std(ddof=1)
        per_sym_vol[sym] = v

    # Build pooled training set: one (features, label) pair per primary event
    # whose triple-barrier resolves by train_end (lookahead-safe).
    primary_long = s
    train_X_rows = []
    train_y = []
    train_sides = []
    for sym, sub in primary_long.groupby("symbol", observed=True):
        if sym not in data:
            continue
        close = data[sym]["close"]
        sig = pd.Series(sub["position"].values, index=sub["timestamp"])
        sig = sig[~sig.index.duplicated(keep="last")].sort_index()
        sig = sig.reindex(close.index).fillna(0.0)
        # Restrict events to those that have a chance to fully resolve by train_end:
        # event timestamps ≤ train_end - max_holding_bars * bar_dt.
        if len(close.index) < 2:
            continue
        bar_dt = (close.index[1] - close.index[0])
        max_event_ts = train_end - bar_dt * meta_spec.max_holding_bars
        train_sig = sig.loc[(sig.index <= max_event_ts) & (sig != 0)]
        if train_sig.empty:
            continue
        from harness.labels import meta_labels as _meta_labels
        tb = _meta_labels(
            primary_signal=train_sig, close=close.loc[close.index <= train_end],
            vol=per_sym_vol[sym].loc[per_sym_vol[sym].index <= train_end],
            pt_mult=meta_spec.pt_mult, sl_mult=meta_spec.sl_mult,
            max_holding_bars=meta_spec.max_holding_bars,
        )
        if tb.empty:
            continue
        feats = per_sym_features.get(sym)
        if feats is None or feats.empty:
            continue
        X = feats.reindex(tb.index)
        keep = X.notna().any(axis=1)
        X = X.loc[keep]
        y = tb.loc[X.index, "y"].astype(int)
        if X.empty:
            continue
        train_X_rows.append(X)
        train_y.append(y)
        train_sides.append(tb.loc[X.index, "side"])

    if not train_X_rows:
        return signals, {
            "status": "skipped",
            "reason": "no training events with resolved triple-barriers",
        }

    X_train = pd.concat(train_X_rows, axis=0)
    y_train = pd.concat(train_y, axis=0).astype(int)
    if len(y_train) < meta_spec.min_train_events:
        return signals, {
            "status": "skipped",
            "reason": f"only {len(y_train)} training events "
                      f"(need ≥{meta_spec.min_train_events})",
        }
    if y_train.sum() == 0 or y_train.sum() == len(y_train):
        return signals, {
            "status": "skipped",
            "reason": f"degenerate class balance "
                      f"({int(y_train.sum())}/{len(y_train)} positive)",
        }

    # Fit ONE classifier on the pooled set.
    from harness.meta import _make_classifier as _mk
    model = _mk(meta_spec)
    model.fit(X_train.values, y_train.values)

    # Diagnostics on the train fit.
    proba_train = model.predict_proba(X_train.values)[:, 1]
    pred_train = (proba_train >= meta_spec.threshold).astype(int)
    tp = int(((pred_train == 1) & (y_train.values == 1)).sum())
    fp = int(((pred_train == 1) & (y_train.values == 0)).sum())
    fn = int(((pred_train == 0) & (y_train.values == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    accuracy = float((pred_train == y_train.values).mean())

    # Feature importance (coef magnitude or builtin).
    importances: dict[str, float] = {n: 0.0 for n in X_train.columns}
    try:
        est = model[-1]
        if hasattr(est, "coef_"):
            w = _np.abs(est.coef_[0])
            tot = w.sum()
            if tot > 0:
                importances = {n: float(v / tot)
                               for n, v in zip(X_train.columns, w)}
        elif hasattr(est, "feature_importances_"):
            w = _np.asarray(est.feature_importances_, dtype=float)
            tot = w.sum()
            if tot > 0:
                importances = {n: float(v / tot)
                               for n, v in zip(X_train.columns, w)}
    except Exception:
        pass

    # Apply: for each symbol's signal, predict proba (using lagged
    # features, mirroring how the primary signal is generated) and
    # modulate.
    modulated_rows = []
    for sym, sub in primary_long.groupby("symbol", observed=True):
        feats = per_sym_features.get(sym)
        if feats is None or feats.empty:
            modulated_rows.append(sub)
            continue
        sub_ts = pd.to_datetime(sub["timestamp"], utc=True)
        primary = pd.Series(sub["position"].values, index=sub_ts).sort_index()
        X_lag = feats[X_train.columns].shift(1).reindex(primary.index)
        valid = X_lag.notna().any(axis=1)
        proba = pd.Series(_np.nan, index=primary.index, name="meta_proba")
        if valid.any():
            proba.loc[valid] = model.predict_proba(X_lag.loc[valid].values)[:, 1]
        final = meta_modulate(primary, proba, meta_spec)
        modulated_rows.append(pd.DataFrame({
            "timestamp": final.index,
            "symbol": sym,
            "position": final.values,
        }))

    out_signals = pd.concat(modulated_rows, ignore_index=True) \
        if modulated_rows else signals
    report = {
        "status": "ok",
        "classifier": meta_spec.classifier,
        "mode": meta_spec.mode,
        "threshold": float(meta_spec.threshold),
        "features": list(X_train.columns),
        "n_train_events": int(len(y_train)),
        "n_train_positive": int(y_train.sum()),
        "train_class_balance": float(y_train.mean()),
        "train_accuracy": accuracy,
        "train_precision_at_thresh": float(precision),
        "train_recall_at_thresh": float(recall),
        "feature_importances": importances,
    }
    return out_signals, report


def _positions_to_wide(signals: pd.DataFrame, symbols: list[str],
                       index: pd.DatetimeIndex,
                       max_position: float = 1.0) -> pd.DataFrame:
    """Convert long-format [timestamp, symbol, position] into wide DataFrame
    (index=time, columns=symbols, values=target position clipped
    to [-max_position, +max_position]).

    ``max_position`` defaults to 1.0 (legacy behavior). Strategies may
    override via the ``MAX_POSITION`` module attribute — useful for
    Kelly sizing where a single-asset position can exceed 100% of an
    equal-weight slot. Note: total portfolio exposure remains capped at
    100% by vectorbt's cash_sharing config regardless.
    """
    if signals.empty:
        return pd.DataFrame(0.0, index=index, columns=symbols)

    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    wide = s.pivot_table(index="timestamp", columns="symbol", values="position",
                         aggfunc="last")
    wide = wide.reindex(index=index, columns=symbols)
    wide = wide.ffill().fillna(0.0).clip(-max_position, max_position)
    return wide


def _run_vectorbt(prices: pd.DataFrame, target_pos: pd.DataFrame,
                  costs=DEFAULT_COSTS, init_cash: float = 10_000.0,
                  volumes: pd.DataFrame | None = None,
                  raw_sizing: bool = False):
    """Run a vectorbt portfolio from target position weights.

    Two sizing modes:

    - **default (``raw_sizing=False``):** ``size = target_pos / n_symbols``.
      ``position[i] = +1`` means asset ``i`` gets ``1/n`` of equity. All
      symbols at +1 → 100% equity allocated equal-weight. Natural for
      cross-sectional baskets and trend-following on a basket. Backward
      compatible with all legacy strategies.

    - **raw (``raw_sizing=True``):** ``size = target_pos`` directly.
      ``position[i] = +0.5`` means asset ``i`` gets 50% of equity.
      Natural for single-asset Kelly or any agent that wants to think
      in terms of "fraction of equity" per asset. Multi-asset users
      should ensure ``sum(|position|) <= 1`` to avoid hitting
      cash_sharing's implicit no-leverage cap.

    Slippage is built via ``build_slippage_matrix`` and respects the
    same notional convention.
    """
    n = prices.shape[1]
    size = target_pos if raw_sizing else target_pos / n
    slippage = build_slippage_matrix(prices, volumes, target_pos, init_cash, costs,
                                       raw_sizing=raw_sizing)
    pf = vbt.Portfolio.from_orders(
        close=prices,
        size=size,
        size_type="targetpercent",
        fees=costs.taker_fee,
        slippage=slippage,
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
              tf: str = "1h", costs=DEFAULT_COSTS, return_curves: bool = False,
              lookback: str | pd.Timedelta | None = None,
              seed_hint: int | None = None) -> dict:
    """Backtest a single train/OOS split. Returns {'train': metrics, 'oos': metrics, ...}.

    If `return_curves=True`, also returns 'equity' and 'benchmark' Series spanning
    the full train+OOS window (benchmark = equal-weight buy-and-hold).

    ``lookback`` (e.g. ``"60D"``) pads the data load BEFORE
    ``split.train_start`` so rolling-indicator strategies have a
    pre-warmed history at bar 1 instead of wasting the first ~lookback
    bars of the window emitting empty signals. Mirrors what a live
    operator does — they look at history, they don't wait for it.

    Mechanics:
      - load_many is called with ``train_start - lookback`` as start
      - strategy sees the padded data dict and computes signals over it
      - vectorbt runs on the full padded range; equity drifts as
        signals fire during padding
      - all per-slice metric masks already restrict to ``train_start``
        onwards so padding doesn't pollute Sharpe / DD / n_trades
      - return_curves payload is trimmed to ``[train_start, oos_end)``
        so the dashboard doesn't show padding flats
    """
    lookback_td = pd.Timedelta(lookback) if lookback else pd.Timedelta(0)
    data_start = split.train_start - lookback_td
    data = load_many(symbols, data_start, split.oos_end, tf=tf)
    data = {s: df for s, df in data.items() if not df.empty}
    if not data:
        return {"train": {}, "oos": {}, "error": "no data"}

    # Sizing-mode flags read from the strategy module (defaults preserve
    # legacy behavior for existing strategies that don't set them).
    raw_sizing = bool(getattr(strategy_mod, "RAW_SIZING", False))
    max_position = float(getattr(strategy_mod, "MAX_POSITION", 1.0))

    raw_prices = pd.concat({s: df["close"] for s, df in data.items()}, axis=1)
    raw_prices = raw_prices.dropna(how="all")
    symbols_present = list(raw_prices.columns)

    # Volume matrix — only used by dynamic-slippage size impact; harmless
    # to construct unconditionally (a few MB on the largest universe).
    raw_volumes = pd.concat({s: df["volume"] for s, df in data.items()}, axis=1)
    raw_volumes = raw_volumes.reindex_like(raw_prices)

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

    # ---- Optional meta-labeling pass ----
    # If the strategy exports META_LABELER, train a secondary classifier
    # on triple-barrier outcomes of its OWN signals over the train slice,
    # then modulate OOS signals by P(trade pays off | features).
    # Lookahead-safe: training events are filtered to those that fully
    # resolve before split.train_end. See harness/meta.py for details.
    # If META_LABELER is absent, ``signals`` passes through unchanged.
    meta_report: dict | None = None
    try:
        meta_spec = getattr(strategy_mod, "META_LABELER", None)
    except Exception:
        meta_spec = None
    if meta_spec is not None:
        try:
            signals, meta_report = _apply_meta_labeler(
                meta_spec, signals, data, prices, split, tf,
            )
        except Exception as exc:
            # Meta-labeling failures must NEVER kill the backtest — the
            # primary signal is the source of truth. Record the error
            # in the report so the UI can surface it, and proceed
            # with the unmodified signal.
            meta_report = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }

    target = _positions_to_wide(signals, symbols_present, prices.index,
                                  max_position=max_position)
    # Force flat on stale bars: closes any open position at the last known
    # price and prevents re-entry while data is still missing.
    target = target.where(~stale_mask.reindex_like(target).fillna(False), 0.0)
    # Force flat during the lookback PADDING period (bars before train_start).
    # Padding exists so rolling indicators are warm by bar 1 of the window —
    # the strategy is NOT supposed to trade during it. Without this guard,
    # vectorbt happily acts on padding-period signals and the resulting
    # entries leak into the saved equity (per-window starting equity != init_cash)
    # and trade ledger (n_trades / total_pnl inflated by padding trades that
    # also overlap calendar-wise with the prior WF window's evaluation slice,
    # double-counting their PnL).
    if split.train_start in target.index or (target.index < split.train_start).any():
        target.loc[target.index < split.train_start, :] = 0.0

    pf = _run_vectorbt(prices, target, costs=costs, volumes=raw_volumes,
                        raw_sizing=raw_sizing)

    try:
        # See _build_standardized_trades: pf.positions gives one row per
        # round-trip; pf.trades inflates by the number of partial fills.
        trade_records = pf.positions.records_readable
        entry_times = pd.to_datetime(trade_records["Entry Timestamp"], utc=True)
    except Exception:
        entry_times = pd.Series(dtype="datetime64[ns, UTC]")

    # Build a standardized trade ledger once — used for both per-slice
    # capacity metrics (always) and the trades artifact (when
    # return_curves=True). Cheap; trade count is bounded by walk_window
    # length × strategy turnover.
    trades_all_df = _build_standardized_trades(pf, split.train_end)
    if not trades_all_df.empty:
        trades_all_df = _augment_trades_with_capacity(trades_all_df, raw_prices, raw_volumes)

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

    out: dict = {}
    for label, lo, hi in [("train", split.train_start, split.train_end),
                           ("oos", split.oos_start, split.oos_end)]:
        mask = (adj_equity_full.index >= lo) & (adj_equity_full.index < hi)
        equity = adj_equity_full[mask]
        rets = adj_returns_full[mask]
        positions = target[mask]
        n_trades = int(((entry_times >= lo) & (entry_times < hi)).sum()) if len(entry_times) else 0
        if not trades_all_df.empty:
            slice_trades = trades_all_df[
                (trades_all_df["entry_time"] >= lo) & (trades_all_df["entry_time"] < hi)
            ]
        else:
            slice_trades = trades_all_df
        out[label] = M.summary(equity, rets, positions, n_trades=n_trades, tf=tf,
                                benchmark=bench[mask],
                                trades_in_slice=slice_trades,
                                seed_hint=seed_hint)

    # Derived: train→OOS Sharpe gap. Overfitting indicator: > 1.0 is
    # a strong signal that the strategy fit the training period rather
    # than generalised. Stored on the OOS dict because that's where
    # readers look when assessing edge quality.
    try:
        sg = float(out["train"].get("sharpe", 0.0)) - float(out["oos"].get("sharpe", 0.0))
        out["oos"]["sharpe_gap"] = sg
    except Exception:
        out["oos"]["sharpe_gap"] = None

    # Attach meta-labeler report (if any) — small dict, fine in every payload.
    if meta_report is not None:
        out["meta_labeler"] = meta_report

    if return_curves:
        # Trim curves to the evaluation window [train_start, oos_end).
        # The padding bars before train_start are blind to the operator's
        # judgment — they were loaded only so rolling indicators were
        # warm by bar 1 of the window.
        ts = split.train_start
        out["equity"] = adj_equity_full[adj_equity_full.index >= ts]
        out["raw_equity"] = raw_equity_full[raw_equity_full.index >= ts]
        out["funding_cashflow"] = fcf[fcf.index >= ts]
        # Benchmark was normalised at the first PADDED bar (data_start),
        # so by the time we trim to train_start it has already absorbed
        # the padding-period market drift. Rebase so bench starts at the
        # same value as equity at train_start — i.e. "$10k buy-and-hold
        # starting at the window's first evaluation bar".
        bench_trim = bench[bench.index >= ts]
        if not bench_trim.empty and not out["equity"].empty:
            scale = float(out["equity"].iloc[0]) / float(bench_trim.iloc[0])
            bench_trim = bench_trim * scale
        out["benchmark"] = bench_trim
        out["split_cutoff"] = split.train_end

        # OOS returns slice — used by iterate.py to compute DSR/PSR/CI on the
        # same series as composite, post-funding-adjustment.
        oos_mask = (adj_equity_full.index >= split.oos_start) & \
                   (adj_equity_full.index < split.oos_end)
        out["oos_returns"] = adj_returns_full[oos_mask]

        # Standardized trade ledger for the dashboard / per-iter analysis.
        # Already built and augmented above; just expose it on the curves dict.
        if not trades_all_df.empty:
            keep_cols = [c for c in [
                "entry_time", "exit_time", "symbol", "direction",
                "size", "entry_price", "exit_price",
                "pnl_quote", "return_pct", "duration_hours", "slice",
                "entry_notional_usd", "entry_daily_volume_usd", "participation_pct",
            ] if c in trades_all_df.columns]
            out["trades"] = trades_all_df[keep_cols].reset_index(drop=True)
        else:
            out["trades"] = trades_all_df
    return out


def run(strategy_dir: str | Path, period_start: str, period_end: str,
        symbols: list[str] | None = None, tf: str = "1h",
        params: dict | None = None, walk_windows: int = 0,
        return_curves: bool = False,
        embargo: str | pd.Timedelta | None = None,
        costs=None,
        lookback: str | pd.Timedelta | None = None,
        seed_hint: int | None = None,
        walk_expanding: bool = False) -> dict:
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
    if costs is None:
        costs = DEFAULT_COSTS

    main_split = train_oos(period_start, period_end, embargo=embargo)
    # Hash the seed_hint with window index (-1 for the single-split main)
    # so each WF window — and the main split — get distinct deterministic
    # bootstrap draws while remaining reproducible per (iter, window).
    main_seed = (int(hash((int(seed_hint), -1)) & 0xFFFFFFFF)
                 if seed_hint is not None else None)
    main = run_split(mod, p, symbols, main_split, tf=tf, costs=costs,
                     return_curves=return_curves, lookback=lookback,
                     seed_hint=main_seed)

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
                                 embargo=embargo, expanding=walk_expanding)
        for i, sp in enumerate(wf_splits):
            print(f"[wf] window {i+1}/{len(wf_splits)} "
                  f"({sp.train_start.date()} -> {sp.oos_end.date()}) running...",
                  flush=True)
            win_seed = (int(hash((int(seed_hint), i)) & 0xFFFFFFFF)
                        if seed_hint is not None else None)
            w = run_split(mod, p, symbols, sp, tf=tf, costs=costs,
                          return_curves=return_curves, lookback=lookback,
                          seed_hint=win_seed)
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
    ap.add_argument("--end", default="2026-01-01",
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
    ap.add_argument("--cost-model", choices=["static", "spread", "full"],
                    default="static",
                    help="static (default) = legacy flat slippage. "
                         "spread = per-bar half-spread from saved estimates. "
                         "full = spread + size-impact. See harness/costs.py.")
    ap.add_argument("--lookback", default=None,
                    help="Pre-load history before each window's train_start "
                         "by this much (e.g. '60D', '12h'). Lets rolling "
                         "indicators be warmed by bar 1 of the window instead "
                         "of wasting the first ~lookback bars. Default: 0 "
                         "(legacy behavior, blind warmup).")
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

    from harness.costs import CostModel
    cost_kwargs = {
        "static": {},
        "spread": {"use_dynamic_spread": True},
        "full": {"use_dynamic_spread": True, "use_dynamic_slippage": True},
    }[args.cost_model]
    costs = CostModel(**cost_kwargs)

    res = run(args.strategy_dir, ps, pe, symbols=args.symbols, tf=tf,
              walk_windows=args.walk, embargo=args.embargo, costs=costs,
              lookback=args.lookback)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
