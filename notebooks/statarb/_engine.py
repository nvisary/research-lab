"""Reusable pair-spread engine for the STATARB line — timeframe-agnostic.

Extracted from nb06 so that nb07+ can reuse it instead of copy-pasting. Every
function here takes the bar count explicitly, so the same code runs on a 1h
panel and on a 15m panel; nothing assumes "hour".

The invariant this module exists to protect (the trap that bit us three times,
see README): **a spread is only defined together with its β**. Any difference,
mean or forward move of a spread must be computed with ONE frozen β, otherwise
the `Δβ·log(price)` jump dominates and you get 20-50σ nonsense. `Book.spread`
takes β as an argument for exactly that reason — there is no way to ask this
module for "the spread" without saying which β.

Usage::

    from _lab import *
    from _engine import Book, calibrate_thr, refit, sweep, shuffled

    bk = Book(px_log_train, syms)          # wide log-price panel
    thr = calibrate_thr(bk, nbars=24*7)
    R = refit(bk, nbars=24*7, thr=thr)
    trades = sweep(bk, R, mode="REFIT")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# vectorised Engle-Granger: OLS on levels + Dickey-Fuller t on the residual
# --------------------------------------------------------------------------
def eg_t_all(W: np.ndarray, IY: np.ndarray, IX: np.ndarray):
    """(t, β, mean, sd, rho, r2) of the EG residual for every pair, on window `W`.

    `W` is (bars x symbols) of LOG prices. ~25 µs per pair, ~11x faster than
    `statsmodels.coint`; agreement on the 5% selection was 95.7% (nb06 §1).

    `rho` is the AR(1) coefficient of the residual minus one (Δe on e_lag), so the
    half-life of reversion is `ln(0.5)/ln(1+rho)` bars — it comes free out of the
    same regression the t-stat does. `r2` is the fit of the levels regression.
    Both are candidate ex-ante filters, which is why they are returned at all.
    """
    n_pairs = IY.size
    out_t = np.empty(n_pairs)
    out_b = np.empty(n_pairs)
    out_m = np.empty(n_pairs)
    out_s = np.empty(n_pairs)
    out_r = np.empty(n_pairs)
    out_q = np.empty(n_pairs)
    for i in range(n_pairs):
        y, x = W[:, IY[i]], W[:, IX[i]]
        xc, yc = x - x.mean(), y - y.mean()
        beta = (xc @ yc) / (xc @ xc)
        e = y - beta * x
        ec = e - e.mean()
        el, de = ec[:-1], ec[1:] - ec[:-1]
        n = de.size
        Sxx = el @ el - el.sum() ** 2 / n
        Sxy = el @ de - el.sum() * de.sum() / n
        rho = Sxy / Sxx
        a = de.sum() / n - rho * el.sum() / n
        r = de - (a + rho * el)
        s2 = (r @ r) / (n - 2)
        out_t[i] = rho / np.sqrt(s2 / Sxx)
        out_b[i] = beta
        out_m[i] = e.mean()
        out_s[i] = e.std()
        out_r[i] = rho
        vy = y.var()
        out_q[i] = 1.0 - e.var() / vy if vy > 0 else np.nan
    return out_t, out_b, out_m, out_s, out_r, out_q


# --------------------------------------------------------------------------
# book = panel + pair list + index bookkeeping
# --------------------------------------------------------------------------
@dataclass
class Book:
    """A log-price panel plus every pair over its columns."""
    LP: pd.DataFrame                     # rows = bars, cols = symbols, LOG prices
    syms: list[str] = field(default_factory=list)
    pairs: list[tuple] | None = None     # explicit (y, x) order; EG is asymmetric

    def __post_init__(self):
        self.syms = self.syms or list(self.LP.columns)
        self.LP = self.LP[self.syms]
        self.M = self.LP.values
        self.index = self.LP.index
        # which leg is y matters: EG regresses y on x, so β and the ADF t-stat
        # both depend on the choice. nb06 put the LESS liquid leg on the left.
        self.PAIRS = self.pairs or list(combinations(self.syms, 2))
        self.IY = np.array([self.syms.index(y) for y, _ in self.PAIRS])
        self.IX = np.array([self.syms.index(x) for _, x in self.PAIRS])
        step = self.index[1] - self.index[0]
        self.bar_min = int(round(step.total_seconds() / 60))
        self.bars_per_day = int(round(1440 / self.bar_min))

    # -- bar-count helpers ------------------------------------------------
    def nbars(self, days: float) -> int:
        return int(round(days * self.bars_per_day))

    def day_ends(self) -> np.ndarray:
        """Position of the LAST bar of every calendar day (UTC)."""
        d = self.index.normalize().values
        return np.where(np.r_[d[1:] != d[:-1], True])[0]

    def with_panel(self, M: np.ndarray) -> "Book":
        """Same pairs/index, different prices (used for the synthetic null)."""
        bk = Book.__new__(Book)
        bk.__dict__.update(self.__dict__)
        bk.M = M
        return bk

    def spread(self, i: int, beta: float) -> np.ndarray:
        """log P_y − β·log P_x for pair `i`, with β you pass in explicitly."""
        return self.M[:, self.IY[i]] - beta * self.M[:, self.IX[i]]


def shuffled(M: np.ndarray, seed: int = 99) -> np.ndarray:
    """Panel with the same marginal return distributions but no cross-links.

    Each column's returns are permuted independently, so any cross-sectional
    relationship is destroyed while the per-symbol volatility profile survives.
    The mandatory baseline for anything that looks like an edge.
    """
    rng = np.random.default_rng(seed)
    rets = np.diff(M, axis=0)
    return np.vstack([M[0], M[0] + np.cumsum(np.apply_along_axis(rng.permutation, 0, rets),
                                             axis=0)])


# --------------------------------------------------------------------------
# threshold calibration on our own synthetics (exact 5% size)
# --------------------------------------------------------------------------
def calibrate_thr(bk: Book, nbars: int, q: float = 5.0, reps: int = 3,
                  seed: int = 11) -> float:
    """DF t-statistic cutoff giving an exact `q`% false-positive rate here.

    More trustworthy than asymptotic critical values: the null is built from
    OUR data (shuffled returns, so no relationship by construction) at the same
    window length, so the test size is right on this sample.
    """
    rng = np.random.default_rng(seed)
    ts = []
    span = len(bk.M) - nbars
    for rep in range(reps):
        lo = int(rep * span / max(reps, 1))
        seg = bk.M[lo: lo + nbars]
        rets = np.diff(seg, axis=0)
        fake = np.cumsum(np.apply_along_axis(rng.permutation, 0, rets), axis=0)
        ts.append(eg_t_all(fake, bk.IY, bk.IX)[0])
    return float(np.percentile(np.concatenate(ts), q))


# --------------------------------------------------------------------------
# causal refit: params estimated on bars <= end of day d, used on day d+1
# --------------------------------------------------------------------------
@dataclass
class Refit:
    T: np.ndarray          # (refits x pairs) DF t-stat
    B: np.ndarray          # β
    MU: np.ndarray         # residual mean
    SD: np.ndarray         # residual sd
    ends: np.ndarray       # bar position each refit was computed AT (inclusive)
    thr: float
    nbars: int
    bar_of: np.ndarray     # (bars,) which refit governs this bar; -1 = none yet
    RHO: np.ndarray | None = None   # AR(1) coef − 1 of the residual (half-life)
    R2: np.ndarray | None = None    # fit of the levels regression

    @property
    def sel(self) -> np.ndarray:
        """Boolean (refits x pairs): passed the ADF cutoff and β>0.

        β>0 is not cosmetic — β<0 puts both legs on the same side, which is a
        directional bet on the market, not a hedged spread (nb02 §4).
        """
        return (self.T < self.thr) & (self.B > 0)


def refit(bk: Book, nbars: int, thr: float, per_day: int = 1) -> Refit:
    """Refit every pair on a trailing `nbars` window, `per_day` times a day.

    Strictly causal: a refit computed at bar `e` governs bars `e+1 ...`, so no
    bar used for the decision is inside the estimation window.
    """
    ends = bk.day_ends()
    if per_day > 1:
        step = max(bk.bars_per_day // per_day, 1)
        ends = np.arange(step - 1, len(bk.M), step)
    ends = ends[ends >= nbars - 1]
    T, B, MU, SD, RHO, R2 = [], [], [], [], [], []
    for e in ends:
        t, b, mu, sd, rho, r2 = eg_t_all(bk.M[e - nbars + 1: e + 1], bk.IY, bk.IX)
        T.append(t); B.append(b); MU.append(mu); SD.append(sd)
        RHO.append(rho); R2.append(r2)
    bar_of = np.full(len(bk.M), -1, dtype=int)
    for k, e in enumerate(ends):
        hi = ends[k + 1] + 1 if k + 1 < len(ends) else len(bk.M)
        bar_of[e + 1: hi] = k
    return Refit(np.array(T), np.array(B), np.array(MU), np.array(SD),
                 ends, thr, nbars, bar_of, np.array(RHO), np.array(R2))


# --------------------------------------------------------------------------
# trading sweep
# --------------------------------------------------------------------------
def sweep(bk: Book, R: Refit, mode: str = "REFIT", zin: float = 2.0,
          zout: float = 0.5, zstop: float = 4.0, max_hold: int | None = None,
          cost_pct: float = 0.21, keep_path: bool = False,
          zcap: float | None = None, delay: int = 0,
          extra_sel: np.ndarray | None = None) -> pd.DataFrame:
    """Every non-overlapping trade per pair under the |z| entry/exit rule.

    `mode='ENTRY'` judges the exit by the parameters frozen at entry;
    `mode='REFIT'` judges it by the freshest refit. The position's β is frozen
    at entry either way — we hold what we bought; refits only move decisions.

    An upper entry bound is mandatory: without it, entering at z=5 with a stop
    at |z|>4 fires instantly every bar and mills out fake trades (nb03). It
    defaults to `zstop` but can be pinned via `zcap`, so a stop sweep does not
    silently shrink the entry band at the same time.

    `delay` bars between the decision and the fill (both entry and exit). This
    is the decisive test against microstructure artefacts: an "edge" that comes
    from the last price bouncing between bid and ask evaporates when you fill
    one bar later, while genuine reversion over hours barely notices.

    PnL is `side·Δspread / gross` in % — i.e. as a fraction of gross notional,
    which is the base costs are charged on, so leg count never double-counts.
    """
    zcap = zstop if zcap is None else zcap
    sel = R.sel if extra_sel is None else (R.sel & extra_sel)
    mh = max_hold if max_hold is not None else 2 * R.nbars
    bar_of = R.bar_of
    valid = bar_of >= 0
    kk = np.clip(bar_of, 0, None)
    nb_ = len(bk.M)
    rows, paths = [], []
    for i in range(len(bk.PAIRS)):
        ok = np.where(valid, sel[kk, i], False)
        if not ok.any():
            continue
        yv, xv = bk.M[:, bk.IY[i]], bk.M[:, bk.IX[i]]
        b_cur = np.where(valid, R.B[kk, i], np.nan)
        mu_cur = np.where(valid, R.MU[kk, i], np.nan)
        sd_cur = np.where(valid, R.SD[kk, i], np.nan)
        z_cur = (yv - b_cur * xv - mu_cur) / sd_cur
        cand = np.where(ok & np.isfinite(z_cur) & (np.abs(z_cur) > zin)
                        & (np.abs(z_cur) <= zcap))[0]
        busy_until = -1
        for t_in in cand:
            if t_in <= busy_until:
                continue
            beta_e, mu_e, sd_e = b_cur[t_in], mu_cur[t_in], sd_cur[t_in]
            g = 1 + abs(beta_e)
            side = -1 if z_cur[t_in] > 0 else 1
            s_pos = yv - beta_e * xv                 # β FROZEN at entry
            t_fill = min(t_in + delay, nb_ - 1)
            hi = min(t_in + mh, nb_ - 1)
            reason, t_out = "time", hi
            for t in range(t_fill + 1, hi + 1):
                if mode == "ENTRY":
                    z = (s_pos[t] - mu_e) / sd_e
                else:
                    if not valid[t]:
                        continue
                    z = z_cur[t]
                if abs(z) < zout:
                    reason, t_out = "target", t
                    break
                if abs(z) > zstop:
                    reason, t_out = "zstop", t
                    break
            t_exit = min(t_out + delay, nb_ - 1)
            gross_pnl = side * (s_pos[t_exit] - s_pos[t_fill]) / g * 100
            rows.append((i, t_fill, t_exit, side, z_cur[t_in], reason,
                         gross_pnl, gross_pnl - cost_pct, beta_e, g,
                         sd_e / g * 100))
            if keep_path:
                paths.append(side * (s_pos[t_fill:t_exit + 1] - s_pos[t_fill]) / g * 100)
            busy_until = t_exit
    df = pd.DataFrame(rows, columns=["pi", "i_in", "i_out", "side", "z_in", "reason",
                                     "gross", "pnl", "beta", "g", "sd_pct"])
    if len(df):
        df["t_in"] = bk.index[df.i_in.values]
        df["t_out"] = bk.index[df.i_out.values]
        df["bars"] = df.i_out - df.i_in
        df["hours"] = df.bars * bk.bar_min / 60
        df["pair"] = [f"{bk.PAIRS[p][0]}/{bk.PAIRS[p][1]}" for p in df.pi]
        df["sigma"] = df.gross / df.sd_pct          # move in σ of the formation
        # net directional weight of the position per unit of gross: long 1 unit of
        # y and short β of x leaves (1−β)/g of naked market exposure. Each pair is
        # β-hedged, but the PORTFOLIO sum of these is not zero — that is the
        # mechanism behind nb04's ±70% net exposure.
        df["net_w"] = df.side * (1 - df.beta) / df.g
        if keep_path:
            df["path"] = paths
    return df


# --------------------------------------------------------------------------
# rule-free response curve: where was z, where did the spread go next
# --------------------------------------------------------------------------
def response(bk: Book, R: Refit, horizons: list[int], zbins: np.ndarray,
             only_selected: bool = True):
    """Mean forward spread move (in σ of formation) bucketed by z.

    No entries, exits, stops or costs — the measurement that decides whether
    there is anything to trade before any rule is chosen. β is frozen at the
    measurement bar t, so the `Δβ·log(price)` trap cannot occur.
    """
    sel = R.sel
    bar_of = R.bar_of
    valid = bar_of >= 0
    kk = np.clip(bar_of, 0, None)
    S = np.zeros((len(zbins) - 1, len(horizons)))
    N = np.zeros((len(zbins) - 1, len(horizons)))
    for i in range(len(bk.PAIRS)):
        okp = valid & (sel[kk, i] if only_selected else True)
        if not okp.any():
            continue
        yv, xv = bk.M[:, bk.IY[i]], bk.M[:, bk.IX[i]]
        b = R.B[kk, i]
        sd = R.SD[kk, i]
        z = (yv - b * xv - R.MU[kk, i]) / sd
        good = okp & np.isfinite(z)
        if not good.any():
            continue
        bins = np.digitize(z, zbins) - 1
        for hi_, h in enumerate(horizons):
            fwd = np.full(len(yv), np.nan)
            dy = yv[h:] - yv[:-h]
            dx = xv[h:] - xv[:-h]
            fwd[:-h] = (dy - b[:-h] * dx) / sd[:-h]   # β frozen at t
            m = good & np.isfinite(fwd)
            if not m.any():
                continue
            np.add.at(S[:, hi_], bins[m], fwd[m])
            np.add.at(N[:, hi_], bins[m], 1)
    return S / np.maximum(N, 1), N


def pull(mean_resp: np.ndarray, zbins: np.ndarray, zmin: float = 2.0) -> np.ndarray:
    """Collapse a response curve into one number per horizon: mean-reversion pull.

    `-sign(z) · move`, averaged over |z| >= zmin. Positive = the spread came
    back; that is the quantity to compare against costs.
    """
    zc = np.array([(zbins[i] + zbins[i + 1]) / 2 for i in range(len(zbins) - 1)])
    zc[0], zc[-1] = zbins[1] - 0.5, zbins[-2] + 0.5
    hi = zc >= zmin
    lo = zc <= -zmin
    return (-mean_resp[hi].mean(axis=0) + mean_resp[lo].mean(axis=0)) / 2


# --------------------------------------------------------------------------
# portfolio: turn a candidate list into an equity curve under a capital limit
# --------------------------------------------------------------------------
def portfolio(bk: Book, cand: pd.DataFrame, f_pos: float = 0.02,
              start: float = 1000.0, cap_gross: float = 1.0,
              one_per_symbol: bool = False, cost_pct: float = 0.21,
              use_funding: bool = True, max_pos: int | None = None):
    """Simulate the candidate trades under a gross-notional limit.

    The per-trade numbers everywhere else answer "what does the average signal
    pay". This answers the different and harder question: "what does the account
    do", which is not the same thing the moment signals arrive faster than
    capital frees up. Contention is resolved by |z| — the most stretched spread
    gets the capital.

    `cand` needs `i_in, i_out, path, net_w, z_in, pair` (`path` = hourly
    cumulative gross PnL in % of the position's gross, from `sweep(keep_path=True)`);
    `fund` is used when present and `use_funding`.

    Returns `(equity, log, stats)`.
    """
    n = len(bk.M)
    by_bar: dict[int, list[int]] = {}
    for k, t in enumerate(cand.i_in.values):
        by_bar.setdefault(int(t), []).append(k)
    zabs = np.abs(cand.z_in.values)
    i_out = cand.i_out.values
    paths = cand.path.values
    net_w = cand.net_w.values
    fund = cand.fund.values if ("fund" in cand and use_funding) else np.zeros(len(cand))
    legs = [p.split("/") for p in cand.pair.values]

    cash = start
    eq = np.empty(n)
    gross_used = np.zeros(n)
    net_expo = np.zeros(n)
    open_pos: list[dict] = []
    held: dict[str, int] = {}
    log, skipped = [], {"gross": 0, "symbol": 0, "maxpos": 0}

    for t in range(n):
        # 1) exits first — capital frees up before new entries compete for it
        still = []
        for p in open_pos:
            if p["i_out"] == t:
                pnl = p["G"] * (p["path"][-1] + p["fund"]) / 100.0
                cash += pnl - p["G"] * cost_pct / 200.0
                for s in p["legs"]:
                    held[s] = held.get(s, 0) - 1
                    if held[s] <= 0:
                        held.pop(s, None)
                log.append({"i_in": p["i_in"], "i_out": t, "pair": p["pair"],
                            "G": p["G"], "pnl": pnl,
                            "ret_pct": pnl / p["G"] * 100.0})
            else:
                still.append(p)
        open_pos = still

        # 2) mark to market with what is currently open
        mtm = sum(p["G"] * p["path"][min(t - p["i_in"], len(p["path"]) - 1)] / 100.0
                  for p in open_pos)
        equity = cash + mtm
        eq[t] = equity

        # 3) entries, most stretched spread first
        for k in sorted(by_bar.get(t, []), key=lambda k: -zabs[k]):
            g_now = sum(p["G"] for p in open_pos)
            G = f_pos * equity
            if equity <= 0:
                break
            if (g_now + G) / equity > cap_gross:
                skipped["gross"] += 1
                continue
            if max_pos is not None and len(open_pos) >= max_pos:
                skipped["maxpos"] += 1
                continue
            if one_per_symbol and any(s in held for s in legs[k]):
                skipped["symbol"] += 1
                continue
            cash -= G * cost_pct / 200.0
            for s in legs[k]:
                held[s] = held.get(s, 0) + 1
            open_pos.append({"G": G, "path": paths[k], "i_in": t, "i_out": int(i_out[k]),
                             "pair": cand.pair.values[k], "legs": legs[k],
                             "net_w": net_w[k], "fund": fund[k]})
        gross_used[t] = sum(p["G"] for p in open_pos) / max(eq[t], 1e-9)
        net_expo[t] = sum(p["G"] * p["net_w"] for p in open_pos) / max(eq[t], 1e-9)

    E = pd.Series(eq, index=bk.index)
    dd = E / E.cummax() - 1
    r = E.pct_change().dropna()
    bars_yr = bk.bars_per_day * 365
    yrs = (bk.index[-1] - bk.index[0]).days / 365.25
    stats = {
        "итог": round(float(E.iloc[-1]), 1),
        "доходность %": round(float(E.iloc[-1] / start - 1) * 100, 2),
        "годовая %": round(((E.iloc[-1] / start) ** (1 / yrs) - 1) * 100, 2),
        "макс DD %": round(float(dd.min()) * 100, 2),
        "Sharpe": round(float(r.mean() / r.std() * np.sqrt(bars_yr)), 2) if r.std() > 0 else np.nan,
        "сделок взято": len(log),
        "кандидатов": len(cand),
        "% взято": round(len(log) / max(len(cand), 1) * 100, 1),
        "пропущено по gross": skipped["gross"],
        "пропущено по символу": skipped["symbol"],
        "ср. gross загрузка %": round(float(gross_used.mean()) * 100, 1),
        "макс gross %": round(float(gross_used.max()) * 100, 1),
        "ср. |net| экспозиция %": round(float(np.abs(net_expo).mean()) * 100, 1),
        "макс |net| %": round(float(np.abs(net_expo).max()) * 100, 1),
    }
    diag = pd.DataFrame({"gross": gross_used, "net": net_expo, "dd": dd.values},
                        index=bk.index)
    return E, pd.DataFrame(log), stats, diag


__all__ = ["eg_t_all", "Book", "Refit", "shuffled", "calibrate_thr", "refit",
           "sweep", "response", "pull", "portfolio"]
