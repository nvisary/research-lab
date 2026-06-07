"""Parameter-optimization primitives: sampling, train-only scoring, plateau clustering.

This module is the pure, I/O-free core behind ``runner.optimize``. It turns a
strategy's ``PARAM_SPACE`` (the hint ranges every strategy already exports) into
a set of candidate parameter dicts, scores each on TRAIN-ONLY inner folds, and
clusters the survivors into *plateaus* — connected regions of parameter space
where the strategy is robustly good, as opposed to isolated single-point spikes
that are almost always overfit artifacts.

Design notes
------------
- **Train-only by construction.** Nothing here touches the real OOS or holdout.
  The fold metrics fed to ``train_only_score`` come from walk-forward windows run
  entirely inside the train region (see ``runner.optimize`` for the boundary).
- **Plateau, not peak.** ``cluster_plateaus`` exists because the single
  highest-scoring config is the *most* likely to be a fluke. A wide plateau of
  near-equal configs is the robust signal. We return plateau representatives plus
  the span of each region so the agent can pick a center and know how forgiving it
  is.
- **Deterministic.** Quasi-random sampling is seeded; same (space, seed, n)
  always yields the same candidates. Reproducibility is a hard requirement of the
  research loop.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- #
# Param-space typing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParamSpec:
    """A single tunable parameter resolved from PARAM_SPACE + DEFAULT_PARAMS.

    ``kind`` is "int" or "float". Integer params are sampled and rounded to
    whole numbers; the grid enumerates every integer in range when the range
    is small. ``lo``/``hi`` are inclusive bounds (the PARAM_SPACE convention).
    """
    name: str
    lo: float
    hi: float
    kind: str  # "int" | "float"

    @property
    def n_distinct_ints(self) -> int:
        return int(round(self.hi)) - int(round(self.lo)) + 1

    def clamp(self, x: float) -> float:
        x = min(max(x, self.lo), self.hi)
        return float(round(x)) if self.kind == "int" else float(x)


def infer_specs(param_space: dict, defaults: dict | None = None,
                only: list[str] | None = None) -> list[ParamSpec]:
    """Resolve PARAM_SPACE entries into typed ParamSpecs.

    A param is treated as integer when BOTH bounds are whole numbers and the
    default (if present) is integer-valued — this matches the repo convention
    where ``cci_period: (10, 30)`` is integer but ``funding_threshold:
    (0.0001, 0.001)`` is float. ``only`` restricts to a subset of names (the
    ``--params`` CLI flag); unknown names raise.
    """
    defaults = defaults or {}
    names = list(param_space.keys()) if only is None else list(only)
    specs: list[ParamSpec] = []
    for name in names:
        if name not in param_space:
            raise KeyError(f"param {name!r} not in PARAM_SPACE "
                           f"(have: {sorted(param_space)})")
        bounds = param_space[name]
        if not (isinstance(bounds, (tuple, list)) and len(bounds) == 2):
            raise ValueError(f"PARAM_SPACE[{name!r}] must be a (lo, hi) pair, "
                             f"got {bounds!r}")
        lo, hi = float(bounds[0]), float(bounds[1])
        if hi < lo:
            lo, hi = hi, lo
        bounds_int = float(lo).is_integer() and float(hi).is_integer()
        dflt = defaults.get(name)
        default_int = dflt is None or (isinstance(dflt, (int, float))
                                        and float(dflt).is_integer())
        kind = "int" if (bounds_int and default_int) else "float"
        specs.append(ParamSpec(name=name, lo=lo, hi=hi, kind=kind))
    return specs


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #
def _dedup(cands: list[dict]) -> list[dict]:
    """Stable dedup of candidate dicts (integer rounding collides Sobol points)."""
    seen: set = set()
    out: list[dict] = []
    for c in cands:
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def sample_grid(specs: list[ParamSpec], resolution: int = 12) -> list[dict]:
    """Full Cartesian grid. Per-axis points = ``resolution`` for float params,
    or every integer in range (capped at ``resolution`` via linspace) for int
    params. Intended for 1-2 dimensions where the grid stays small and the
    plateau is directly visualizable.
    """
    axes: list[list[float]] = []
    for sp in specs:
        if sp.kind == "int" and sp.n_distinct_ints <= resolution:
            vals = [float(v) for v in range(int(round(sp.lo)), int(round(sp.hi)) + 1)]
        else:
            raw = np.linspace(sp.lo, sp.hi, resolution)
            vals = sorted({sp.clamp(v) for v in raw})
        axes.append(vals)
    out = [dict(zip((s.name for s in specs), combo))
           for combo in itertools.product(*axes)]
    return _dedup(out)


def sample_quasi(specs: list[ParamSpec], n: int = 256, seed: int = 0) -> list[dict]:
    """Low-discrepancy (Sobol) sample of the box, deterministic given seed.

    Sobol gives much more even space coverage than i.i.d. random at the same
    budget — the right default for 3+ dimensions where a full grid explodes
    combinatorially. Falls back to a seeded uniform draw if scipy's qmc is
    unavailable. Integer params are rounded (and the result deduped).
    """
    d = len(specs)
    try:
        from scipy.stats import qmc
        sampler = qmc.Sobol(d=d, scramble=True, seed=seed)
        # Sobol is balanced at powers of two; round n up, then trim.
        m = max(1, math.ceil(math.log2(max(n, 2))))
        unit = sampler.random_base2(m)[:n]
    except Exception:
        rng = np.random.default_rng(seed)
        unit = rng.random((n, d))
    out: list[dict] = []
    for row in unit:
        cand = {}
        for sp, u in zip(specs, row):
            cand[sp.name] = sp.clamp(sp.lo + float(u) * (sp.hi - sp.lo))
        out.append(cand)
    return _dedup(out)


def build_candidates(specs: list[ParamSpec], method: str = "auto",
                     grid_resolution: int = 12, n_quasi: int = 256,
                     seed: int = 0) -> tuple[list[dict], str]:
    """Pick a sampling method and return (candidates, method_used).

    ``method="auto"``: grid for <=2 params (plateau is visualizable, coverage is
    complete), Sobol quasi-random for 3+ (a grid would be combinatorially huge).
    """
    if not specs:
        raise ValueError("no params to optimize")
    chosen = method
    if method == "auto":
        chosen = "grid" if len(specs) <= 2 else "quasi"
    if chosen == "grid":
        return sample_grid(specs, resolution=grid_resolution), "grid"
    if chosen == "quasi":
        return sample_quasi(specs, n=n_quasi, seed=seed), "quasi"
    raise ValueError(f"unknown sampling method {method!r}")


# --------------------------------------------------------------------------- #
# Train-only scoring
# --------------------------------------------------------------------------- #
@dataclass
class FoldScore:
    """Result of scoring one candidate across its inner train-only folds."""
    score: float
    mean_sharpe: float
    std_sharpe: float
    median_sharpe: float
    mean_n_trades: float
    mean_tip: float
    n_folds: int
    eligible: bool
    reason: str = ""


def train_only_score(fold_sharpes: list[float], fold_n_trades: list[float],
                     fold_tip: list[float], stability_penalty: float = 0.5,
                     min_trades_per_fold: float = 10.0,
                     low_trades_penalty: float = 0.5,
                     min_tip: float = 20.0,
                     tip_penalty: float = 1.0) -> FoldScore:
    """Robustness-weighted train-only score: ``mean(SR) - k*std(SR)`` with
    GRADED penalties mirroring ``harness.metrics.composite_score``.

    Operates on the INNER walk-forward folds inside the train region — never the
    real OOS. The score is::

        mean(SR) - stability_penalty*std(SR)
                 - low_trades_penalty*(1 - sqrt(mean_nt / min_trades_per_fold))   [if mean_nt < floor]
                 - tip_penalty*(1 - mean_tip / min_tip)                            [if mean_tip < floor]

    The penalties are GRADED, not hard gates — exactly as the real composite
    treats them. This is deliberate: a hard ``tip < 20% → -inf`` cutoff would
    nuke entire legitimate sparse strategy families (e.g. pivot-reversion sits
    in the market <10% of the time) that the composite merely penalizes. The
    only ``-inf`` (ineligible) case is ``mean_n_trades == 0`` — a candidate that
    never trades is a stopped clock, not a strategy.

    Trade/time-in-position floors are PER FOLD (inner-oos slices are short — a
    fraction of the iter-loop's 50-trade floor over the full ~6-month OOS).
    """
    sr = np.asarray([s for s in fold_sharpes if s is not None], dtype=float)
    nt = np.asarray([t for t in fold_n_trades if t is not None], dtype=float)
    tip = np.asarray([p for p in fold_tip if p is not None], dtype=float)
    if sr.size == 0:
        return FoldScore(float("-inf"), 0, 0, 0, 0, 0, 0, False, "no folds")

    mean_sr = float(np.mean(sr))
    std_sr = float(np.std(sr, ddof=1)) if sr.size >= 2 else 0.0
    median_sr = float(np.median(sr))
    mean_nt = float(np.mean(nt)) if nt.size else 0.0
    mean_tip = float(np.mean(tip)) if tip.size else 0.0

    # Only true degeneracy is ineligible: a candidate that never trades.
    if mean_nt <= 0.0:
        return FoldScore(float("-inf"), mean_sr, std_sr, median_sr, mean_nt,
                         mean_tip, int(sr.size), False, "mean_n_trades == 0")

    score = mean_sr - stability_penalty * std_sr
    notes: list[str] = []
    # Graded low-activity penalty (one-sided, like composite_score).
    if mean_nt < min_trades_per_fold:
        deficit = 1.0 - math.sqrt(mean_nt / min_trades_per_fold)
        score -= low_trades_penalty * deficit
        notes.append(f"sparse(mean_nt={mean_nt:.1f})")
    # Graded time-in-position penalty (one-sided, like composite_score).
    if tip.size and mean_tip < min_tip:
        tip_deficit = 1.0 - max(mean_tip, 0.0) / min_tip
        score -= tip_penalty * tip_deficit
        notes.append(f"low_tip({mean_tip:.1f}%)")

    return FoldScore(float(score), mean_sr, std_sr, median_sr, mean_nt,
                     mean_tip, int(sr.size), True, "; ".join(notes))


# --------------------------------------------------------------------------- #
# Plateau clustering
# --------------------------------------------------------------------------- #
@dataclass
class Plateau:
    center: dict             # params of the highest-scoring member (an evaluated config)
    span: dict               # name -> [min, max] over members
    n_configs: int
    mean_score: float
    max_score: float
    median_fold_sharpe: float
    mean_fold_std: float
    members_idx: list[int] = field(default_factory=list)


def _normalize_point(cand: dict, specs: list[ParamSpec]) -> np.ndarray:
    out = np.empty(len(specs), dtype=float)
    for i, sp in enumerate(specs):
        rng = (sp.hi - sp.lo) or 1.0
        out[i] = (float(cand[sp.name]) - sp.lo) / rng
    return out


def cluster_plateaus(candidates: list[dict], scores: list[FoldScore],
                     specs: list[ParamSpec], top_frac: float = 0.25,
                     radius: float = 0.2, max_plateaus: int = 8) -> list[Plateau]:
    """Greedy plateau clustering in normalized [0,1]^d parameter space.

    1. Keep only eligible candidates whose score is in the top ``top_frac`` by
       value (the "good enough to be on a plateau" set).
    2. Sort that set by score descending. Walk it: each unclaimed point seeds a
       new plateau and absorbs every still-unclaimed point within ``radius``
       (normalized euclidean). The seed (highest score) becomes the plateau
       center — an actually-evaluated config, not an interpolated centroid.
    3. Report up to ``max_plateaus`` regions with their span and robustness
       stats. A region with many members spanning a wide box is robust; a
       1-member region is a spike to distrust.

    No scipy/sklearn dependency — the candidate count is small (hundreds).
    """
    elig = [i for i, s in enumerate(scores)
            if s.eligible and math.isfinite(s.score)]
    if not elig:
        return []

    vals = np.asarray([scores[i].score for i in elig], dtype=float)
    # Threshold: top_frac by score. Guard tiny sets so we always keep >=1.
    if len(vals) >= 4:
        cutoff = float(np.quantile(vals, 1.0 - top_frac))
    else:
        cutoff = float(np.min(vals))
    kept = [i for i in elig if scores[i].score >= cutoff]
    kept.sort(key=lambda i: scores[i].score, reverse=True)

    pts = {i: _normalize_point(candidates[i], specs) for i in kept}
    claimed: set = set()
    plateaus: list[Plateau] = []
    for seed in kept:
        if seed in claimed:
            continue
        members = [seed]
        claimed.add(seed)
        for j in kept:
            if j in claimed:
                continue
            if float(np.linalg.norm(pts[seed] - pts[j])) <= radius:
                members.append(j)
                claimed.add(j)
        # Aggregate the region.
        span = {}
        for sp in specs:
            mv = [float(candidates[m][sp.name]) for m in members]
            span[sp.name] = [min(mv), max(mv)]
        m_scores = [scores[m].score for m in members]
        m_med_sr = [scores[m].median_sharpe for m in members]
        m_std = [scores[m].std_sharpe for m in members]
        plateaus.append(Plateau(
            center=dict(candidates[seed]),
            span=span,
            n_configs=len(members),
            mean_score=float(np.mean(m_scores)),
            max_score=float(np.max(m_scores)),
            median_fold_sharpe=float(np.median(m_med_sr)),
            mean_fold_std=float(np.mean(m_std)),
            members_idx=members,
        ))
    # Rank: robust plateaus first — high mean score, then more members.
    plateaus.sort(key=lambda p: (p.mean_score, p.n_configs), reverse=True)
    return plateaus[:max_plateaus]
