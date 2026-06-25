"""Funding-rate vs forward-return relationship (train-only).

Reference tool: replaces the old ad-hoc analyze_funding.py root script with a
registered, train-only, reusable version. Answers "does funding carry any
predictive sign for the next funding interval?" — the basis for any
funding-tilt or carry hypothesis.
"""
from __future__ import annotations

import numpy as np

from harness.research.contract import ResearchResult, ToolMeta, research_tool


@research_tool(ToolMeta(
    name="funding_corr",
    question="Does the funding rate correlate with the forward return over the "
             "next funding interval, and is the sign-conditional return asymmetric?",
    params={
        "symbol": "symbol to analyze (default: strategy's first symbol)",
        "resample": "bar size to align price to funding cadence (default 8h)",
    },
    returns=[
        "pearson_corr", "mean_fwd_ret_pos_funding", "mean_fwd_ret_neg_funding",
        "n_obs",
    ],
    tags=["funding", "carry", "signal", "alts"],
))
def funding_corr(data, symbol: str = "", resample: str = "8h") -> ResearchResult:
    sym = symbol or None
    name = data._resolve(sym)
    fund = data.funding(sym)
    if fund.empty:
        return ResearchResult(
            summary=f"[{name}] no funding data present for the train window",
            metrics={"n_obs": 0},
        )

    close = data.ohlcv(sym)["close"].resample(resample).last().dropna()
    fwd = close.pct_change().shift(-1)  # forward return over the next interval

    rate = fund["rate"].reindex(close.index, method="ffill")
    df = (fwd.to_frame("fwd").join(rate.rename("rate"))).dropna()
    if len(df) < 30:
        return ResearchResult(
            summary=f"[{name}] only {len(df)} aligned funding/price points — too few",
            metrics={"n_obs": int(len(df))},
        )

    corr = float(np.corrcoef(df["rate"], df["fwd"])[0, 1]) if df["rate"].std() > 0 else float("nan")
    pos = df[df["rate"] > 0]["fwd"]
    neg = df[df["rate"] < 0]["fwd"]
    mean_pos = float(pos.mean()) if len(pos) else float("nan")
    mean_neg = float(neg.mean()) if len(neg) else float("nan")

    if np.isfinite(corr) and abs(corr) > 0.05:
        direction = "high funding → lower" if corr < 0 else "high funding → higher"
        verdict = f"funding has sign ({direction} forward return, r={corr:+.3f})"
    else:
        verdict = f"funding shows no usable forward-return sign (r={corr:+.3f})"

    return ResearchResult(
        summary=f"[{name}] {verdict}; "
                f"E[fwd|fund>0]={mean_pos:+.5f} vs E[fwd|fund<0]={mean_neg:+.5f}",
        metrics={
            "pearson_corr": round(corr, 4) if np.isfinite(corr) else None,
            "mean_fwd_ret_pos_funding": round(mean_pos, 6) if np.isfinite(mean_pos) else None,
            "mean_fwd_ret_neg_funding": round(mean_neg, 6) if np.isfinite(mean_neg) else None,
            "n_obs": int(len(df)),
        },
    )
