"""researchlab quantitative-EDA layer.

A small registry of *research tools* that measure properties of the train-only
data (volatility regimes, funding/return relationships, autocorrelation, …) so
that strategy hypotheses are grounded in measurement before any backtest is run.

Entry points
------------
  * ``runner.explore --list``                  → see every registered tool
  * ``runner.explore <strategy> --tool NAME``  → run one tool on the train slice

Public API
----------
  * ``ToolMeta``, ``ResearchResult``, ``research_tool`` — the contract
  * ``TrainData``, ``load_train_data`` — the train-only data handle
  * ``REGISTRY`` — name → ResearchTool

Importing this package registers all vetted tools (``.lib``).
"""
from __future__ import annotations

from .contract import REGISTRY, ResearchResult, ResearchTool, ToolMeta, research_tool
from .loader import TrainData, load_train_data, train_window

# Side-effect import: registers the vetted tool library.
from . import lib  # noqa: E402,F401

__all__ = [
    "REGISTRY", "ResearchResult", "ResearchTool", "ToolMeta", "research_tool",
    "TrainData", "load_train_data", "train_window", "lib",
]
