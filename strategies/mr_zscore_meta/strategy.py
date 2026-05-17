"""BTC mean-reversion with meta-labeling.

Primary signal:
  Long when close < rolling_mean − z_thresh · std (deep oversold).
  Short when close > rolling_mean + z_thresh · std (deep overbought).
  Exit when |z| < z_exit.

Meta-labeler:
  A LogisticRegression trained on triple-barrier outcomes of the
  primary signals over the train slice, using ``atr_pct_14``,
  ``realized_vol_30``, ``rsi_14``, ``trend_50_200`` as features.
  At decision time the primary signal is multiplied by
  P(trade pays off | features) — "scale" mode. The expectation is
  that the meta-labeler tames trades taken in regimes where MR
  doesn't work (trending high-vol periods).

This is a demo / template for how to attach a meta-labeler to any
existing strategy. See ``harness/meta.py:MetaSpec`` for all options.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.meta import MetaSpec

DESCRIPTION = (
    "Demo strategy: BTC z-score mean reversion gated by a meta-labeler "
    "(LogReg on ATR%, RV30, RSI14, trend) trained on triple-barrier outcomes."
)

DEFAULT_SYMBOLS = ["BTCUSDT"]
DEFAULT_TF = "1h"

DEFAULT_PARAMS = {
    "zwindow": 168,
    "z_thresh": 2.0,
    "z_exit": 0.0,
}

PARAM_SPACE = {
    "zwindow": (24, 720),
    "z_thresh": (1.0, 4.0),
    "z_exit": (-1.0, 1.0),
}

# --------------------------------------------------------------------------- #
# Meta-labeler: a secondary classifier on top of the primary signal.
# Exported as a module-level constant; the harness picks this up
# automatically and trains/applies it during run_split. See README §4.
META_LABELER = MetaSpec(
    features=["atr_pct_14", "realized_vol_30", "rsi_14", "trend_50_200"],
    classifier="logreg",
    mode="scale",                        # scale primary signal by P(positive)
    threshold=0.55,                      # used only in "gate" mode
    pt_mult=2.0, sl_mult=1.0,
    max_holding_bars=24,
    vol_feature="realized_vol_30",
    min_train_events=80,
)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    sym = DEFAULT_SYMBOLS[0]
    if sym not in data or data[sym].empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    df = data[sym]
    close = df["close"]
    zwin = int(params["zwindow"])
    zt = float(params["z_thresh"])
    ze = float(params["z_exit"])

    mu = close.rolling(zwin, min_periods=zwin).mean()
    sd = close.rolling(zwin, min_periods=zwin).std(ddof=1)
    z = (close - mu) / sd.where(sd > 0)

    long_sig = (z < -zt).astype(float)
    short_sig = (z > zt).astype(float) * -1.0

    # Combine and forward-fill until exit threshold crossed.
    raw = long_sig + short_sig
    pos = pd.Series(0.0, index=close.index)
    state = 0.0
    for i, v in enumerate(raw.values):
        z_i = z.iloc[i] if i < len(z) else np.nan
        if state == 0.0 and v != 0.0:
            state = v
        elif state != 0.0 and not np.isnan(z_i) and abs(z_i) < ze:
            state = 0.0
        pos.iloc[i] = state

    # Critical: shift(1) so signal at bar t uses ≤ t-1 data only.
    pos = pos.shift(1).fillna(0.0)
    return pd.DataFrame({
        "timestamp": close.index,
        "symbol": sym,
        "position": pos.values,
    })
