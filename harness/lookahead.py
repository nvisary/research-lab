"""Lookahead-bias audit for trading strategies.

The contract enforced here: a strategy's ``position[t]`` may depend only on
data with timestamp strictly less than ``t``. Concurrent-bar usage
(``close[t]`` for ``pos[t]`` without ``.shift(1)``) and forward-looking
indicators (``close.shift(-N)``, centered rolling, etc.) are both bugs.

Three layered checks:

1. **Determinism.** Run the strategy twice on identical inputs. Outputs
   must match. Otherwise we can't distinguish lookahead bugs from RNG noise.

2. **Tail-poison test.** Replace OHLCV at indices ``>= C`` with NaN. Signals
   at indices ``< C`` use only data at indices ``< C``, which is unchanged,
   so they must be bit-for-bit identical. Detects forward-shift and
   centered-rolling style bugs (where future bars leak into past signals).

3. **Per-bar perturbation.** For ``K`` randomly chosen bar indices, scale
   the OHLCV at exactly that bar. Signals at indices ``<= t`` use only data
   at indices ``< t`` (or ``< t`` ≤ ``t``-1), which is unchanged, so they
   must remain identical. Detects concurrent-bar usage (``pos[t]`` reading
   ``close[t]``) which the tail-poison test misses.

Comparison is on the raw output of ``generate_signals`` reshaped to
``(timestamp, symbol)`` wide form WITHOUT ffill — both "different value"
and "appeared/disappeared" count as a divergence. NaN==NaN is treated as
equal so unaffected warmup bars don't false-positive.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Errors and report
# --------------------------------------------------------------------------- #
class LookaheadError(RuntimeError):
    """Strategy used data at or beyond the bar it was deciding for."""

    def __init__(self, message: str, *, mode: str, offending: list | None = None):
        super().__init__(message)
        self.mode = mode
        self.offending = offending or []


class DeterminismError(RuntimeError):
    """Strategy produced different outputs on identical inputs."""


@dataclass
class AuditReport:
    passed: bool
    sha256: str
    duration_seconds: float
    n_symbols_tested: int
    n_bars_tested: int
    k_perturbations: int
    notes: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
OHLCV = ("open", "high", "low", "close", "volume")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signals_to_wide(signals: pd.DataFrame, symbols: list[str],
                     index: pd.DatetimeIndex) -> pd.DataFrame:
    """Pivot long-format signals to wide. NO ffill — purer audit semantics.

    Missing entries become NaN in the wide frame. NaN==NaN is treated as a
    match in the diff routine, so warmup bars (where the strategy chose not
    to emit) don't false-positive.
    """
    if signals is None or len(signals) == 0:
        return pd.DataFrame(np.nan, index=index, columns=symbols)
    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    wide = s.pivot_table(index="timestamp", columns="symbol", values="position",
                         aggfunc="last")
    return wide.reindex(index=index, columns=symbols)


def _diff_positions(orig_wide: pd.DataFrame, pert_wide: pd.DataFrame,
                    *, valid_index: pd.DatetimeIndex,
                    atol: float = 1e-9) -> list[tuple]:
    """Return list of (timestamp, symbol, orig_value, pert_value) where the
    two wide frames disagree on rows in ``valid_index``. NaN==NaN matches.
    """
    if len(orig_wide) == 0 or len(pert_wide) == 0:
        return []
    a = orig_wide.loc[orig_wide.index.intersection(valid_index)]
    b = pert_wide.reindex(index=a.index, columns=a.columns)

    diff: list[tuple] = []
    for sym in a.columns:
        sa = a[sym].values
        sb = b[sym].values
        # NaN==NaN as equal
        both_nan = np.isnan(sa) & np.isnan(sb)
        close = ~np.isnan(sa) & ~np.isnan(sb) & (np.abs(sa - sb) <= atol)
        equal = both_nan | close
        if equal.all():
            continue
        bad_idx = np.where(~equal)[0]
        for i in bad_idx[:5]:  # cap per symbol to keep reports small
            diff.append((a.index[i], sym, float(sa[i]) if not np.isnan(sa[i]) else float("nan"),
                         float(sb[i]) if not np.isnan(sb[i]) else float("nan")))
    return diff


def _sample_subset(data: dict[str, pd.DataFrame], n_symbols: int,
                   n_bars: int) -> dict[str, pd.DataFrame]:
    """Take the last ``n_bars`` rows of the first ``n_symbols`` symbols.

    Returns a dict that is independent of ``data`` (each frame is a copy).
    """
    out: dict[str, pd.DataFrame] = {}
    for sym in list(data.keys())[:n_symbols]:
        df = data[sym]
        if df.empty:
            continue
        out[sym] = df.tail(n_bars).copy()
    return out


def _canonical_index(sub: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Use the union of all symbols' timestamps. Strategies may keep their own
    per-symbol index, so we don't enforce alignment here."""
    if not sub:
        return pd.DatetimeIndex([], tz="UTC")
    idxs = [df.index for df in sub.values()]
    out = idxs[0]
    for i in idxs[1:]:
        out = out.union(i)
    return out


