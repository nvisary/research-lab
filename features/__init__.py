"""Feature store — reusable feature computations with parquet caching.

A "feature" is a pure function ``(symbol, start, end, tf) -> pd.Series``
that returns a tz-aware UTC time series. The store handles caching to
``data/features/<feature_name>/<symbol>/<YYYY-MM>.parquet`` so heavy
features (rolling indicators, regime classifiers) are computed once
and reused across strategies / iters / meta-labeler training.

Public API:
  - ``list_features()``        — all registered feature names
  - ``feature_meta(name)``     — short description + dependencies
  - ``compute(name, symbol, start, end, tf, use_cache=True)``
                               — main entry point
  - ``coverage_table(name)``   — what's already cached on disk

To add a new feature:
  1. Write a function ``def my_feature(symbol, start, end, tf): -> pd.Series``
     in ``features/builtin.py`` (or a new module).
  2. Decorate with ``@register("my_feature", description=..., deps=...)``.
  3. It is now available via ``features.compute("my_feature", ...)``.

Anti-lookahead invariant: every feature MUST be computable using only
data with timestamp ≤ output_index_t. Tests in ``tests/test_features.py``
verify this for each registered feature via a tail-poison check.
"""
from __future__ import annotations

from features.registry import (
    compute,
    coverage_table,
    feature_meta,
    list_features,
    register,
    clear_cache,
)

# Import side-effect: registers all builtins.
from features import builtin as _builtin  # noqa: F401

__all__ = [
    "compute",
    "coverage_table",
    "feature_meta",
    "list_features",
    "register",
    "clear_cache",
]
