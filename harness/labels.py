"""Labeling utilities for supervised meta-models — triple-barrier and
fractional differentiation, after López de Prado *Advances in Financial
Machine Learning* (2018), chapters 3 and 5.

These are tools the meta-labeler (``harness/meta.py``) uses to turn a
primary strategy's signals into a (features → outcome) supervised
problem. None of them are used by the primary strategy itself.

Anti-lookahead invariant for triple-barrier labels:
  Each event at time t_i gets:
    - t1[i]  = the actual exit time (PT/SL hit OR max-holding cap)
    - ret[i] = realised log return between t_i and t1[i]
    - bin[i] = +1 / 0 / -1 (PT hit / max-holding hit / SL hit)
  The label IS allowed to look forward IN TIME — the whole point of
  meta-labeling is to train on past outcomes. The constraint is that
  no feature used at training time may be derived from data beyond the
  observation time t_i. The label is the supervisor; the features are
  the input. Don't mix them up.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
@dataclass
class TripleBarrierConfig:
    """Triple-barrier parameters.

    pt_mult   : profit-take multiplier in units of the per-event volatility.
                e.g. pt_mult=2.0 with vol=0.01 → +2% PT barrier.
    sl_mult   : stop-loss multiplier (same units).
    max_holding_bars : vertical (time) barrier, in bars from event entry.
    side      : optional column "side" in events (+1 long, -1 short).
                When omitted, labels are direction-agnostic (binary +/-).
    """
    pt_mult: float = 2.0
    sl_mult: float = 1.0
    max_holding_bars: int = 24
    use_side: bool = True


def triple_barrier_labels(
    close: pd.Series,
    events: pd.DataFrame,
    vol: pd.Series,
    cfg: TripleBarrierConfig,
) -> pd.DataFrame:
    """Compute triple-barrier outcomes for a set of events.

    Arguments:
      close  : per-bar close prices, tz-aware UTC index, sorted.
      events : DataFrame indexed at event-entry timestamps. Optional
               column "side" with +1 long / -1 short signals.
      vol    : per-bar realised volatility (e.g. rolling std of log-returns).
               Used to scale barriers to current regime. Aligned to ``close``.
      cfg    : TripleBarrierConfig.

    Returns DataFrame indexed by event timestamp with columns:
      - t1   : exit timestamp (when ANY barrier was hit)
      - ret  : log return from entry to exit
      - bin  : +1 PT hit / 0 vertical / -1 SL hit (already signed by side)
      - side : if input had side, mirrored here for convenience

    Events whose computed t1 lies beyond the end of ``close`` are kept
    with t1 = close.index[-1] and bin = 0 (forced vertical) — these are
    "right-censored" and should be filtered by the caller if labels need
    to be terminal.
    """
    if events.empty:
        return events.assign(t1=pd.NaT, ret=np.nan, bin=0)

    has_side = cfg.use_side and "side" in events.columns
    # Align vol to event timestamps. Forward-fill so events between vol
    # observations inherit the most recent published volatility.
    vol_at_event = vol.reindex(events.index, method="ffill")

    out_rows = []
    close_vals = close.values
    close_idx = close.index
    n = len(close_idx)
    # Pre-build a fast lookup: timestamp → integer location.
    loc_of = {ts: i for i, ts in enumerate(close_idx)}

    for ts, ev in events.iterrows():
        if ts not in loc_of:
            # Event timestamp not on the close grid → snap to nearest prior bar.
            pos_arr = close_idx.get_indexer([ts], method="ffill")
            i0 = int(pos_arr[0]) if pos_arr[0] >= 0 else -1
            if i0 < 0:
                out_rows.append((ts, pd.NaT, np.nan, 0,
                                 int(ev["side"]) if has_side else 0))
                continue
        else:
            i0 = loc_of[ts]
        i_last = min(i0 + cfg.max_holding_bars, n - 1)
        if i_last <= i0:
            out_rows.append((ts, close_idx[i0], 0.0, 0,
                             int(ev["side"]) if has_side else 0))
            continue
        v = float(vol_at_event.loc[ts]) if ts in vol_at_event.index else np.nan
        if not np.isfinite(v) or v <= 0:
            # No vol estimate → vertical-only barrier.
            ret = float(np.log(close_vals[i_last] / close_vals[i0]))
            if has_side:
                ret *= int(ev["side"])
            out_rows.append((ts, close_idx[i_last], ret, 0,
                             int(ev["side"]) if has_side else 0))
            continue

        side = int(ev["side"]) if has_side else 1
        # Log-return barriers (so they compose multiplicatively).
        pt = cfg.pt_mult * v
        sl = -cfg.sl_mult * v
        path = np.log(close_vals[i0 + 1 : i_last + 1] / close_vals[i0])
        signed = side * path
        pt_hit_rel = np.argmax(signed >= pt) if (signed >= pt).any() else None
        sl_hit_rel = np.argmax(signed <= sl) if (signed <= sl).any() else None
        # argmax on bool returns first True index ONLY if there is one;
        # otherwise it returns 0. We guarded with .any() above.
        first = None
        if pt_hit_rel is not None and sl_hit_rel is not None:
            first = ("pt", pt_hit_rel) if pt_hit_rel <= sl_hit_rel else ("sl", sl_hit_rel)
        elif pt_hit_rel is not None:
            first = ("pt", pt_hit_rel)
        elif sl_hit_rel is not None:
            first = ("sl", sl_hit_rel)

        if first is None:
            exit_i = i_last
            label = 0
        else:
            exit_i = i0 + 1 + int(first[1])
            label = +1 if first[0] == "pt" else -1
        ret = float(np.log(close_vals[exit_i] / close_vals[i0]))
        if has_side:
            ret *= side
        out_rows.append((ts, close_idx[exit_i], ret, label,
                         side if has_side else 0))

    out = pd.DataFrame(
        out_rows, columns=["__ts", "t1", "ret", "bin", "side"],
    ).set_index("__ts")
    out.index.name = events.index.name
    if not has_side:
        out = out.drop(columns=["side"])
    return out


# --------------------------------------------------------------------------- #
def meta_labels(
    primary_signal: pd.Series,
    close: pd.Series,
    vol: pd.Series,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_holding_bars: int = 24,
) -> pd.DataFrame:
    """Convert a primary signal (∈ {-1, 0, +1}) into a meta-labeling
    supervised set: every non-zero signal is an event; the label is
    "did this trade hit PT before SL?" (binary 0/1) PLUS the realised
    log return.

    Used by ``harness.meta`` to train P(trade pays off | features).
    The output's index is the event timestamps; ``side`` records the
    primary signal's direction for downstream sign-correction.
    """
    sig = primary_signal.fillna(0.0)
    events_idx = sig[sig != 0].index
    if events_idx.empty:
        return pd.DataFrame(columns=["t1", "ret", "bin", "side", "y"])
    events = pd.DataFrame({"side": np.sign(sig.loc[events_idx]).astype(int)},
                          index=events_idx)
    cfg = TripleBarrierConfig(pt_mult=pt_mult, sl_mult=sl_mult,
                              max_holding_bars=max_holding_bars, use_side=True)
    tb = triple_barrier_labels(close, events, vol, cfg)
    # Meta-label = 1 if PT hit (profitable), 0 otherwise (SL or vertical).
    tb["y"] = (tb["bin"] == 1).astype(int)
    return tb


# --------------------------------------------------------------------------- #
def frac_diff(series: pd.Series, d: float,
              threshold: float = 1e-4) -> pd.Series:
    """Fractional differentiation (López de Prado §5).

    Computes the FFD (Fixed-width Frac-Diff) series: a stationary
    version of ``series`` that retains long memory by using a
    fractional differencing order ``d`` between 0 and 1. The window
    is truncated where coefficient weights fall below ``threshold``
    so the operation is O(n · W) with W typically << n.

    Returns a Series aligned to the input, with the first W bars NaN
    (warm-up). For typical financial series d=0.4–0.6 makes them
    stationary while preserving most of the level information that
    full differencing destroys.
    """
    if d <= 0 or d >= 1:
        # d=0 → return series unchanged; d=1 → first differences.
        if d == 0:
            return series.copy()
        if d == 1:
            return series.diff()
        raise ValueError(f"frac_diff order d must be in (0, 1), got {d}")

    # Build truncated weights w_k = -w_{k-1} · (d - k + 1) / k.
    w = [1.0]
    k = 1
    while True:
        w_k = -w[-1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
        k += 1
        if k > len(series):
            break
    weights = np.array(w[::-1])           # apply via convolution with reversed weights
    W = len(weights)

    vals = series.values.astype(float)
    out = np.full(len(vals), np.nan)
    for i in range(W - 1, len(vals)):
        window = vals[i - W + 1 : i + 1]
        if np.isnan(window).any():
            continue
        out[i] = float(np.dot(weights, window))
    return pd.Series(out, index=series.index, name=series.name)