def _poison_tail(sub: dict[str, pd.DataFrame], cutoff_idx: int) -> dict[str, pd.DataFrame]:
    """Return a fresh copy with OHLCV at canonical-index positions ``>= cutoff_idx``
    set to NaN, per symbol. Symbols that don't share that exact bar are
    poisoned at their own `>= cutoff_idx`-th row, which is fine — we compare
    only on bars present in the canonical index."""
    out = {}
    for sym, df in sub.items():
        df2 = df.copy()
        cols = [c for c in OHLCV if c in df2.columns]
        if cutoff_idx < len(df2):
            df2.iloc[cutoff_idx:, df2.columns.get_indexer(cols)] = np.nan
        out[sym] = df2
    return out


def _perturb_bar(sub: dict[str, pd.DataFrame], target_sym: str,
                 t_idx: int, factor: float) -> dict[str, pd.DataFrame]:
    """Multiply OHLCV at exactly ``sub[target_sym].iloc[t_idx]`` by ``factor``.

    All other rows and other symbols are untouched.
    """
    out = {s: df.copy() for s, df in sub.items()}
    df = out[target_sym]
    cols_present = [c for c in OHLCV if c in df.columns]
    if 0 <= t_idx < len(df):
        df.iloc[t_idx, df.columns.get_indexer(cols_present)] = (
            df.iloc[t_idx][cols_present].values * factor
        )
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def audit(mod: Any, data: dict[str, pd.DataFrame], params: dict, *,
          k: int = 12, cutoff_frac: float = 0.7, atol: float = 1e-9,
          sample_symbols: int = 2, sample_bars: int = 1500,
          warmup_skip: int = 30, seed: int = 42) -> AuditReport:
    """Run determinism + tail-poison + per-bar perturbation tests.

    Raises ``DeterminismError`` or ``LookaheadError`` on the first failure.
    Returns ``AuditReport(passed=True, ...)`` on success.

    Parameters
    ----------
    mod : module
        Strategy module exposing ``generate_signals(data, params)``.
    data : dict[str, DataFrame]
        Full dataset. The audit subsets it for cost.
    params : dict
        Parameters to pass to the strategy.
    k : int
        Number of per-bar perturbations to try.
    cutoff_frac : float
        Fraction of the index at which the tail-poison cutoff is placed.
    atol : float
        Absolute tolerance for "equal" position values.
    sample_symbols, sample_bars : int
        Subset size for cost control.
    warmup_skip : int
        Avoid sampling perturbation bars in the first ``warmup_skip`` rows
        (where indicators are still warming up and signals are NaN regardless).
    seed : int
        RNG seed for reproducibility.
    """
    t0 = time.time()
    sub = _sample_subset(data, sample_symbols, sample_bars)
    if not sub:
        return AuditReport(passed=True, sha256="", duration_seconds=0.0,
                           n_symbols_tested=0, n_bars_tested=0,
                           k_perturbations=0,
                           notes="no data — audit skipped")

    cidx = _canonical_index(sub)
    n = len(cidx)
    symbols = list(sub.keys())

    # ---- Determinism ----
    sig_a = mod.generate_signals(sub, params)
    sig_b = mod.generate_signals(sub, params)
    wide_a = _signals_to_wide(sig_a, symbols, cidx)
    wide_b = _signals_to_wide(sig_b, symbols, cidx)
    diffs = _diff_positions(wide_a, wide_b, valid_index=cidx, atol=atol)
    if diffs:
        first = diffs[0]
        raise DeterminismError(
            f"strategy is non-deterministic: identical inputs produced different "
            f"signals; e.g. at {first[0]} for {first[1]}: {first[2]} vs {first[3]}. "
            f"If you use randomness, fix a seed inside generate_signals."
        )
    sig_orig_wide = wide_a

    # ---- Tail-poison ----
    cutoff_idx = max(warmup_skip + 5, int(n * cutoff_frac))
    if cutoff_idx >= n - 1:
        # Sample too short for a meaningful tail-poison; skip but keep going.
        tail_done = False
    else:
        poisoned = _poison_tail(sub, cutoff_idx)
        sig_poison = mod.generate_signals(poisoned, params)
        sig_poison_wide = _signals_to_wide(sig_poison, symbols, cidx)
        pre_cutoff = cidx[:cutoff_idx]
        diffs = _diff_positions(sig_orig_wide, sig_poison_wide,
                                valid_index=pre_cutoff, atol=atol)
        if diffs:
            first = diffs[0]
            raise LookaheadError(
                f"tail-poison: replacing future data (bars >= {cidx[cutoff_idx]}) "
                f"with NaN changed the signal at {first[0]} for {first[1]} "
                f"from {first[2]} to {first[3]}. The strategy uses future data "
                f"to compute past signals (forward-shift, centered rolling, etc.).",
                mode="tail_poison",
                offending=diffs[:5],
            )
        tail_done = True

    # ---- Per-bar perturbation ----
    rng = np.random.default_rng(seed)
    pool = [i for i in range(warmup_skip, n - 1)]  # avoid warmup, last bar
    if not pool or n < warmup_skip + k:
        return AuditReport(
            passed=True, sha256="",
            duration_seconds=time.time() - t0,
            n_symbols_tested=len(symbols), n_bars_tested=n,
            k_perturbations=0,
            notes=f"sample too short ({n} bars) for per-bar perturbation; "
                  f"tail-poison done={tail_done}",
        )

    bar_indices = rng.choice(pool, size=min(k, len(pool)), replace=False).tolist()
    bar_indices.sort()

    # Rotate which symbol we perturb so multi-symbol strategies are exercised.
    for i, t_idx in enumerate(bar_indices):
        target_sym = symbols[i % len(symbols)]
        factor = float(rng.choice([0.95, 1.05]))
        perturbed = _perturb_bar(sub, target_sym, t_idx, factor)
        sig_p = mod.generate_signals(perturbed, params)
        sig_p_wide = _signals_to_wide(sig_p, symbols, cidx)
        # Signals at canonical bars 0..t_idx (inclusive) must be unchanged.
        valid = cidx[: t_idx + 1]
        diffs = _diff_positions(sig_orig_wide, sig_p_wide, valid_index=valid, atol=atol)
        if diffs:
            first = diffs[0]
            raise LookaheadError(
                f"per-bar: scaling OHLCV at {cidx[t_idx]} for {target_sym} "
                f"by {factor:g} changed the signal at {first[0]} for {first[1]} "
                f"from {first[2]} to {first[3]}. position[t] must depend only on "
                f"data[<t]; this strategy reads concurrent or future data. "
                f"Common fix: add `.shift(1)` to the indicator before emitting.",
                mode="per_bar",
                offending=diffs[:5],
            )

    return AuditReport(
        passed=True, sha256="",
        duration_seconds=time.time() - t0,
        n_symbols_tested=len(symbols),
        n_bars_tested=n,
        k_perturbations=len(bar_indices),
        notes="all checks passed",
        extra={"cutoff_idx": cutoff_idx, "perturbed_bars": [str(cidx[i]) for i in bar_indices]},
    )
