"""Return autocorrelation & variance-ratio (train-only).

Reference tool: the single cheapest read on "is this series mean-reverting or
trending, and at what horizon?" — which decides whether an MR or a momentum
entry has any structural basis before you ever touch strategy.py.
"""
from __future__ import annotations

import numpy as np

from harness.research.contract import ResearchResult, ToolMeta, research_tool


def _variance_ratio(rets: np.ndarray, q: int) -> float:
    """VR(q) = Var(q-bar return) / (q · Var(1-bar return)).

    <1 ⇒ mean-reverting at horizon q; ≈1 ⇒ random walk; >1 ⇒ trending.
    """
    n = len(rets)
    if n < q * 2:
        return float("nan")
    mu = rets.mean()
    var1 = np.sum((rets - mu) ** 2) / (n - 1)
    if var1 == 0:
        return float("nan")
    agg = np.convolve(rets, np.ones(q), mode="valid")  # rolling q-sum
    varq = np.sum((agg - q * mu) ** 2) / (len(agg) - 1)
    return float(varq / (q * var1))


@research_tool(ToolMeta(
    name="return_autocorr",
    question="Are per-bar returns mean-reverting or trending — by lag-k "
             "autocorrelation and the variance ratio?",
    params={
        "symbol": "symbol to analyze (default: strategy's first symbol)",
        "max_lag": "highest autocorrelation lag to report (default 10)",
        "vr_horizons": "comma-separated variance-ratio horizons (default 2,5,10)",
    },
    returns=["lag1_autocorr", "vr_min", "vr_min_horizon", "regime_hint"],
    tags=["mean-reversion", "momentum", "signal", "autocorrelation"],
))
def return_autocorr(data, symbol: str = "", max_lag: int = 10,
                    vr_horizons: str = "2,5,10") -> ResearchResult:
    sym = symbol or None
    name = data._resolve(sym)
    r = data.returns(sym).to_numpy()
    if len(r) < max_lag * 20:
        return ResearchResult(
            summary=f"[{name}] only {len(r)} bars — too few for lag {max_lag}",
            metrics={"n_bars": int(len(r))},
        )

    acf = {}
    for k in range(1, max_lag + 1):
        a, b = r[:-k], r[k:]
        acf[k] = round(float(np.corrcoef(a, b)[0, 1]), 4) if a.std() and b.std() else None

    horizons = [int(x) for x in str(vr_horizons).split(",") if x.strip()]
    vr = {q: round(_variance_ratio(r, q), 4) for q in horizons}
    finite_vr = {q: v for q, v in vr.items() if v is not None and np.isfinite(v)}
    vr_min_h = min(finite_vr, key=finite_vr.get) if finite_vr else None
    vr_min = finite_vr.get(vr_min_h) if vr_min_h is not None else None

    lag1 = acf.get(1)
    if lag1 is not None and lag1 < -0.03:
        hint = "mean-reverting (negative lag-1 autocorr)"
    elif lag1 is not None and lag1 > 0.03:
        hint = "trending/momentum (positive lag-1 autocorr)"
    elif vr_min is not None and vr_min < 0.9:
        hint = f"mean-reverting at horizon {vr_min_h} (VR={vr_min})"
    elif vr_min is not None and max(finite_vr.values()) > 1.1:
        hint = "trending at longer horizons (VR>1)"
    else:
        hint = "approximately random-walk (no strong structure)"

    return ResearchResult(
        summary=f"[{name}] {hint}; lag1 acf={lag1}, VR={vr}",
        metrics={
            "lag1_autocorr": lag1,
            "vr_min": vr_min,
            "vr_min_horizon": vr_min_h,
            "regime_hint": hint,
            "n_bars": int(len(r)),
        },
        series={"acf": acf, "variance_ratio": vr},
    )
