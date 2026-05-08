"""Standalone HTML tear sheet for a single iteration.

Self-contained: Plotly via inline CDN <script>, no Jinja, no server roundtrip
needed once written. Open the .html file directly in a browser.

Sections:
  - Header: strategy / iter / verdict / period / params
  - Summary table: composite, DSR, sharpe, sortino, calmar, maxDD, ...
  - Equity (raw + adjusted) per window
  - Drawdown (underwater) per window
  - Monthly returns heatmap (years × months)
  - Rolling 30d Sharpe
  - Trade analysis (per-trade return distribution, side breakdown)
  - Worst-N drawdowns table
"""
from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def _safe(o: Any) -> Any:
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, dict):
        return {k: _safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_safe(v) for v in o]
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in rows
    )
    return f'<table class="kv">{body}</table>'


def _fmt(x: Any, n: int = 4) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)):
        return f"{x:.{n}f}"
    return str(x)


def _fmt_pct(x: Any, n: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x * 100:.{n}f}%"


def _trace(x: list, y: list, name: str, mode: str = "lines",
           line: dict | None = None, fill: str | None = None,
           hist: bool = False) -> dict:
    t: dict = {"name": name}
    if hist:
        t.update({"x": x, "type": "histogram", "nbinsx": 60})
    else:
        t.update({"x": x, "y": y, "mode": mode, "type": "scatter"})
    if line:
        t["line"] = line
    if fill:
        t["fill"] = fill
    return t


def _plotly_div(fig_id: str, traces: list[dict], layout: dict, height: int = 320) -> str:
    return f'''
    <div id="{fig_id}" style="height:{height}px"></div>
    <script>Plotly.newPlot("{fig_id}", {json.dumps(traces, default=str)},
      Object.assign({{
        paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
        font: {{color: "#cbd5e1", size: 11}},
        margin: {{t: 10, b: 36, l: 60, r: 10}},
        xaxis: {{gridcolor: "#334155"}},
        yaxis: {{gridcolor: "#334155"}},
        legend: {{orientation: "h", y: -0.18}},
      }}, {json.dumps(layout, default=str)}),
      {{displaylogo: false, responsive: true}});</script>
    '''


def _drawdown(eq: pd.Series) -> pd.Series:
    peak = eq.cummax()
    return eq / peak - 1.0


def _monthly_returns(eq: pd.Series) -> pd.DataFrame:
    """Compound monthly returns from an equity series, indexed by year × month."""
    if eq.empty:
        return pd.DataFrame()
    monthly = eq.resample("MS").last().pct_change().dropna()
    df = pd.DataFrame({"ret": monthly.values, "year": monthly.index.year, "month": monthly.index.month})
    pivot = df.pivot(index="year", columns="month", values="ret")
    return pivot.reindex(columns=range(1, 13))


def _worst_drawdowns(eq: pd.Series, n: int = 5) -> list[dict]:
    if eq.empty:
        return []
    dd = _drawdown(eq)
    in_dd = dd < 0
    if not in_dd.any():
        return []
    out = []
    grp = (in_dd != in_dd.shift()).cumsum()
    for _, sub in dd[in_dd].groupby(grp[in_dd]):
        depth = float(sub.min())
        start = sub.index[0]
        end = sub.index[-1]
        recovery_bars = int(len(sub))
        out.append({"start": start.isoformat(), "end": end.isoformat(),
                    "depth": depth, "bars": recovery_bars})
    out.sort(key=lambda d: d["depth"])
    return out[:n]


