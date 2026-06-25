"""Volatility-regime characterization of the data (train-only).

Reference tool: shows the *shape* of behavior conditional on volatility regime,
so a hypothesis like "mean-reversion concentrates in high-vol bars → add a vol
filter" is grounded in a measurement, not a guess.
"""
from __future__ import annotations

import numpy as np

from harness.research.contract import ResearchResult, ToolMeta, research_tool


@research_tool(ToolMeta(
    name="vol_regime_split",
    question="How do return magnitude and lag-1 autocorrelation differ across "
             "low/mid/high realized-volatility regimes?",
    params={
        "symbol": "symbol to analyze (default: strategy's first symbol)",
        "vol_window": "bars in the rolling realized-vol estimate (default 24)",
        "n_regimes": "number of equal-frequency vol regimes / quantile bins (default 3)",
    },
    returns=[
        "lag1_autocorr_high", "lag1_autocorr_low", "autocorr_spread_high_minus_low",
        "mean_abs_ret_high", "mean_abs_ret_low",
    ],
    tags=["volatility", "regime", "mean-reversion", "filter"],
))
def vol_regime_split(data, symbol: str = "", vol_window: int = 24,
                     n_regimes: int = 3) -> ResearchResult:
    sym = symbol or None
    rets = data.returns(sym)
    if len(rets) < vol_window * n_regimes * 5:
        return ResearchResult(
            summary=f"insufficient data ({len(rets)} bars) for vol_window={vol_window}",
            metrics={"n_bars": int(len(rets))},
        )

    vol = rets.rolling(vol_window).std()
    df = rets.to_frame("ret")
    df["vol"] = vol
    df["next"] = df["ret"].shift(-1)
    df = df.dropna()

    # Equal-frequency regimes by volatility quantile.
    labels = list(range(n_regimes))
    df["regime"] = (df["vol"].rank(pct=True) * n_regimes).clip(upper=n_regimes - 1e-9).astype(int)

    per_regime = {}
    for r in labels:
        g = df[df["regime"] == r]
        if len(g) < 10:
            continue
        ac = float(np.corrcoef(g["ret"], g["next"])[0, 1]) if g["ret"].std() > 0 else float("nan")
        per_regime[r] = {
            "share_of_time": round(len(g) / len(df), 4),
            "mean_abs_ret": round(float(g["ret"].abs().mean()), 6),
            "mean_ret": round(float(g["ret"].mean()), 6),
            "lag1_autocorr": round(ac, 4),
            "vol_band": [round(float(g["vol"].min()), 6), round(float(g["vol"].max()), 6)],
        }

    lo, hi = labels[0], labels[-1]
    ac_hi = per_regime.get(hi, {}).get("lag1_autocorr", float("nan"))
    ac_lo = per_regime.get(lo, {}).get("lag1_autocorr", float("nan"))
    spread = ac_hi - ac_lo if np.isfinite(ac_hi) and np.isfinite(ac_lo) else float("nan")

    if np.isfinite(spread) and spread < -0.03:
        verdict = (f"high-vol regime is MORE mean-reverting (lag1 acf {ac_hi:+.3f} vs "
                   f"{ac_lo:+.3f} in low-vol) → a high-vol filter may help an MR entry")
    elif np.isfinite(spread) and spread > 0.03:
        verdict = (f"high-vol regime is MORE trending (lag1 acf {ac_hi:+.3f} vs "
                   f"{ac_lo:+.3f} in low-vol) → vol may favor momentum, not MR")
    else:
        verdict = (f"lag1 autocorr similar across vol regimes "
                   f"({ac_lo:+.3f}→{ac_hi:+.3f}); a vol regime filter is unlikely to help")

    return ResearchResult(
        summary=f"[{data._resolve(sym)}] {verdict}",
        metrics={
            "lag1_autocorr_high": ac_hi,
            "lag1_autocorr_low": ac_lo,
            "autocorr_spread_high_minus_low": round(spread, 4) if np.isfinite(spread) else None,
            "mean_abs_ret_high": per_regime.get(hi, {}).get("mean_abs_ret"),
            "mean_abs_ret_low": per_regime.get(lo, {}).get("mean_abs_ret"),
            "n_bars": int(len(df)),
        },
        series={"per_regime": per_regime},
    )
