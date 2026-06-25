"""Vetted research-tool library — importing this registers every tool.

Each module decorates its function(s) with ``@research_tool`` from
``harness.research.contract``; importing the module runs the decorator and
populates the global REGISTRY. Add a new vetted tool by creating a module here
and importing it below (or rely on the auto-scan in this package's import).

These tools are READ-ONLY for the iterating agent: they are promoted here from
strategy-local scratch (``strategies/<name>/research/``) by the operator after
review. The iterating agent calls them but does not edit them.
"""
from __future__ import annotations

from . import autocorr, funding, volatility  # noqa: F401  (registration side-effect)

__all__ = ["autocorr", "funding", "volatility"]