def build(iter_data: dict, equity_df: pd.DataFrame, trades_df: pd.DataFrame | None,
          history: list[dict]) -> str:
    """Render an HTML tear sheet for one iteration.

    iter_data keys expected: iter, verdict (optional), composite, dsr, params,
                              symbols, period, tf, metrics, wf_aggregate.
    equity_df: frame with columns [timestamp, equity, benchmark, window?,
               raw_equity?, funding_cashflow?].
    trades_df: standardized trade ledger or None.
    history: list of past iteration rows for context.
    """
    iter_id = iter_data.get("iter", "?")
    composite = iter_data.get("composite")
    dsr = iter_data.get("dsr")
    params = iter_data.get("params") or {}
    symbols = iter_data.get("symbols") or []
    period = iter_data.get("period") or ["", ""]
    tf = iter_data.get("tf", "?")
    metrics = iter_data.get("metrics") or {}
    wf_agg = iter_data.get("wf_aggregate")

    # Reshape equity: groupby window if present.
    df = equity_df.copy()
    if "window" not in df.columns:
        df["window"] = 0
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # ----- Equity curves per window -----
    eq_traces = []
    dd_traces = []
    for w_id, g in df.groupby("window"):
        g = g.sort_values("timestamp")
        ts = g["timestamp"].astype(str).tolist()
        eq_traces.append({
            "x": ts, "y": g["equity"].tolist(),
            "name": f"strategy w{int(w_id)}",
            "mode": "lines", "type": "scatter",
        })
        eq_traces.append({
            "x": ts, "y": g["benchmark"].tolist(),
            "name": f"buy & hold w{int(w_id)}",
            "mode": "lines", "type": "scatter",
            "line": {"dash": "dot", "width": 1, "color": "#94a3b8"},
            "showlegend": int(w_id) == 0,
        })
        dd = _drawdown(g.set_index("timestamp")["equity"]).reset_index()
        dd_traces.append({
            "x": dd["timestamp"].astype(str).tolist(),
            "y": dd["equity"].tolist(),
            "name": f"DD w{int(w_id)}",
            "mode": "lines", "type": "scatter",
        })

    # ----- Monthly returns heatmap on the concatenated equity -----
    eq_concat = df.sort_values("timestamp").set_index("timestamp")["equity"]
    mret = _monthly_returns(eq_concat)
    if not mret.empty:
        z = mret.values.tolist()
        years = mret.index.tolist()
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        heatmap = [{
            "z": z, "x": months, "y": [str(y) for y in years],
            "type": "heatmap",
            "colorscale": [[0, "#ef4444"], [0.5, "#1e293b"], [1, "#22c55e"]],
            "zmid": 0,
            "hovertemplate": "%{y} %{x}: %{z:.2%}<extra></extra>",
        }]
        heatmap_div = _plotly_div("monthly", heatmap, {
            "yaxis": {"autorange": "reversed"},
        }, height=240)
    else:
        heatmap_div = '<em class="dim">monthly returns: insufficient data</em>'

    # ----- Rolling 30d Sharpe (concatenated returns) -----
    rets = eq_concat.pct_change().dropna()
    if len(rets) > 200:
        # 30 days * 24 hourly bars = 720 (assuming 1h)
        roll_window = 720
        ann = math.sqrt(365.25 * 24)
        rolling = rets.rolling(roll_window).mean() / rets.rolling(roll_window).std() * ann
        rolling = rolling.dropna()
        rolling_div = _plotly_div("rolling", [{
            "x": rolling.index.astype(str).tolist(),
            "y": rolling.values.tolist(),
            "name": "rolling 30d Sharpe (annualized)",
            "mode": "lines", "type": "scatter",
            "line": {"color": "#60a5fa"},
        }], {"yaxis": {"title": {"text": "Sharpe"}}}, height=240)
    else:
        rolling_div = '<em class="dim">rolling Sharpe: insufficient data</em>'

    # ----- Worst drawdowns -----
    worst = _worst_drawdowns(eq_concat, n=5)
    if worst:
        worst_html = '<table class="grid"><thead><tr><th>start</th><th>end</th><th>depth</th><th>bars</th></tr></thead><tbody>'
        worst_html += "".join(
            f"<tr><td>{w['start']}</td><td>{w['end']}</td>"
            f"<td>{_fmt_pct(w['depth'])}</td><td>{w['bars']}</td></tr>"
            for w in worst
        )
        worst_html += "</tbody></table>"
    else:
        worst_html = '<em class="dim">no drawdowns</em>'

    # ----- Trade analysis -----
    if trades_df is not None and not trades_df.empty and "return_pct" in trades_df.columns:
        wins = trades_df[trades_df["pnl_quote"] > 0]
        losses = trades_df[trades_df["pnl_quote"] < 0]
        trade_summary = _kv_table([
            ("trades", len(trades_df)),
            ("win rate", _fmt_pct(len(wins) / max(len(trades_df), 1))),
            ("avg win", f"${_fmt(wins['pnl_quote'].mean(), 2)}" if len(wins) else "—"),
            ("avg loss", f"${_fmt(losses['pnl_quote'].mean(), 2)}" if len(losses) else "—"),
            ("payoff", _fmt(wins['pnl_quote'].mean() / -losses['pnl_quote'].mean(), 2)
             if len(wins) and len(losses) else "—"),
            ("median duration (h)", _fmt(trades_df.get("duration_hours", pd.Series([0])).median(), 1)),
        ])
        trade_hist = _plotly_div("trade_dist", [{
            "x": (trades_df["return_pct"] * 100).tolist(),
            "type": "histogram", "nbinsx": 60,
            "marker": {"color": "#60a5fa"},
            "name": "per-trade return %",
        }], {
            "xaxis": {"title": {"text": "return (%)"}},
            "yaxis": {"title": {"text": "count"}},
            "shapes": [{"type": "line", "x0": 0, "x1": 0, "yref": "paper",
                        "y0": 0, "y1": 1,
                        "line": {"color": "#94a3b8", "dash": "dash", "width": 1}}],
        }, height=260)
    else:
        trade_summary = '<em class="dim">no trades</em>'
        trade_hist = ""

    # ----- Header summary -----
    hdr_rows = [
        ("strategy iter", iter_id),
        ("composite", _fmt(composite)),
        ("DSR", _fmt(dsr, 3)),
        ("symbols", ", ".join(symbols)),
        ("period", " → ".join(str(x) for x in period)),
        ("timeframe", tf),
        ("params", json.dumps(_safe(params))),
    ]
    if wf_agg:
        hdr_rows += [
            ("WF mean ± std Sharpe",
             f"{_fmt(wf_agg.get('mean_sharpe'))} ± {_fmt(wf_agg.get('std_sharpe'))}"),
            ("WF worst MaxDD", _fmt_pct(wf_agg.get("worst_max_dd"))),
            ("WF window composites",
             json.dumps([round(c, 3) for c in (wf_agg.get("window_composites") or [])])),
        ]
    elif metrics.get("oos"):
        oos = metrics["oos"]
        hdr_rows += [
            ("OOS Sharpe", _fmt(oos.get("sharpe"))),
            ("OOS MaxDD", _fmt_pct(oos.get("max_dd"))),
            ("OOS PSR", _fmt(oos.get("psr"), 3)),
            ("OOS Sharpe CI",
             f"[{_fmt(oos.get('sharpe_ci_lo'))}, {_fmt(oos.get('sharpe_ci_hi'))}]"),
            ("OOS trades", oos.get("n_trades", "—")),
        ]

    summary_div = _kv_table(hdr_rows)

    return f'''<!doctype html>
<html><head>
<meta charset="utf-8">
<title>tear sheet — iter {iter_id}</title>
<script src="{PLOTLY_CDN}"></script>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #0f172a; color: #e2e8f0;
       font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
main {{ max-width: 1200px; margin: 24px auto; padding: 0 24px; }}
h1 {{ margin: 0 0 16px; font-size: 22px; }}
h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
      color: #94a3b8; margin: 0 0 12px; font-weight: 600; }}
section {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px;
           padding: 16px; margin-bottom: 16px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #334155; }}
.kv th {{ width: 180px; color: #94a3b8; font-weight: 500; }}
.kv td {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; }}
.grid th {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; }}
.grid td {{ font-family: ui-monospace, monospace; font-size: 12px; }}
.dim {{ color: #64748b; }}
.row2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 800px) {{ .row2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
<h1>Tear sheet — iter {iter_id} <span class="dim" style="font-size:14px">({iter_data.get("verdict","")})</span></h1>

<section><h2>Summary</h2>{summary_div}</section>

<section><h2>Equity</h2>
{_plotly_div("equity", eq_traces, {"yaxis": {"title": {"text": "equity"}}}, 360)}
</section>

<section><h2>Drawdown</h2>
{_plotly_div("dd", dd_traces, {"yaxis": {"title": {"text": "drawdown"}, "tickformat": ".0%", "rangemode": "nonpositive"}}, 260)}
</section>

<div class="row2">
  <section><h2>Monthly returns</h2>{heatmap_div}</section>
  <section><h2>Rolling 30d Sharpe</h2>{rolling_div}</section>
</div>

<section><h2>Trades</h2>
<div class="row2"><div>{trade_summary}</div><div>{trade_hist}</div></div>
</section>

<section><h2>Worst drawdown periods</h2>{worst_html}</section>

<section class="dim" style="font-size:12px">
generated {datetime.now(timezone.utc).isoformat()} · history depth: {len(history)} iterations
</section>
</main>
</body></html>
'''


def render_to_file(iter_data: dict, equity_df: pd.DataFrame,
                   trades_df: pd.DataFrame | None, history: list[dict],
                   out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build(iter_data, equity_df, trades_df, history),
                        encoding="utf-8")
    return out_path
