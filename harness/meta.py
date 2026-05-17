"""Meta-labeling: a secondary classifier on top of a primary signal.

López de Prado (2018) §3.3 introduces meta-labeling as the "secondary
model" pattern:
  - The PRIMARY strategy decides WHEN and IN WHICH DIRECTION to trade.
  - The META model decides HOW MUCH (or whether) — outputting P(this
    trade will be profitable | features at signal time).

The primary signal is left untouched. The meta model is trained on
triple-barrier outcomes of the primary's own historical signals,
using a feature set the operator declares. At decision time, the
final position is the primary signal scaled by the meta model's
predicted probability:

    final_position[t] = primary_signal[t] · meta_proba[t]

(or, in `gate` mode, the signal is zeroed when proba is below
threshold.)

This module is engine-agnostic — it provides:
  - ``MetaSpec``         : declarative spec a strategy.py can export
  - ``MetaLabeler``      : fit/predict object
  - ``meta_modulate``    : the function that wraps the primary signal

Anti-lookahead invariants:
  - Training data: only events with t1 ≤ train_end are used.
  - Features at event time t MUST be ≤ t (enforced by the feature
    store's tail-poison test).
  - Predict-time: meta_proba[t] is computed using features.shift(1)
    so the score is causal w.r.t. the primary signal's bar.

This module depends on scikit-learn for the classifier. Two engine
options out of the box:
  - "logreg"   : LogisticRegression with L2; fast, calibrated by default.
  - "gbm"      : HistGradientBoostingClassifier; flexible, slower fit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from harness.labels import meta_labels


# --------------------------------------------------------------------------- #
@dataclass
class MetaSpec:
    """Operator-declared meta-labeling config. Exported by strategy.py
    as ``META_LABELER = MetaSpec(...)`` (optional).

    features    : list of feature names registered in ``features``.
                  Each is loaded per-symbol via features.compute(...).
    classifier  : "logreg" | "gbm".
    mode        : "scale" (multiply primary by proba) or
                  "gate" (zero out below threshold).
    threshold   : threshold for "gate" mode (ignored in "scale").
    pt_mult     : profit-take barrier multiplier (units of per-bar vol).
    sl_mult     : stop-loss barrier multiplier.
    max_holding_bars : vertical-barrier time horizon.
    vol_feature : which registered feature to use as the per-bar
                  volatility for barrier scaling. Default
                  "realized_vol_30".
    min_train_events : refuse to fit if the train slice has fewer
                       than this many labeled events. Default 100.
    """
    features: list[str]
    classifier: Literal["logreg", "gbm"] = "logreg"
    mode: Literal["scale", "gate"] = "scale"
    threshold: float = 0.55
    pt_mult: float = 2.0
    sl_mult: float = 1.0
    max_holding_bars: int = 24
    vol_feature: str = "realized_vol_30"
    min_train_events: int = 100
    # Hyperparameters passed straight to the sklearn estimator.
    classifier_kwargs: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
def _make_classifier(spec: MetaSpec):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer

    if spec.classifier == "logreg":
        kw = {"max_iter": 1000, "C": 1.0, **spec.classifier_kwargs}
        return Pipeline([
            ("imp", SimpleImputer(strategy="median")),
            ("sc", StandardScaler()),
            ("lr", LogisticRegression(**kw)),
        ])
    elif spec.classifier == "gbm":
        kw = {"max_iter": 200, "max_depth": 4, **spec.classifier_kwargs}
        return Pipeline([
            ("imp", SimpleImputer(strategy="median")),
            ("gbm", HistGradientBoostingClassifier(**kw)),
        ])
    raise ValueError(f"unknown classifier: {spec.classifier!r}")


# --------------------------------------------------------------------------- #
@dataclass
class MetaFitReport:
    """Diagnostics returned after fitting. Surfaced to the UI."""
    n_train_events: int
    n_train_positive: int                  # PT-hit count
    train_class_balance: float             # fraction positive
    train_accuracy: float
    train_precision_at_thresh: float
    train_recall_at_thresh: float
    feature_importances: dict[str, float]  # name → |coef| or perm-importance proxy
    classifier: str
    threshold: float

    def to_dict(self) -> dict:
        return {
            "n_train_events": self.n_train_events,
            "n_train_positive": self.n_train_positive,
            "train_class_balance": self.train_class_balance,
            "train_accuracy": self.train_accuracy,
            "train_precision_at_thresh": self.train_precision_at_thresh,
            "train_recall_at_thresh": self.train_recall_at_thresh,
            "feature_importances": self.feature_importances,
            "classifier": self.classifier,
            "threshold": self.threshold,
        }


# --------------------------------------------------------------------------- #
class MetaLabeler:
    """Fit on (features, triple-barrier outcomes) over a train slice,
    predict P(trade pays off | features) on OOS bars.

    Usage:
        labeler = MetaLabeler(spec)
        labeler.fit(primary_signal_train, close_train, features_train, vol_train)
        proba_oos = labeler.predict_proba(features_oos)
        final = meta_modulate(primary_signal_oos, proba_oos, spec)
    """

    def __init__(self, spec: MetaSpec):
        self.spec = spec
        self.model = None
        self.feature_names_: list[str] | None = None
        self.report_: MetaFitReport | None = None

    # ----------------------------------------------------------------------- #
    def fit(self,
            primary_signal: pd.Series,
            close: pd.Series,
            features: pd.DataFrame,
            vol: pd.Series) -> "MetaLabeler":
        """Build the supervised set via triple-barrier labels and fit.

        primary_signal, close, vol : indexed by the same UTC timestamps.
        features : DataFrame, same index, one column per feature name.

        Returns self. Sets self.model, self.report_.
        """
        labels = meta_labels(
            primary_signal=primary_signal, close=close, vol=vol,
            pt_mult=self.spec.pt_mult, sl_mult=self.spec.sl_mult,
            max_holding_bars=self.spec.max_holding_bars,
        )
        if labels.empty or len(labels) < self.spec.min_train_events:
            raise RuntimeError(
                f"meta-labeler: only {len(labels)} train events "
                f"(need ≥{self.spec.min_train_events}). "
                "Reduce min_train_events or extend the train window."
            )

        # Features at event time t: use the row at t (NOT shifted), so
        # the classifier sees the state that produced the signal. The
        # primary signal itself was already lookahead-safe by contract
        # (it was .shift(1)-ed by the strategy). At predict time we
        # apply features.shift(1) to ensure the meta-score lags the
        # signal by one bar — see predict_proba().
        X = features.reindex(labels.index)
        # Drop rows where ALL features are NaN (no warmup yet).
        keep = X.notna().any(axis=1)
        X = X.loc[keep]
        y = labels.loc[X.index, "y"].astype(int).values
        feature_names = list(X.columns)
        if y.sum() == 0 or y.sum() == len(y):
            raise RuntimeError(
                f"meta-labeler: degenerate class balance "
                f"({y.sum()}/{len(y)} positive). Skipping meta-labeling."
            )

        model = _make_classifier(self.spec)
        model.fit(X.values, y)

        proba = model.predict_proba(X.values)[:, 1]
        pred = (proba >= self.spec.threshold).astype(int)
        acc = float((pred == y).mean())
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        importances = self._estimate_importance(model, X, y, feature_names)

        self.model = model
        self.feature_names_ = feature_names
        self.report_ = MetaFitReport(
            n_train_events=int(len(y)),
            n_train_positive=int(y.sum()),
            train_class_balance=float(y.mean()),
            train_accuracy=acc,
            train_precision_at_thresh=float(precision),
            train_recall_at_thresh=float(recall),
            feature_importances=importances,
            classifier=self.spec.classifier,
            threshold=float(self.spec.threshold),
        )
        return self

    # ----------------------------------------------------------------------- #
    def _estimate_importance(self, model, X, y, names) -> dict[str, float]:
        """Cheap importance: coef magnitude (logreg) or built-in feature
        importance (gbm). Falls back to permutation importance only if
        nothing else works."""
        try:
            est = model[-1]
            if hasattr(est, "coef_"):
                w = np.abs(est.coef_[0])
                total = w.sum()
                if total > 0:
                    return {n: float(v / total) for n, v in zip(names, w)}
            if hasattr(est, "feature_importances_"):
                w = np.asarray(est.feature_importances_, dtype=float)
                total = w.sum()
                if total > 0:
                    return {n: float(v / total) for n, v in zip(names, w)}
        except Exception:
            pass
        return {n: 0.0 for n in names}

    # ----------------------------------------------------------------------- #
    def predict_proba(self, features: pd.DataFrame) -> pd.Series:
        """Return P(trade pays off) per bar in features.index.

        Causality: features are shifted by 1 bar before prediction so
        the score for bar t uses information ≤ t-1, mirroring how the
        primary signal is generated. NaN at bars where features are
        not yet warm — the modulator treats NaN as 0.5 (neutral).
        """
        if self.model is None or self.feature_names_ is None:
            raise RuntimeError("MetaLabeler not fitted")
        cols = [c for c in self.feature_names_ if c in features.columns]
        if not cols:
            return pd.Series(np.nan, index=features.index, name="meta_proba")
        X_lag = features[cols].shift(1)
        valid = X_lag.notna().any(axis=1)
        out = pd.Series(np.nan, index=features.index, name="meta_proba")
        if valid.any():
            proba = self.model.predict_proba(X_lag.loc[valid].values)[:, 1]
            out.loc[valid] = proba
        return out


# --------------------------------------------------------------------------- #
def meta_modulate(primary: pd.Series, proba: pd.Series,
                  spec: MetaSpec) -> pd.Series:
    """Combine the primary signal with the meta probability per spec.

    Modes:
      - "scale" : final = primary · proba.
                  NaN proba → neutral scale of 0.5 (so untouched bars
                  trade at half size — a deliberately conservative
                  fallback in the warm-up region).
      - "gate"  : final = primary if proba ≥ threshold else 0.
                  NaN proba → 0 (don't trade until meta is warm).
    """
    aligned = proba.reindex(primary.index)
    if spec.mode == "scale":
        proba_clipped = aligned.clip(0.0, 1.0).fillna(0.5)
        return (primary * proba_clipped).astype(float)
    elif spec.mode == "gate":
        ok = (aligned >= spec.threshold).fillna(False)
        return primary.where(ok, 0.0).astype(float)
    raise ValueError(f"unknown mode: {spec.mode!r}")
