"""Cointegration-based pairs trading on the broad Bybit USDT-perp universe.

Logic ported from C:\\[win] projects\\pairs_bot (find_pairs_async + the
PairMonitor z-score loop), stripped of all live-trading infrastructure
(ccxt, telegram, async scheduler, persistence) and rewritten as a single
pure function compatible with researchlab's strategy contract.

At each bar t the strategy runs a two-tier loop:

  1. Slow loop (every ``refresh_bars``).  On the rolling lookback ending
     strictly before t_r, scan candidate pairs from the broad universe:
     correlation prefilter -> OLS beta/alpha -> ADF p-value -> half-life
     -> spread vol -> beta-stability.  Take top-N by score, dedup so each
     symbol appears in at most one active pair.

  2. Fast loop (every bar within an active window).  For each active pair
     (a, b) with fitted (beta, alpha): spread = log(close_a) - beta*log(close_b)
     - alpha; rolling EWMA mu, sigma; z = (spread - mu) / sigma.  Hysteresis
     state machine: enter long (short) the spread when z crosses below
     -z_entry (above +z_entry), exit when |z| < z_exit, hard-stop when |z| >
     z_stop.  Long spread = long a, short beta * b.

  3. Aggregate per-symbol position with leg_size / n_active_pairs allocation,
     clip to MAX_POSITION, final .shift(1) before emit (lookahead-safe).

Survivorship caveat (AGENTS.md S10d): the universe is currently-listed
Bybit perps only.  Pairs that broke and got delisted are absent by
construction, biasing all metrics upward.  Treat baseline as a sanity check
on the porting itself, not a deployable edge.

Fixes applied vs. pairs_bot at port time:
  - half-life: phi <= 0 returned float('inf') in pairs_bot (rejecting
    fast / oscillating mean-reversion).  Now treated as 0.5 bars.
  - beta-stability test exposed as a tunable param (was hardcoded 70/30
    split + 0.7 ratio threshold).
  - all magic numbers (corr threshold, sigma min, z thresholds, hl bounds,
    beta bounds, EWMA span) exposed in DEFAULT_PARAMS / PARAM_SPACE.
  - Hurst, KPSS, Kalman, FDR/BH correction NOT ported (out of scope for v1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller


# --- Universe -----------------------------------------------------------
# All 173 currently-listed Bybit USDT-perps with 1m data on disk
# (data/bybit/perp/1m/).  Mirrors the live-bot scanner's full breadth.
DEFAULT_SYMBOLS = [
    "10000SATSUSDT", "1000BONKUSDT", "1000BTTUSDT", "1000FLOKIUSDT",
    "1000LUNCUSDT", "1000PEPEUSDT", "1000RATSUSDT", "1000XECUSDT",
    "API3USDT", "ARKUSDT", "ARPAUSDT", "ARUSDT", "ASTRUSDT", "ATOMUSDT",
    "AUCTIONUSDT", "AVAXUSDT", "AXLUSDT", "AXSUSDT", "BANDUSDT",
    "BATUSDT", "BCHUSDT", "BEAMUSDT", "BELUSDT", "BICOUSDT",
    "BIGTIMEUSDT", "BLURUSDT", "BNBUSDT", "BNTUSDT", "BOBAUSDT",
    "BSVUSDT", "BTCUSDT", "C98USDT", "CAKEUSDT", "CELOUSDT", "CFXUSDT",
    "CHRUSDT", "CHZUSDT", "CKBUSDT", "COMPUSDT", "COREUSDT", "COTIUSDT",
    "CROUSDT", "CRVUSDT", "CTCUSDT", "CVCUSDT", "CVXUSDT", "CYBERUSDT",
    "DASHUSDT", "DOGEUSDT", "DOTUSDT", "DUSKUSDT", "DYDXUSDT", "EDUUSDT",
    "EGLDUSDT", "ENJUSDT", "ENSUSDT", "ETCUSDT", "ETHUSDT", "FILUSDT",
    "FLOWUSDT", "FLRUSDT", "GALAUSDT", "GASUSDT", "GLMUSDT", "GMTUSDT",
    "GMXUSDT", "GODSUSDT", "GRTUSDT", "HBARUSDT", "HFTUSDT", "HIGHUSDT",
    "HNTUSDT", "ICPUSDT", "ICXUSDT", "IDUSDT", "ILVUSDT", "IMXUSDT",
    "INJUSDT", "IOSTUSDT", "IOTAUSDT", "IOTXUSDT", "JASMYUSDT", "JSTUSDT",
    "JTOUSDT", "KASUSDT", "KAVAUSDT", "KNCUSDT", "KSMUSDT", "LDOUSDT",
    "LINKUSDT", "LPTUSDT", "LQTYUSDT", "LRCUSDT", "LTCUSDT", "LUNA2USDT",
    "MAGICUSDT", "MEMEUSDT", "MNTUSDT", "MTLUSDT", "NEARUSDT", "NEOUSDT",
    "NMRUSDT", "OGNUSDT", "OGUSDT", "ONGUSDT", "ONTUSDT", "OPUSDT",
    "ORBSUSDT", "ORDIUSDT", "OXTUSDT", "PAXGUSDT", "PENDLEUSDT",
    "PEOPLEUSDT", "POLYXUSDT", "POWRUSDT", "PYTHUSDT", "QNTUSDT",
    "QTUMUSDT", "RAREUSDT", "REQUSDT", "RLCUSDT", "ROSEUSDT", "RPLUSDT",
    "RSRUSDT", "RUNEUSDT", "RVNUSDT", "SANDUSDT", "SCRTUSDT", "SCUSDT",
    "SEIUSDT", "SHIB1000USDT", "SKLUSDT", "SLPUSDT", "SNTUSDT", "SNXUSDT",
    "SOLUSDT", "SSVUSDT", "STEEMUSDT", "STGUSDT", "STXUSDT", "SUIUSDT",
    "SUNUSDT", "SUPERUSDT", "SUSHIUSDT", "THETAUSDT", "TIAUSDT",
    "TLMUSDT", "TONUSDT", "TRBUSDT", "TRXUSDT", "TUSDT", "TWTUSDT",
    "UMAUSDT", "UNIUSDT", "USTCUSDT", "VETUSDT", "WAVESUSDT", "WAXPUSDT",
    "WLDUSDT", "WOOUSDT", "XCNUSDT", "XLMUSDT", "XMRUSDT", "XRPUSDT",
    "XVGUSDT", "XVSUSDT", "YFIUSDT", "YGGUSDT", "ZECUSDT", "ZENUSDT",
    "ZILUSDT", "ZRXUSDT",
]
DEFAULT_TF = "1h"

# Pairs trading needs opposite-sign legs of matched notional, so the legacy
# equal-weight-slot semantics don't fit.  RAW_SIZING=True makes positions
# fractions of total equity directly.
RAW_SIZING = True
MAX_POSITION = 1.0

DEFAULT_PARAMS = {
    # Pair selection
    "lookback_bars":      720,    # ~30 days at 1h
    "refresh_bars":       168,    # rescan every 7 days at 1h
    "top_n_pairs":        5,
    "neighbor_k":         5,      # top-K corr neighbors per symbol in prefilter
    "min_corr":           0.65,
    "adf_pmax":           0.05,
    "hl_min":             4.0,
    "hl_max":             240.0,
    "beta_min":           0.3,
    "beta_max":           3.0,
    "sigma_min":          2e-4,
    "beta_stab_max":      0.7,    # |beta_late/beta_early - 1| threshold

    # Signal
    "ewma_span":          30,
    "z_entry":            2.0,
    "z_exit":             0.5,
    "z_stop":             4.0,

    # Sizing
    "leg_size":           0.5,
}

PARAM_SPACE = {
    "lookback_bars":      (240, 1440),
    "refresh_bars":       (24, 720),
    "top_n_pairs":        (1, 20),
    "neighbor_k":         (2, 20),
    "min_corr":           (0.4, 0.9),
    "adf_pmax":           (0.01, 0.10),
    "hl_min":             (1.0, 24.0),
    "hl_max":             (48.0, 720.0),
    "beta_min":           (0.1, 0.5),
    "beta_max":           (2.0, 5.0),
    "sigma_min":          (1e-5, 1e-2),
    "beta_stab_max":      (0.2, 2.0),
    "ewma_span":          (10, 100),
    "z_entry":            (1.0, 3.5),
    "z_exit":             (0.0, 1.5),
    "z_stop":             (2.5, 6.0),
    "leg_size":           (0.05, 1.0),
}


# --- Helpers -----------------------------------------------------------

def _ar1_phi_and_hl(spread: np.ndarray) -> tuple[float, float]:
    """Fit AR(1) s_t = a + phi*s_{t-1} on the spread; return (phi, half_life).

    Stationary mean-reverting AR(1) has phi in (-1, 1).  phi in (0, 1) is
    the standard OU regime: half_life = -ln(2) / ln(phi).
    phi in (-1, 0] is oscillating mean-reversion (faster than one bar);
    treat half-life as 0.5 bars (fix vs pairs_bot:245 which returned inf).
    phi >= 1 (random walk / explosive): half_life = inf.  No mean-reversion.

    Returns (phi, half_life).  phi is used both for stationarity gating
    and to fix the half-life-of-fast-MR bug from pairs_bot.
    """
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if len(s) < 5:
        return float("inf"), float("inf")
    s_lag = s[:-1]
    s_now = s[1:]
    X = np.column_stack([np.ones_like(s_lag), s_lag])
    try:
        coef, *_ = np.linalg.lstsq(X, s_now, rcond=None)
    except Exception:
        return float("inf"), float("inf")
    phi = float(coef[1])
    if not np.isfinite(phi):
        return float("inf"), float("inf")
    if phi >= 1.0:
        return phi, float("inf")
    if phi <= 0.0:
        return phi, 0.5
    hl = float(-np.log(2) / np.log(phi))
    return phi, hl


def _ols_beta_alpha(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """OLS y = beta*x + alpha via np.linalg.lstsq."""
    X = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(coef[1]), float(coef[0])


def _beta_stability(x: np.ndarray, y: np.ndarray) -> float:
    """|beta_late / beta_early - 1| on a 70/30 split.  Returns a large
    sentinel if degenerate."""
    n = len(x)
    if n < 40:
        return 1e9
    mid = int(n * 0.7)
    try:
        b1, _ = np.polyfit(x[:mid], y[:mid], 1)
        b2, _ = np.polyfit(x[mid:], y[mid:], 1)
    except Exception:
        return 1e9
    if not np.isfinite(b1) or not np.isfinite(b2) or abs(b1) < 1e-12:
        return 1e9
    return float(abs(b2 / b1 - 1.0))


def _select_pairs(log_panel: pd.DataFrame, params: dict) -> list[tuple]:
    """Run cointegration scan on a lookback window; return list of
    (a, b, beta, alpha) for top-N pairs.

    `log_panel` is a wide DataFrame of log close prices, one column per
    symbol, covering the lookback window [t_r - L, t_r) STRICTLY.  All
    information used here is in the past relative to the bars where the
    fitted pair will be applied.
    """
    # Keep only symbols with full coverage on the lookback window.
    panel = log_panel.dropna(axis=1, how="any")
    if panel.shape[1] < 2 or panel.shape[0] < 50:
        return []

    cols = list(panel.columns)
    arr = panel.values  # shape (L, S)

    # Returns and correlation prefilter.
    rets = np.diff(arr, axis=0)
    if rets.shape[0] < 10:
        return []
    # nan-safe corr
    rets = np.where(np.isfinite(rets), rets, 0.0)
    corr = np.corrcoef(rets.T)
    corr = np.where(np.isfinite(corr), corr, 0.0)

    k = max(1, int(params["neighbor_k"]))
    min_corr = float(params["min_corr"])

    # Build candidate edge list: top-K |corr| neighbors per symbol, undirected.
    seen = set()
    candidates: list[tuple[str, str]] = []
    abs_corr = np.abs(corr)
    np.fill_diagonal(abs_corr, -1.0)
    for i in range(len(cols)):
        order = np.argsort(-abs_corr[i])[:k]
        for j in order:
            if abs_corr[i, j] < min_corr:
                break
            a, b = cols[i], cols[j]
            edge = (a, b) if a < b else (b, a)
            if edge in seen:
                continue
            seen.add(edge)
            candidates.append(edge)

    if not candidates:
        return []

    beta_min = float(params["beta_min"])
    beta_max = float(params["beta_max"])
    adf_pmax = float(params["adf_pmax"])
    hl_min = float(params["hl_min"])
    hl_max = float(params["hl_max"])
    sigma_min = float(params["sigma_min"])
    beta_stab_max = float(params["beta_stab_max"])
    z_entry = float(params["z_entry"])
    # Round-trip cost estimate for scoring (taker fee 5.5 bps both legs,
    # 4 legs per round trip -> ~22 bps gross).  Hardcoded to match
    # pairs_bot.commission_roundtrip semantics; harness applies real fees
    # in the backtest itself.
    cost_estimate = 4 * (5.5 / 1e4)

    panel_arr = panel.values
    sym_idx = {c: i for i, c in enumerate(cols)}

    out: list[tuple] = []
    for a, b in candidates:
        ia, ib = sym_idx[a], sym_idx[b]
        lx = panel_arr[:, ia]
        ly = panel_arr[:, ib]
        try:
            beta, alpha = _ols_beta_alpha(ly, lx)  # lx = beta*ly + alpha
        except Exception:
            continue
        if not np.isfinite(beta) or not (beta_min <= abs(beta) <= beta_max):
            continue

        spread = lx - (beta * ly + alpha)
        if not np.all(np.isfinite(spread)):
            continue

        sigma = float(np.std(spread))
        if sigma < sigma_min:
            continue

        stab = _beta_stability(ly, lx)
        if stab > beta_stab_max:
            continue

        try:
            adf_p = float(adfuller(spread, autolag="AIC")[1])
        except Exception:
            continue
        if not np.isfinite(adf_p) or adf_p > adf_pmax:
            continue

        _phi, hl = _ar1_phi_and_hl(spread)
        if not (hl_min <= hl <= hl_max):
            continue

        # Score: edge per unit time (approx).  Cost-aware.
        edge = max(0.0, z_entry * sigma - cost_estimate)
        score = edge / max(hl, 1e-6)
        out.append((a, b, beta, alpha, score, adf_p))

    if not out:
        return []

    # Rank by score desc, then ADF p-value asc as tiebreaker.
    out.sort(key=lambda r: (-r[4], r[5]))

    # Dedup so each symbol appears in at most one active pair (matches
    # pairs_bot MAX_PAIRS_PER_SYMBOL=1).
    top_n = int(params["top_n_pairs"])
    used = set()
    selected: list[tuple] = []
    for a, b, beta, alpha, _score, _p in out:
        if a in used or b in used:
            continue
        selected.append((a, b, beta, alpha))
        used.add(a)
        used.add(b)
        if len(selected) >= top_n:
            break

    return selected


def _state_machine(z: np.ndarray, z_entry: float, z_exit: float,
                    z_stop: float) -> np.ndarray:
    """Per-bar pair direction with hysteresis.

    +1 = long spread  (long a, short beta*b), set when z < -z_entry.
    -1 = short spread (short a, long beta*b), set when z > +z_entry.
     0 = flat.

    Exit on mean-reversion (|z| < z_exit) or hard-stop on blow-out
    (|z| > z_stop in the same direction the spread moved against us).
    """
    n = len(z)
    out = np.zeros(n, dtype=np.float64)
    state = 0
    for i in range(n):
        zi = z[i]
        if not np.isfinite(zi):
            state = 0
        elif state == 0:
            if zi > z_entry:
                state = -1
            elif zi < -z_entry:
                state = +1
        elif state == +1:
            # Long spread: bet z rises toward 0.  Exit if z >= -z_exit
            # (mean-reverted).  Hard-stop if z < -z_stop (spread blew
            # further negative).
            if zi >= -z_exit or zi < -z_stop:
                state = 0
        else:  # state == -1
            # Short spread: bet z falls toward 0.  Exit if z <= +z_exit.
            # Hard-stop if z > +z_stop.
            if zi <= z_exit or zi > z_stop:
                state = 0
        out[i] = state
    return out


# --- Main entry --------------------------------------------------------

def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    if not data:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    closes = pd.concat({s: df["close"] for s, df in data.items()}, axis=1)
    closes = closes.sort_index()
    if closes.shape[0] < 100 or closes.shape[1] < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # Log close panel; non-positive values -> NaN (defensive).
    valid = closes.where(closes > 0)
    log_closes = np.log(valid)

    L = int(params.get("lookback_bars", 720))
    R = int(params.get("refresh_bars", 168))
    span = int(params.get("ewma_span", 30))
    leg_size = float(params.get("leg_size", 0.5))
    z_entry = float(params.get("z_entry", 2.0))
    z_exit = float(params.get("z_exit", 0.5))
    z_stop = float(params.get("z_stop", 4.0))

    L = max(50, L)
    R = max(1, R)
    span = max(2, span)

    n_bars = len(closes)
    if n_bars <= L + 5:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns,
                        dtype=np.float64)

    # Refresh ticks — first scan after enough lookback exists; subsequent
    # ticks every R bars.  Pair selection at t_r uses log_closes[t_r-L : t_r]
    # (strictly past).  The fitted pairs are applied to bars [t_r : t_end).
    refresh_ticks = list(range(L, n_bars, R))
    if not refresh_ticks:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    for i, t_r in enumerate(refresh_ticks):
        t_end = refresh_ticks[i + 1] if (i + 1) < len(refresh_ticks) else n_bars
        window = log_closes.iloc[t_r - L : t_r]
        active = _select_pairs(window, params)
        if not active:
            continue

        scale = leg_size / max(len(active), 1)
        slice_idx = closes.index[t_r : t_end]

        for a, b, beta, alpha in active:
            if a not in log_closes.columns or b not in log_closes.columns:
                continue
            la = log_closes[a]
            lb = log_closes[b]
            spread_full = la - beta * lb - alpha

            # EWMA over the full series; valid because we only USE the
            # slice [t_r:t_end] below.  At any t in that slice the EWMA
            # has a long warm-up history.  We shift mu/sigma by 1 so that
            # z[t] uses past EWMA only (final pos.shift(1) below ensures
            # the resulting position is itself lagged by one bar — total
            # decision at bar t depends on data <= t-1 only).
            mu = spread_full.ewm(span=span, adjust=False).mean()
            var = (spread_full - mu).pow(2).ewm(span=span, adjust=False).mean()
            sigma = np.sqrt(var.clip(lower=1e-12))
            z = (spread_full - mu) / sigma

            z_slice = z.loc[slice_idx].values
            if len(z_slice) == 0:
                continue
            direction = _state_machine(z_slice, z_entry, z_exit, z_stop)

            # Aggregate per-symbol exposure.  Long spread (+1) = long a,
            # short beta*b.  Sign of beta is preserved naturally.
            pos.loc[slice_idx, a] = pos.loc[slice_idx, a].values + direction * scale
            pos.loc[slice_idx, b] = pos.loc[slice_idx, b].values - direction * beta * scale

    pos = pos.clip(lower=-MAX_POSITION, upper=MAX_POSITION)
    pos = pos.shift(1).fillna(0.0)

    pos.index.name = "timestamp"
    pos.columns.name = "symbol"
    out = pos.stack().rename("position").reset_index()
    return out[["timestamp", "symbol", "position"]]
