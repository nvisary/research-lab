"""Per-iteration diagnostic JSON — surfaces what the agent's verdict
summary doesn't show.

The standard verdict JSON gives ~10 numbers (composite, OOS Sharpe,
DD, n_trades, DSR, etc.). That's enough to decide KEEP/REVERT but
hides the kind of red flags an experienced reviewer would catch:

  - Per-window shape (is one window dominating? are sharpe gaps large?)
  - DSR trajectory (declining DSR despite rising composite = selection bias)
  - Equity curve shape (is it smooth, or one-fat-tail?)
  - Monthly losing streaks
  - Trade distribution (hit rate × payoff)

This module computes a compact JSON (~30-50 lines) that gets attached
to the verdict summary and surfaced to the agent. Best-effort — if a
parquet is missing or malformed, the affected section is just omitted
and the rest succeeds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def compute_regime(equity_df: pd.DataFrame, tf: str | None = None) -> dict:
    """Vol × Trend regime decomposition of strategy returns.

    Buckets each bar by (a) rolling std of benchmark (BTC) returns and
    (b) rolling mean of benchmark returns; both are 2-day rolling.
    Quantile-cuts vol into 4 quartiles and trend into 3 terciles, so 12
    cells. For each cell, computes annualized Sharpe of the *strategy*
    return restricted to bars in that cell — so the table answers
    "where in the (vol, trend) plane does the edge live?".

    Cheap to run on every iter (a few rolling reductions). The full
    table goes into the diagnostics JSON; the agent reads only a
    one-line flag derived from it.

    Returns ``{}`` if the curve is too short or the benchmark is
    missing — never raises.
    """
    if equity_df is None or equity_df.empty:
        return {}
    if "benchmark" not in equity_df.columns or "equity" not in equity_df.columns:
        return {}

    df = equity_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "window" in df.columns:
        # Stitch per-window in chronological order. Per-bar pct_change
        # within each window is used so cross-window jumps don't poison
        # the return series.
        parts_eq, parts_b = [], []
        for _, g in df.groupby("window"):
            g = g.sort_values("timestamp").set_index("timestamp")
            parts_eq.append(g["equity"].pct_change())
            parts_b.append(g["benchmark"].pct_change())
        strat_ret = pd.concat(parts_eq).sort_index().dropna()
        bench_ret = pd.concat(parts_b).sort_index().dropna()
    else:
        s = df.sort_values("timestamp").set_index("timestamp")
        strat_ret = s["equity"].pct_change().dropna()
        bench_ret = s["benchmark"].pct_change().dropna()

    aligned = pd.concat([strat_ret, bench_ret], axis=1, join="inner").dropna()
    aligned.columns = ["s", "b"]
    if len(aligned) < 100:
        return {}

    from harness.metrics import TF_PERIODS_PER_YEAR
    ppy = TF_PERIODS_PER_YEAR.get(tf, 365.25 * 24)  # fall back to 1h
    bars_per_day = max(1.0, ppy / 365.25)
    W = max(10, int(round(2 * bars_per_day)))

    vol = aligned["b"].rolling(W).std()
    trend = aligned["b"].rolling(W).mean()
    valid = vol.notna() & trend.notna()
    if int(valid.sum()) < 100:
        return {}

    vol_v = vol[valid]
    trend_v = trend[valid]
    sret = aligned["s"][valid]

    try:
        vb = pd.qcut(vol_v, q=4, labels=["v1", "v2", "v3", "v4"], duplicates="drop")
        tb = pd.qcut(trend_v, q=3, labels=["bear", "flat", "bull"], duplicates="drop")
    except Exception:
        return {}

    ann = float(np.sqrt(ppy))
    table: list[dict[str, Any]] = []
    for v_lvl in list(vb.cat.categories):
        for t_lvl in list(tb.cat.categories):
            mask = (vb == v_lvl) & (tb == t_lvl)
            n = int(mask.sum())
            cell: dict[str, Any] = {
                "vol": str(v_lvl), "trend": str(t_lvl),
                "n_bars": n,
            }
            if n < 5:
                cell.update({"sharpe": None, "mean_ret_bps": None,
                             "hit_rate_pct": None})
            else:
                r_in = sret[mask]
                sd = float(r_in.std(ddof=1)) if len(r_in) >= 2 else 0.0
                sh = float(r_in.mean() / sd * ann) if sd > 0 else 0.0
                cell.update({
                    "sharpe": round(sh, 2),
                    # Mean per-bar return in basis points — rescaled so
                    # the table reads in human units regardless of TF.
                    "mean_ret_bps": round(float(r_in.mean()) * 1e4, 2),
                    "hit_rate_pct": round(float((r_in > 0).mean()) * 100, 1),
                })
            table.append(cell)

    eligible = [c for c in table if c["sharpe"] is not None and c["n_bars"] >= 20]
    n_total = len(eligible)
    n_healthy = sum(1 for c in eligible if c["sharpe"] > 0.5)
    n_loss = sum(1 for c in eligible if c["sharpe"] < 0)

    return {
        "window_bars": W,
        "n_buckets_total": n_total,
        "n_buckets_healthy": n_healthy,
        "n_buckets_lossy": n_loss,
        "buckets": table,
    }


def build_diagnostics(iter_id: int, runs_dir: Path, summary: dict,
                      result: dict, dsr_value: float) -> dict:
    """Build the rich per-iter diagnostic JSON.

    Parameters
    ----------
    iter_id : int
        The iteration number (used to find equity/trades parquets).
    runs_dir : Path
        ``strategies/<name>/runs`` — root for parquets, history, etc.
    summary : dict
        The condensed verdict summary already built by ``run_one``.
    result : dict
        The full backtest result dict from ``harness.backtest.run`` —
        contains ``walk_forward.windows`` with per-window train/oos metrics.
    dsr_value : float
        Current iter's deflated Sharpe (already computed upstream).

    Returns
    -------
    dict
        A nested dict with keys: ``windows``, ``trajectory``, ``stitched``,
        ``monthly``, ``shape``, ``flags``. Each is independently best-effort.
    """
    out: dict[str, Any] = {}

    # --- Per-window train vs OOS, with sharpe gap ---
    windows_in = (result.get("walk_forward") or {}).get("windows") or []
    if windows_in:
        ws = []
        for i, w in enumerate(windows_in):
            tr = w.get("train") or {}
            os_ = w.get("oos") or {}
            tr_sh = float(tr.get("sharpe", 0) or 0)
            os_sh = float(os_.get("sharpe", 0) or 0)
            ws.append({
                "w": i,
                "train_sh": round(tr_sh, 2),
                "oos_sh": round(os_sh, 2),
                "gap": round(tr_sh - os_sh, 2),
                "trades": int(os_.get("n_trades", 0)),
                "dd": round(float(os_.get("max_dd", 0) or 0), 3),
            })
        out["windows"] = ws

    # --- Trajectory: DSR, composite over recent iters ---
    history = _load_history(runs_dir)
    if history:
        dsr_series = [float(h.get("dsr", 0) or 0)
                       for h in history if h.get("dsr") is not None]
        comp_series = [h.get("composite") for h in history
                        if h.get("composite") is not None
                        and isinstance(h.get("composite"), (int, float))]
        if dsr_series:
            best_dsr = max(dsr_series)
            out["trajectory"] = {
                "n_iters": len(history),
                "dsr_now": round(dsr_value, 2),
                "dsr_best": round(best_dsr, 2),
                "dsr_delta_from_best": round(dsr_value - best_dsr, 2),
                "composite_last_5": [round(float(c), 2) for c in comp_series[-5:]],
            }

    # --- Stitched equity (24mo compounded) + reconciliation ---
    eq_path = runs_dir / "equity" / f"iter_{iter_id:04d}.parquet"
    tr_path = runs_dir / "trades" / f"iter_{iter_id:04d}.parquet"
    if eq_path.exists():
        try:
            eq_df = pd.read_parquet(eq_path)
            out["stitched"] = _stitched(eq_df, tr_path)
        except Exception:
            pass

    # --- Monthly returns shape (red/green count, longest neg streak) ---
    if eq_path.exists():
        try:
            eq_df = pd.read_parquet(eq_path)
            out["monthly"] = _monthly(eq_df)
        except Exception:
            pass

    # --- Trade ledger shape (win rate, payoff, fat-tail check) ---
    if tr_path.exists():
        try:
            tr_df = pd.read_parquet(tr_path)
            out["shape"] = _trade_shape(tr_df)
        except Exception:
            pass

    # --- WF trade-overlap diagnostic ---
    # Disjoint walk-forward windows tile [s, e); a strategy's run within
    # window i+1 starts from (its) train_start, which in disjoint mode
    # equals window_i.oos_end. If the strategy holds a position across
    # that boundary, vbt closes it at window_i.oos_end and may re-open
    # an identical-time, identical-symbol trade in window_{i+1}'s train
    # — same (entry_time, symbol, entry_price) tuple appears in both
    # windows' ledgers, and the stitched equity in the dashboard
    # double-counts the P&L on those bars. We just count and flag;
    # the fix (deduplicate or scope each window to its OOS slice) is
    # a separate decision the operator should make explicitly.
    if tr_path.exists():
        try:
            tr_df = pd.read_parquet(tr_path)
            out["wf_overlap"] = _wf_trade_overlap(tr_df)
        except Exception:
            pass

    # --- Regime decomposition (vol × trend buckets) ---
    if eq_path.exists():
        try:
            eq_df = pd.read_parquet(eq_path)
            tf = result.get("tf") if isinstance(result, dict) else None
            reg = compute_regime(eq_df, tf=tf)
            if reg:
                out["regime"] = reg
        except Exception:
            pass

    # --- Heuristic flags (✓ ⚠ ✗ ℹ) ---
    out["flags"] = _flags(out, summary)

    return out


def _load_history(runs_dir: Path) -> list[dict]:
    p = runs_dir / "history.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _stitched(eq_df: pd.DataFrame, tr_path: Path) -> dict:
    eq_df = eq_df.copy()
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"], utc=True)
    has_windows = "window" in eq_df.columns

    if has_windows:
        prod = 1.0
        for _, g in eq_df.groupby("window"):
            g = g.sort_values("timestamp")
            if len(g) < 2:
                continue
            prod *= float(g["equity"].iloc[-1] / g["equity"].iloc[0])
    else:
        eq_df = eq_df.sort_values("timestamp")
        if len(eq_df) < 2:
            return {}
        prod = float(eq_df["equity"].iloc[-1] / eq_df["equity"].iloc[0])

    out = {"compounded_return_pct": round((prod - 1) * 100, 2)}

    if tr_path.exists():
        try:
            tr_df = pd.read_parquet(tr_path)
            if "pnl_quote" in tr_df.columns:
                out["trade_pnl_sum_usd"] = round(float(tr_df["pnl_quote"].sum()), 2)
        except Exception:
            pass

    if "funding_cashflow" in eq_df.columns:
        out["funding_paid_usd"] = round(float(eq_df["funding_cashflow"].sum()), 2)

    return out


def _monthly(eq_df: pd.DataFrame) -> dict:
    eq_df = eq_df.copy()
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"], utc=True)

    if "window" in eq_df.columns:
        # Stitch per-bar returns within each window, concatenate
        # chronologically — matches the api_monthly_returns approach
        # so the agent's view aligns with the dashboard's heatmap.
        rets = []
        for _, g in eq_df.groupby("window"):
            g = g.sort_values("timestamp")
            r = g["equity"].pct_change().fillna(0.0)
            rets.append(pd.Series(r.values, index=g["timestamp"]))
        s = pd.concat(rets).sort_index()
    else:
        s = eq_df.sort_values("timestamp").set_index(
            "timestamp")["equity"].pct_change().fillna(0.0)

    monthly = ((1 + s).resample("1MS").prod() - 1).dropna()
    if monthly.empty:
        return {}

    streak = 0
    max_streak = 0
    for r in monthly:
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return {
        "n_months": int(len(monthly)),
        "n_red": int((monthly < 0).sum()),
        "n_green": int((monthly > 0).sum()),
        "longest_neg_streak": int(max_streak),
        "worst_month_pct": round(float(monthly.min()) * 100, 2),
        "best_month_pct": round(float(monthly.max()) * 100, 2),
    }


def _trade_shape(tr_df: pd.DataFrame) -> dict:
    if tr_df.empty or "pnl_quote" not in tr_df.columns:
        return {}
    pnl = tr_df["pnl_quote"].astype(float)
    n = int(len(pnl))
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    out = {
        "n_trades": n,
        "win_rate_pct": round(float((pnl > 0).sum()) / max(n, 1) * 100, 1),
        "expectancy_usd": round(float(pnl.mean()), 2) if n else None,
    }
    if len(wins) > 0 and len(losses) > 0:
        out["payoff_ratio"] = round(float(wins.mean() / (-losses.mean())), 2)
        # Profit factor: Σ(wins)/|Σ(losses)|. Different from payoff_ratio
        # (which is mean win / mean loss) — PF accounts for trade COUNTS,
        # so a strategy with 90% small wins and 10% giant losses can have
        # a great payoff but a bad PF.
        loss_sum = float(-losses.sum())
        if loss_sum > 0:
            out["profit_factor"] = round(float(wins.sum() / loss_sum), 2)
        if "return_pct" in tr_df.columns:
            out["median_win_pct"] = round(
                float(tr_df.loc[pnl > 0, "return_pct"].median()) * 100, 2)
            out["median_loss_pct"] = round(
                float(tr_df.loc[pnl < 0, "return_pct"].median()) * 100, 2)
            out["avg_win_pct"] = round(
                float(tr_df.loc[pnl > 0, "return_pct"].mean()) * 100, 2)
            out["avg_loss_pct"] = round(
                float(tr_df.loc[pnl < 0, "return_pct"].mean()) * 100, 2)
    elif len(wins) > 0:
        # All wins, no losses — JSON can't represent inf cleanly.
        out["profit_factor"] = None
    elif len(losses) > 0:
        out["profit_factor"] = 0.0

    # Fat-tail check: largest single trade as % of |total pnl|
    total = float(pnl.sum())
    if abs(total) > 1e-6:
        out["largest_trade_pct_of_total"] = round(
            float(pnl.abs().max()) / abs(total) * 100, 1)

    return out


def _wf_trade_overlap(tr_df: pd.DataFrame) -> dict:
    """Count trades whose (entry_time, symbol) tuple appears in more than
    one WF window. Indicates a position held across a window boundary
    that vbt closed and re-opened — those bars contribute to two
    windows' P&L when iterate.py stitches the equity for the dashboard.

    Returns ``{}`` if the trade ledger lacks the ``window`` column
    (single-split mode) or required key columns.
    """
    if tr_df is None or tr_df.empty:
        return {}
    cols = tr_df.columns
    if "window" not in cols or "entry_time" not in cols or "symbol" not in cols:
        return {}
    # Group by (entry_time, symbol). A tuple appearing in >1 window
    # is an overlap. We don't dedupe on price/size because the
    # re-opened trade's price will be the next bar's open and the
    # tuple already uniquely identifies the calendar bar.
    grp = tr_df.groupby(["entry_time", "symbol"], dropna=False)["window"].nunique()
    overlapping = grp[grp > 1]
    overlap_count = int(len(overlapping))
    if overlap_count == 0:
        return {"overlap_count": 0, "n_trades_total": int(len(tr_df))}
    # Pull the top few offenders for an at-a-glance look.
    sample = []
    for (et, sym), n_windows in overlapping.head(5).items():
        rows = tr_df[(tr_df["entry_time"] == et) & (tr_df["symbol"] == sym)]
        sample.append({
            "entry_time": str(et),
            "symbol": str(sym),
            "n_windows": int(n_windows),
            "windows": sorted([int(x) for x in rows["window"].unique()]),
            "pnl_quote_sum": float(rows["pnl_quote"].sum())
                if "pnl_quote" in cols else None,
        })
    return {
        "overlap_count": overlap_count,
        "n_trades_total": int(len(tr_df)),
        "sample": sample,
    }


def _flags(diag: dict, summary: dict) -> list[str]:
    """Heuristic ✓/⚠/✗/ℹ markers — one-line, machine-scannable."""
    flags: list[str] = []

    # WF trade overlap: surfaced first when present because it affects
    # how the operator should read the rest of the numbers below
    # (stitched equity is double-counting some bars' P&L).
    wf_overlap = (diag.get("wf_overlap") or {}).get("overlap_count") or 0
    if wf_overlap > 0:
        flags.append(
            f"⚠ {wf_overlap} trade(s) cross WF window boundary — "
            f"stitched equity double-counts those bars' P&L"
        )

    sh = float(summary.get("oos_sharpe") or 0)
    if sh >= 1.5:
        flags.append(f"✓ strong OOS Sharpe ({sh:+.2f})")
    elif sh < 0:
        flags.append(f"✗ negative OOS Sharpe ({sh:+.2f})")

    windows = diag.get("windows", [])
    if windows:
        n_pos = sum(1 for w in windows if w["oos_sh"] > 0)
        if n_pos == len(windows):
            flags.append(f"✓ all {len(windows)} windows positive OOS")
        elif n_pos == 0:
            flags.append(f"✗ all {len(windows)} windows negative OOS")
        elif n_pos == 1:
            flags.append(
                f"⚠ only 1/{len(windows)} windows positive — single-window-dominant")

        worst_gap = max((w["gap"] for w in windows), default=0)
        if worst_gap > 1.0:
            worst_w = max(windows, key=lambda w: w["gap"])
            flags.append(
                f"⚠ sharpe_gap W{worst_w['w']}: {worst_gap:+.2f} "
                f"(train sh > oos by >1.0 — overfit signal)")

        # Single-window dominance via |sharpe| share
        if len(windows) >= 3:
            sharpes_abs = [abs(w["oos_sh"]) for w in windows]
            total_abs = sum(sharpes_abs)
            if total_abs > 0:
                max_share = max(sharpes_abs) / total_abs
                if max_share > 0.5:
                    top_w = max(windows, key=lambda w: abs(w["oos_sh"]))
                    flags.append(
                        f"⚠ W{top_w['w']} dominates: {max_share*100:.0f}% "
                        f"of total |sharpe| across windows")

    traj = diag.get("trajectory", {})
    if traj.get("dsr_delta_from_best", 0) <= -0.2:
        flags.append(
            f"⚠ DSR down {abs(traj['dsr_delta_from_best']):.2f} from peak "
            f"({traj['dsr_best']:.2f} → {traj['dsr_now']:.2f}) — "
            f"selection bias accruing")

    n_trades = int(summary.get("oos_n_trades") or 0)
    if 0 < n_trades < 50:
        flags.append(
            f"⚠ oos_n_trades {n_trades} < 50 — penalty active, small sample")
    elif n_trades == 0:
        flags.append("✗ 0 OOS trades — composite forced to -inf")

    shape = diag.get("shape", {})
    if shape.get("largest_trade_pct_of_total", 0) > 30:
        flags.append(
            f"⚠ largest trade = "
            f"{shape['largest_trade_pct_of_total']:.0f}% of total PnL — "
            f"fat-tail dependent")

    monthly = diag.get("monthly", {})
    if monthly.get("longest_neg_streak", 0) >= 3:
        flags.append(
            f"⚠ {monthly['longest_neg_streak']} consecutive negative "
            f"months — lossy regime hidden in aggregate")

    # Reconciliation summary: stitched compounded return vs trade pnl
    # sum. They measure different things — stitched is one $10k account
    # compounded across windows, trade pnl is the sum across N independent
    # $10k accounts. They should reconcile via: trade_pnl - funding ≈
    # sum of per-window equity changes. We surface both for context.
    stitched = diag.get("stitched", {})
    cmp_ret = stitched.get("compounded_return_pct")
    trade_sum = stitched.get("trade_pnl_sum_usd")
    funding = stitched.get("funding_paid_usd")
    if cmp_ret is not None and trade_sum is not None:
        funding_str = (f"funding ${funding:+.0f}"
                        if funding is not None else "funding n/a")
        flags.append(
            f"ℹ stitched {cmp_ret:+.2f}pct vs trade pnl ${trade_sum:+.0f} "
            f"({funding_str})")

    # Selection-bias trap: positive aggregate OOS Sharpe but negative
    # 24mo compounded return = edge lives only on OOS slices, not
    # across the full period. This is exactly what burned bb_rsi_meanrev
    # (composite +1.58 → holdout -4.02). Fires whenever stitched is
    # available, regardless of trade ledger presence.
    if cmp_ret is not None and sh > 0.5 and cmp_ret < 0:
        flags.append(
            f"⚠⚠ OOS Sharpe {sh:+.2f} but 24mo stitched {cmp_ret:+.1f}pct "
            f"— edge lives in OOS slices only, suspect WF calendar bias")

    # Regime decomposition: how many vol×trend buckets carry the edge?
    regime = diag.get("regime", {})
    rt = int(regime.get("n_buckets_total", 0))
    rh = int(regime.get("n_buckets_healthy", 0))
    if rt >= 6:
        share = rh / rt
        if share <= 0.25:
            flags.append(
                f"✗ regime: {rh}/{rt} buckets healthy "
                f"(Sharpe>0.5) — single-regime strategy")
        elif share < 0.5:
            flags.append(
                f"⚠ regime: {rh}/{rt} buckets healthy — "
                f"limited regime coverage")
        else:
            flags.append(
                f"✓ regime: {rh}/{rt} buckets healthy — multi-regime")

    # Profit Factor: < 1.0 means cumulative losses > wins, regardless
    # of Sharpe. Flag visibly because PF is the single best leading
    # indicator of "looks good but isn't".
    pf = (summary.get("oos_metrics") or {}).get("profit_factor") if \
         isinstance(summary.get("oos_metrics"), dict) else None
    # Fall back to shape (per-iter trade aggregate) if oos_metrics not
    # yet wired into summary.
    if pf is None:
        pf = (shape or {}).get("profit_factor")
    if pf is not None:
        if pf < 1.0:
            flags.append(f"✗ profit_factor {pf:.2f} < 1.0 — losses dominate")
        elif pf < 1.3:
            flags.append(f"⚠ profit_factor {pf:.2f} — thin edge")

    return flags


# --------------------------------------------------------------------------- #
# Standalone CLI: re-compute diagnostics for an existing iter without rerunning
# the backtest. Useful for inspecting historical runs after this module is added.
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse, io, sys
    # Force UTF-8 on stdout so the ✓/⚠/✗/ℹ glyphs survive Windows cp1252.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Re-compute diagnostics JSON for an existing iter.")
    ap.add_argument("strategy_dir")
    ap.add_argument("--iter", type=int, default=None,
                    help="Iter number; default = best.iter from best.json")
    args = ap.parse_args()

    sd = Path(args.strategy_dir)
    runs = sd / "runs"
    iter_id = args.iter
    if iter_id is None:
        bp = runs / "best.json"
        if bp.exists():
            iter_id = json.loads(bp.read_text(encoding="utf-8")).get("iter")
    if iter_id is None:
        print("error: --iter required (no best.json)", file=sys.stderr)
        sys.exit(1)

    # Pull this iter's row from history for summary + result reconstruction
    history = _load_history(runs)
    row = next((h for h in history if h.get("iter") == iter_id), None)
    if row is None:
        print(f"error: iter {iter_id} not in history.jsonl", file=sys.stderr)
        sys.exit(1)

    summary = {
        "verdict": row.get("verdict"),
        "composite": row.get("composite"),
        "oos_sharpe": (row.get("metrics_oos") or {}).get("sharpe"),
        "oos_max_dd": (row.get("metrics_oos") or {}).get("max_dd"),
        "oos_n_trades": (row.get("metrics_oos") or {}).get("n_trades"),
    }
    result = {"walk_forward": row.get("walk_forward")}
    dsr = float(row.get("dsr") or 0)

    diag = build_diagnostics(iter_id, runs, summary, result, dsr)
    print(json.dumps(diag, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
