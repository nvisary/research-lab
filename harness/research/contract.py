"""The research-tool contract — one shape every EDA tool shares.

A *research tool* answers one quantitative question about the data
(volatility regimes, funding/return correlation, return autocorrelation, …)
and returns a small, structured, comparable result. It NEVER edits a
strategy, never runs a backtest, never sees OOS or holdout — it only reads
the train-only slice handed to it as ``TrainData`` (see ``loader.py``).

Why a fixed shape
-----------------
So that an agent can:
  * discover what exists (``runner.explore --list`` reads the REGISTRY),
  * understand a tool without reading its body (``ToolMeta``),
  * compare results across iterations (``ResearchResult.metrics`` are scalars),
  * and contribute a new tool that every future agent can reuse — by writing a
    function with this same signature and decorating it with ``@research_tool``.

The signature every tool obeys::

    @research_tool(ToolMeta(name="...", question="...", ...))
    def my_tool(data: TrainData, lookback: int = 30) -> ResearchResult:
        ...
        return ResearchResult(summary="one line", metrics={...})

Tools take ``data`` first (positional) and any number of keyword params with
plain-typed defaults (int/float/bool/str) — the CLI coerces ``--param k=v``
strings against those defaults, so defaults double as the param type spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolMeta:
    """Self-description of a research tool — what an agent reads to choose it.

    Keep ``question`` to one line: it is the human-facing "what does this
    measure". ``params``/``returns`` are name → one-line-description maps so the
    agent knows the knobs and the output keys without opening the source.
    """
    name: str                                   # kebab/snake id, unique in REGISTRY
    question: str                               # one-line "what does this measure?"
    params: dict[str, str] = field(default_factory=dict)   # name -> description
    returns: list[str] = field(default_factory=list)       # metric keys it sets
    tags: list[str] = field(default_factory=list)          # for grouping/search

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "question": self.question,
            "params": self.params,
            "returns": self.returns,
            "tags": self.tags,
        }


@dataclass
class ResearchResult:
    """The structured payload every tool returns.

    ``summary``  — one line stating the main finding (this is what the agent
                   quotes in program.md).
    ``metrics``  — scalar numbers, comparable across runs/iterations. This is
                   the part you reason over when forming a hypothesis.
    ``series``   — optional named arrays (e.g. per-regime curves, acf lags) for
                   when a scalar isn't enough. Kept out of the headline.
    """
    summary: str
    metrics: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "metrics": self.metrics, "series": self.series}


@dataclass
class ResearchTool:
    meta: ToolMeta
    fn: Callable


# Global registry — populated by the @research_tool decorator at import time.
# Vetted tools (harness/research/lib/*) register when `harness.research` is
# imported; scratch tools (strategies/<name>/research/*) register when
# runner.explore imports that strategy's research dir.
REGISTRY: dict[str, ResearchTool] = {}


def research_tool(meta: ToolMeta) -> Callable:
    """Decorator: register ``fn`` as a discoverable research tool under ``meta.name``.

    Re-importing the same module simply re-registers (last definition wins),
    so editing a scratch tool and re-running ``runner.explore`` picks up the
    new version without a stale-cache surprise.
    """
    def deco(fn: Callable) -> Callable:
        REGISTRY[meta.name] = ResearchTool(meta=meta, fn=fn)
        fn._research_meta = meta  # introspectable on the function too
        return fn
    return deco
