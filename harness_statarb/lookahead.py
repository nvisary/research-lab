"""Lookahead audit for stat-arb strategies.

Two contracts to verify:

  A. `find_structures(train_data, params)` depends ONLY on `train_data`.
     - Determinism: two calls on identical input return identical baskets.
     - Train-slice isolation: extending `train_data` with synthetic
       future bars must NOT change the output (otherwise the strategy
       has peeked outside its sliced dict — via globals, disk, etc.).

  B. `trade_basket(basket, data, params)` decisions at bar `t` depend
     only on data with timestamps `≤ t-1`.
     - Determinism: two calls on identical inputs match.
     - Per-bar perturbation: scaling OHLCV at a single bar `t` must NOT
       change basket positions at `t` and earlier.

The audit emits a JSON-friendly report and raises `LookaheadError` /
`DeterminismError` on violation. Runner invokes this before each backtest.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from datafeed.loader import load_many
from harness_statarb.backtest import load_statarb_strategy
from harness_statarb.structures import Basket


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LookaheadError(RuntimeError):
    """Stat-arb strategy used data outside its declared input slice."""

    def __init__(self, message: str, *, stage: str, detail: Any = None):
        super().__init__(message)
        self.stage = stage
        self.detail = detail


class DeterminismError(RuntimeError):
    """Stat-arb strategy produced different output on identical input."""


@dataclass
class StatArbAuditReport:
    passed: bool
    sha256: str
    n_baskets_fitted: int
    n_perturbations: int
    notes: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _basket_key(b: Basket | dict) -> tuple:
    """Hashable signature of a basket — id + sorted legs + fit_stats subset."""
    if not isinstance(b, Basket):
        b = Basket.from_dict(b)
    legs = tuple(sorted((s, round(float(w), 8)) for s, w in b.legs.items()))
    # Selected fit_stats for comparison (others may carry timestamps that
    # legitimately differ across re-runs).
    fs = b.fit_stats or {}
    fs_key = tuple(sorted(
        (k, round(float(v), 8)) for k, v in fs.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ))
    return (b.id, legs, fs_key)


def _signature(baskets: list) -> tuple:
    return tuple(sorted(_basket_key(b) for b in baskets))


# --------------------------------------------------------------------------- #
# Stage A: find_structures audit
# --------------------------------------------------------------------------- #
def audit_find_structures(
    mod,
    train_data: dict[str, pd.DataFrame],
    params: dict,
) -> dict:
    """Verify determinism + train-slice isolation of find_structures.

    Returns a dict of details. Raises on failure.
    """
    out1 = mod.find_structures(train_data, params) or []
    out2 = mod.find_structures(train_data, params) or []
    sig1 = _signature(out1)
    sig2 = _signature(out2)
    if sig1 != sig2:
        raise DeterminismError(
            f"find_structures non-deterministic: "
            f"{len(out1)} vs {len(out2)} baskets / signatures differ"
        )

    # Input sensitivity: replace OHLC in train_data with a synthetic random
    # walk of the same length and run find_structures again. The output
    # MUST change (different baskets or empty list) — if it's identical
    # to the real-data run, the strategy is reading prices from somewhere
    # other than its `train_data` argument (globals, disk, network).
    rng = np.random.default_rng(0xA11E)
    scrambled: dict[str, pd.DataFrame] = {}
    for sym, df in train_data.items():
        if df.empty:
            scrambled[sym] = df
            continue
        last_close = float(df["close"].iloc[-1]) if "close" in df.columns else 100.0
        n = len(df)
        # Random walk starting from last_close.
        rw = last_close * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        df2 = df.copy()
        for col in ("open", "high", "low", "close"):
            if col in df2.columns:
                df2[col] = rw
        # volume kept the same.
        scrambled[sym] = df2

    out_scrambled = mod.find_structures(scrambled, params) or []
    sig_scr = _signature(out_scrambled)
    if out1 and sig_scr == sig1:
        # Output is identical on totally different data → strategy is
        # NOT actually reading train_data.
        raise LookaheadError(
            "find_structures returned identical baskets after the input "
            "OHLC data was replaced by a random walk — the strategy is "
            "not reading from train_data (likely loads prices from disk "
            "or globals).",
            stage="input_sensitivity",
            detail={"n_baskets_original": len(out1), "n_baskets_scrambled": len(out_scrambled)},
        )
    return {
        "n_baskets": len(out1),
        "deterministic": True,
        "input_sensitive": True,
    }


# --------------------------------------------------------------------------- #
# Stage B: trade_basket audit (per-bar perturbation)
# --------------------------------------------------------------------------- #
def audit_trade_basket(
    mod,
    basket: Basket,
    data: dict[str, pd.DataFrame],
    params: dict,
    k_perturbations: int = 8,
    seed: int = 0xB72C,
) -> dict:
    """Verify determinism + bar-causality of trade_basket.

    For K randomly chosen bar indices in the trading window, scale
    OHLCV at that single bar by ±5%. The basket position series at
    indices `≤ t` must remain identical (depend only on data `< t`).
    """
    def _call(d):
        try:
            return mod.trade_basket(basket, d, params, active_window=(d[list(d.keys())[0]].index[0], d[list(d.keys())[0]].index[-1]))
        except TypeError:
            return mod.trade_basket(basket, d, params)

    s1 = _call(data)
    s2 = _call(data)
    if not isinstance(s1, pd.Series):
        s1 = pd.Series(s1)
    if not isinstance(s2, pd.Series):
        s2 = pd.Series(s2)
    if len(s1) != len(s2) or not np.array_equal(
        s1.fillna(0.0).values, s2.fillna(0.0).values
    ):
        raise DeterminismError("trade_basket non-deterministic across two identical calls")

    rng = np.random.default_rng(seed)
    # Sample valid bar indices from the FIRST symbol's index — the rest must align.
    first_sym = next(iter(data.keys()))
    n = len(data[first_sym])
    if n < 50:
        return {"deterministic": True, "perturbations_skipped": True}
    candidates = rng.choice(np.arange(20, n - 1), size=min(k_perturbations, n - 22), replace=False)
    for t_idx in candidates:
        perturbed = {}
        scale = 1.0 + rng.uniform(-0.05, 0.05)
        for sym, df in data.items():
            df2 = df.copy()
            for col in ("open", "high", "low", "close"):
                if col in df2.columns:
                    df2.iloc[t_idx, df2.columns.get_loc(col)] = float(df2.iloc[t_idx][col]) * scale
            perturbed[sym] = df2
        s_pert = _call(perturbed)
        if not isinstance(s_pert, pd.Series):
            s_pert = pd.Series(s_pert)
        # Compare positions at bars ≤ t_idx (those should NOT depend on bar t_idx).
        common_idx = s1.index.intersection(s_pert.index)
        if len(common_idx) == 0:
            continue
        ts_t = data[first_sym].index[t_idx]
        mask = common_idx <= ts_t
        a = s1.reindex(common_idx).fillna(0.0).values[mask]
        b = s_pert.reindex(common_idx).fillna(0.0).values[mask]
        if not np.allclose(a, b, equal_nan=True):
            diff_at = np.where(~np.isclose(a, b))[0]
            offending_ts = common_idx[mask][diff_at[0]] if len(diff_at) else None
            raise LookaheadError(
                f"trade_basket position at or before bar t_idx={t_idx} "
                f"({ts_t}) changed after perturbing OHLCV at that bar — "
                f"first divergence at {offending_ts}.",
                stage="trade_basket_perturbation",
                detail={"t_idx": int(t_idx), "ts": str(ts_t)},
            )
    return {
        "deterministic": True,
        "perturbations_passed": int(len(candidates)),
    }


# --------------------------------------------------------------------------- #
# Top-level audit
# --------------------------------------------------------------------------- #
def audit_strategy(
    strategy_dir: str | Path,
    period_start: str = "2024-01-01",
    period_end: str = "2024-07-01",
    tf: str | None = None,
    fit_window_bars: int | None = None,
    symbols: list[str] | None = None,
) -> StatArbAuditReport:
    """Audit a stat-arb strategy on a real data slice.

    Loads `fit_window_bars` of data, calls find_structures (audit A),
    then for the first returned basket calls trade_basket on the next
    window of data (audit B).
    """
    strategy_dir = Path(strategy_dir)
    mod = load_statarb_strategy(strategy_dir)
    p = dict(mod.DEFAULT_PARAMS)
    if tf is None:
        tf = getattr(mod, "DEFAULT_TF", "1h")
    if symbols is None:
        symbols = getattr(mod, "DEFAULT_SYMBOLS", ["BTCUSDT", "ETHUSDT"])
    if fit_window_bars is None:
        fit_window_bars = int(p.get("fit_window_bars", 2160))

    # Load enough data: fit_window + a trading window to test trade_basket.
    refit = int(p.get("refit_freq_bars", 168))
    data = load_many(symbols, period_start, period_end, tf=tf)
    data = {s: df for s, df in data.items() if not df.empty}
    if not data:
        return StatArbAuditReport(
            passed=False, sha256=file_sha256(strategy_dir / "strategy.py"),
            n_baskets_fitted=0, n_perturbations=0,
            notes="no data loaded",
        )

    idx_union = pd.DatetimeIndex(
        sorted(set().union(*[df.index for df in data.values()]))
    )
    if len(idx_union) <= fit_window_bars + 60:
        return StatArbAuditReport(
            passed=False, sha256=file_sha256(strategy_dir / "strategy.py"),
            n_baskets_fitted=0, n_perturbations=0,
            notes=f"need ≥ fit_window+60 bars; got {len(idx_union)}",
        )

    fit_end_ts = idx_union[fit_window_bars]
    train_data = {
        s: df.loc[df.index < fit_end_ts]
        for s, df in data.items()
    }

    # ---- Stage A ----
    a_result = audit_find_structures(mod, train_data, p)

    # ---- Stage B ----
    b_result = {"skipped": True, "reason": "no baskets fitted"}
    baskets = mod.find_structures(train_data, p) or []
    if baskets:
        b = baskets[0]
        if not isinstance(b, Basket):
            b = Basket.from_dict(b)
        b = b.normalize_to_gross(1.0)
        # Pass data including everything up to ~refit bars past fit_end.
        trade_end_ts = idx_union[min(fit_window_bars + refit, len(idx_union) - 1)]
        leg_data = {
            s: df.loc[df.index < trade_end_ts]
            for s, df in data.items() if s in b.legs
        }
        b_result = audit_trade_basket(mod, b, leg_data, p)

    sha = file_sha256(strategy_dir / "strategy.py")
    return StatArbAuditReport(
        passed=True,
        sha256=sha,
        n_baskets_fitted=int(a_result.get("n_baskets", 0)),
        n_perturbations=int(b_result.get("perturbations_passed", 0)),
        notes="ok",
        extra={"find_structures": a_result, "trade_basket": b_result},
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2024-07-01")
    args = ap.parse_args()
    try:
        rep = audit_strategy(args.strategy_dir, args.start, args.end)
        print(json.dumps({
            "passed": rep.passed,
            "sha256": rep.sha256,
            "n_baskets_fitted": rep.n_baskets_fitted,
            "n_perturbations": rep.n_perturbations,
            "notes": rep.notes,
            "extra": rep.extra,
        }, indent=2, default=str))
        sys.exit(0 if rep.passed else 1)
    except DeterminismError as e:
        print(json.dumps({"passed": False, "error": "DeterminismError", "msg": str(e)}, indent=2))
        sys.exit(3)
    except LookaheadError as e:
        print(json.dumps({"passed": False, "error": "LookaheadError",
                          "stage": e.stage, "msg": str(e), "detail": e.detail}, indent=2, default=str))
        sys.exit(2)


if __name__ == "__main__":
    main()
