# mr_zscore — TS mean-reversion via per-asset z-score

## Baseline

Per symbol on 1h bars, compute rolling z-score of close over 168h
(1 week). Enter long when z drops below -2.0, exit when z returns
to >= 0.0. Symmetric short above +2.0.

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1h"
zwindow         = 168
z_thresh        = 2.0
z_exit          = 0.0
long_only       = 0
```

## Hypothesis

When a single asset's price is at a statistical extreme relative
to its own recent history (|z| > 2σ), the next move is more likely
to be mean-reverting than continuing. Each asset evaluated
independently — basket diversification comes from uncorrelated
per-asset extremes, not from cross-sectional ranking.

## Why this slot in the 4-strategy quadrant

Time-series MR. Should fire on idiosyncratic extremes within an
asset, regardless of whether other basket members are at their own
extremes. Naturally orthogonal to mr_xsection (which fires only on
RELATIVE extremes within the basket).

## Open questions

- z_thresh sweep — 1.5 / 2.0 / 2.5
- zwindow sweep — 1d (24) / 3d (72) / 1w (168) / 2w (336)
- z_exit asymmetry: tighter exit for shorts (funding drag)?
- HTF trend gate: only long when 1d > 1d_EMA?
- Cost-aware skip when |z| only marginally above threshold
