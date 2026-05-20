/**
 * Sweep page — cross-symbol × cross-period robustness matrix.
 *
 * Lets the operator launch a sweep across any subset of available symbols
 * (explicit / top-N by volume / all-symbols / all-symbols-covered) and any
 * set of periods (year presets / train / holdout / custom), then explore the
 * result as a heatmap of (symbol × period) cells with click-through equity
 * drawers and an OOS-returns correlation matrix.
 *
 * Sweeps are read-only relative to the iter loop: never touch history.jsonl,
 * best.json, or program.md.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type Job,
  type StrategySummary,
  type SweepCellRow,
  type SweepCorrelations,
  type SweepEquityCurve,
  type SweepListEntry,
  type SweepPayload,
  type SweepRunRequest,
  type SymbolMeta,
} from "../api";
import { fmt, fmtPct } from "../format";
import { SweepHeatmap, type SweepMetric } from "../components/SweepHeatmap";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type SelectionMode = "explicit" | "top" | "all" | "all_covered";

type FormState = {
  strategy: string;
  selectionMode: SelectionMode;
  symbols: Set<string>;
  topN: number;
  coverageMin: number;
  periods: string[];
  customPeriod: string;
  tf: string;
  wfMode: "single" | "wf4" | "off";
  costModel: "static" | "spread" | "full";
  parallel: number;
  tag: string;
};

const PERIOD_PRESETS = ["2024", "2025", "2026", "train", "holdout"];

const defaultForm: FormState = {
  strategy: "",
  selectionMode: "top",
  symbols: new Set<string>(),
  topN: 30,
  coverageMin: 0.9,
  periods: ["2024", "2025", "2026"],
  customPeriod: "",
  tf: "",
  wfMode: "single",
  costModel: "static",
  parallel: 8,
  tag: "",
};

export function Sweep() {
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [symbols, setSymbols] = useState<SymbolMeta[]>([]);
  const [form, setForm] = useState<FormState>(defaultForm);
  const [job, setJob] = useState<Job | null>(null);
  const [sweepList, setSweepList] = useState<SweepListEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sweep, setSweep] = useState<SweepPayload | null>(null);
  const [correlations, setCorrelations] = useState<SweepCorrelations | null>(null);
  const [activeCell, setActiveCell] = useState<SweepCellRow | null>(null);
  const [cellEquity, setCellEquity] = useState<SweepEquityCurve | null>(null);
  const [metric, setMetric] = useState<SweepMetric>("sharpe");
  const [error, setError] = useState<string | null>(null);
  const [symbolFilter, setSymbolFilter] = useState("");

  const pollRef = useRef<number | null>(null);

  // ---- initial data ---- //
  useEffect(() => {
    api.strategies().then(setStrategies).catch((e) => setError(String(e)));
    api.symbols().then(setSymbols).catch((e) => setError(String(e)));
  }, []);

  // ---- sweep history for selected strategy ---- //
  const refreshList = useCallback((strategy: string) => {
    if (!strategy) return;
    api.sweepList(strategy).then((rows) => {
      setSweepList(rows);
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (form.strategy) refreshList(form.strategy);
  }, [form.strategy, refreshList]);

  // ---- selected sweep payload ---- //
  useEffect(() => {
    if (!form.strategy || !selectedId) {
      setSweep(null); setCorrelations(null); return;
    }
    api.sweepGet(form.strategy, selectedId).then(setSweep)
      .catch((e) => setError(String(e)));
    api.sweepCorrelations(form.strategy, selectedId)
      .then(setCorrelations).catch(() => setCorrelations(null));
  }, [form.strategy, selectedId]);

  // ---- equity curve drawer ---- //
  useEffect(() => {
    if (!activeCell || !form.strategy || !selectedId) {
      setCellEquity(null); return;
    }
    api.sweepEquity(form.strategy, selectedId, activeCell.symbol, activeCell.period)
      .then(setCellEquity).catch(() => setCellEquity(null));
  }, [activeCell, form.strategy, selectedId]);

  // ---- job polling ---- //
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      if (job?.status === "done") {
        // Reload list — the newest entry will be the just-finished sweep.
        refreshList(form.strategy);
        api.sweepList(form.strategy).then((rows) => {
          if (rows.length > 0) setSelectedId(rows[0].sweep_id);
        });
      }
      return;
    }
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(() => {
      api.job(job.id).then(setJob).catch(() => undefined);
      // Also refresh sweep list so progress.json updates show up.
      if (form.strategy) refreshList(form.strategy);
    }, 1500);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [job, form.strategy, refreshList]);

  // ---- launch sweep ---- //
  const launch = useCallback(() => {
    setError(null);
    if (!form.strategy) {
      setError("pick a strategy first");
      return;
    }
    const body: SweepRunRequest = {
      periods: form.periods.length
        ? form.periods
        : ["2024", "2025", "2026"],
      cost_model: form.costModel,
      parallel: form.parallel,
      tag: form.tag,
      coverage_min: form.coverageMin,
    };
    if (form.tf) body.tf = form.tf;
    if (form.wfMode === "off") {
      body.no_wf = true;
    } else {
      body.wf = form.wfMode === "wf4" ? 4 : 1;
    }
    if (form.selectionMode === "explicit") {
      body.symbols = Array.from(form.symbols);
      if (!body.symbols.length) {
        setError("pick at least one symbol");
        return;
      }
    } else if (form.selectionMode === "top") {
      body.top_n = form.topN;
    } else if (form.selectionMode === "all") {
      body.all_symbols = true;
    } else if (form.selectionMode === "all_covered") {
      body.all_symbols_covered = true;
    }
    api.sweepRun(form.strategy, body).then(setJob)
      .catch((e) => setError(String(e)));
  }, [form]);

  // ---- symbol picker helpers ---- //
  const filteredSymbols = useMemo(() => {
    if (!symbolFilter) return symbols;
    const q = symbolFilter.toUpperCase();
    return symbols.filter((s) => s.symbol.includes(q));
  }, [symbols, symbolFilter]);

  const toggleSymbol = (sym: string) =>
    setForm((f) => {
      const next = new Set(f.symbols);
      if (next.has(sym)) next.delete(sym);
      else next.add(sym);
      return { ...f, symbols: next };
    });

  const togglePeriod = (p: string) =>
    setForm((f) => {
      const has = f.periods.includes(p);
      return {
        ...f,
        periods: has ? f.periods.filter((x) => x !== p) : [...f.periods, p],
      };
    });

  const addCustomPeriod = () => {
    if (!form.customPeriod.trim()) return;
    setForm((f) => ({
      ...f,
      periods: [...new Set([...f.periods, f.customPeriod.trim()])],
      customPeriod: "",
    }));
  };

  // ---- render ---- //
  const cells = sweep?.summary ?? [];
  const report = sweep?.report;

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded p-3 text-sm">
          {error}
        </div>
      )}

      {/* Launcher */}
      <div className="bg-panel border border-edge rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Run sweep</h2>
          <div className="text-xs text-slate-500">
            cross-symbol × cross-period robustness. read-only — never writes to
            history.jsonl / best.json / program.md.
          </div>
        </div>

        <div className="grid grid-cols-12 gap-3 text-sm">
          <div className="col-span-12 md:col-span-3">
            <label className="block text-xs text-slate-400 mb-1">strategy</label>
            <select
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              value={form.strategy}
              onChange={(e) => setForm((f) => ({ ...f, strategy: e.target.value }))}
            >
              <option value="">— pick —</option>
              {strategies.map((s) => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </select>
          </div>

          <div className="col-span-6 md:col-span-2">
            <label className="block text-xs text-slate-400 mb-1">tf override</label>
            <input
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              placeholder="auto (DEFAULT_TF)"
              value={form.tf}
              onChange={(e) => setForm((f) => ({ ...f, tf: e.target.value }))}
            />
          </div>
          <div className="col-span-6 md:col-span-2">
            <label className="block text-xs text-slate-400 mb-1">walk-forward</label>
            <select
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              value={form.wfMode}
              onChange={(e) => setForm((f) => ({
                ...f, wfMode: e.target.value as FormState["wfMode"],
              }))}
            >
              <option value="single">single train/OOS (wf=1)</option>
              <option value="wf4">walk-forward (wf=4)</option>
              <option value="off">no split (whole period)</option>
            </select>
          </div>
          <div className="col-span-6 md:col-span-2">
            <label className="block text-xs text-slate-400 mb-1">cost model</label>
            <select
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              value={form.costModel}
              onChange={(e) => setForm((f) => ({
                ...f, costModel: e.target.value as FormState["costModel"],
              }))}
            >
              <option value="static">static</option>
              <option value="spread">spread</option>
              <option value="full">full</option>
            </select>
          </div>
          <div className="col-span-3 md:col-span-1">
            <label className="block text-xs text-slate-400 mb-1">parallel</label>
            <input
              type="number"
              min={1}
              max={32}
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              value={form.parallel}
              onChange={(e) => setForm((f) => ({
                ...f, parallel: Number(e.target.value) || 1,
              }))}
            />
          </div>
          <div className="col-span-9 md:col-span-2">
            <label className="block text-xs text-slate-400 mb-1">tag (optional)</label>
            <input
              className="w-full bg-bg border border-edge rounded px-2 py-1 font-mono"
              placeholder="robustness-v1"
              value={form.tag}
              onChange={(e) => setForm((f) => ({ ...f, tag: e.target.value }))}
            />
          </div>
        </div>

        {/* Symbol selection */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs text-slate-400">symbols</span>
            {(["top", "all_covered", "all", "explicit"] as SelectionMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setForm((f) => ({ ...f, selectionMode: m }))}
                className={`px-2 py-0.5 text-xs rounded border font-mono
                  ${form.selectionMode === m
                    ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                    : "border-edge text-slate-400 hover:text-slate-200"}`}
              >
                {m === "top" ? "top-N volume"
                  : m === "all_covered" ? "all (covered)"
                  : m === "all" ? `all (${symbols.length})`
                  : "pick manually"}
              </button>
            ))}
          </div>
          {form.selectionMode === "top" && (
            <div className="flex items-center gap-3">
              <label className="text-xs text-slate-400">N</label>
              <input
                type="number"
                min={1}
                max={symbols.length}
                className="bg-bg border border-edge rounded px-2 py-1 text-sm font-mono w-24"
                value={form.topN}
                onChange={(e) => setForm((f) => ({
                  ...f, topN: Number(e.target.value) || 30,
                }))}
              />
              <span className="text-xs text-slate-500">
                Ranked by quote volume across all selected periods.
              </span>
            </div>
          )}
          {form.selectionMode === "all_covered" && (
            <div className="flex items-center gap-3">
              <label className="text-xs text-slate-400">min coverage</label>
              <input
                type="number"
                step={0.05}
                min={0.5}
                max={1.0}
                className="bg-bg border border-edge rounded px-2 py-1 text-sm font-mono w-24"
                value={form.coverageMin}
                onChange={(e) => setForm((f) => ({
                  ...f, coverageMin: Number(e.target.value) || 0.9,
                }))}
              />
              <span className="text-xs text-slate-500">
                Fraction of bars present over the encompassing period.
              </span>
            </div>
          )}
          {form.selectionMode === "all" && (
            <div className="text-xs text-slate-500">
              All {symbols.length} symbols on disk. May include illiquid /
              short-history listings — prefer "all (covered)" for a clean run.
            </div>
          )}
          {form.selectionMode === "explicit" && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <input
                  className="bg-bg border border-edge rounded px-2 py-1 text-sm font-mono flex-1 max-w-xs"
                  placeholder="filter symbols (e.g. BTC)"
                  value={symbolFilter}
                  onChange={(e) => setSymbolFilter(e.target.value)}
                />
                <span className="text-xs text-slate-500">
                  {form.symbols.size} / {symbols.length} selected
                </span>
                {form.symbols.size > 0 && (
                  <button
                    onClick={() => setForm((f) => ({
                      ...f, symbols: new Set<string>(),
                    }))}
                    className="text-xs text-slate-400 hover:text-rose-400"
                  >
                    clear
                  </button>
                )}
              </div>
              <div className="max-h-40 overflow-y-auto border border-edge rounded p-2 flex flex-wrap gap-1">
                {filteredSymbols.slice(0, 400).map((s) => {
                  const sel = form.symbols.has(s.symbol);
                  return (
                    <button
                      key={s.symbol}
                      onClick={() => toggleSymbol(s.symbol)}
                      className={`text-xs px-2 py-0.5 rounded font-mono border
                        ${sel
                          ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                          : "border-edge text-slate-400 hover:text-slate-200"}`}
                    >
                      {s.symbol}
                    </button>
                  );
                })}
                {filteredSymbols.length > 400 && (
                  <span className="text-xs text-slate-500 self-center">
                    +{filteredSymbols.length - 400} more — refine filter
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Periods */}
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-xs text-slate-400">periods</span>
            {PERIOD_PRESETS.map((p) => {
              const sel = form.periods.includes(p);
              return (
                <button
                  key={p}
                  onClick={() => togglePeriod(p)}
                  className={`px-2 py-0.5 text-xs rounded border font-mono
                    ${sel
                      ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                      : "border-edge text-slate-400 hover:text-slate-200"}`}
                >
                  {p}
                </button>
              );
            })}
            {form.periods.filter((p) => !PERIOD_PRESETS.includes(p)).map((p) => (
              <button
                key={p}
                onClick={() => togglePeriod(p)}
                className="px-2 py-0.5 text-xs rounded border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 font-mono"
                title="remove"
              >
                {p} ×
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input
              className="bg-bg border border-edge rounded px-2 py-1 text-sm font-mono w-56"
              placeholder="custom: 2024-06:2025-06"
              value={form.customPeriod}
              onChange={(e) => setForm((f) => ({
                ...f, customPeriod: e.target.value,
              }))}
              onKeyDown={(e) => e.key === "Enter" && addCustomPeriod()}
            />
            <button
              onClick={addCustomPeriod}
              className="text-xs px-2 py-1 border border-edge rounded text-slate-300 hover:text-emerald-300"
            >
              add
            </button>
          </div>
        </div>

        {/* Launch */}
        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={launch}
            disabled={!form.strategy || job?.status === "running"}
            className="px-3 py-1.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {job?.status === "running" ? "running…" : "Run sweep"}
          </button>
          {job && (
            <div className="text-xs text-slate-400 font-mono">
              job <span className="text-slate-200">{job.id}</span> ·
              status <span className="text-slate-200">{job.status}</span>
              {job.exit_code !== null && (
                <> · exit <span className="text-slate-200">{job.exit_code}</span></>
              )}
            </div>
          )}
        </div>

        {job && job.tail.length > 0 && (
          <details className="text-xs text-slate-400 font-mono">
            <summary className="cursor-pointer hover:text-slate-200">
              job tail ({job.tail.length} lines)
            </summary>
            <pre className="mt-2 max-h-48 overflow-y-auto bg-bg/40 border border-edge rounded p-2 whitespace-pre-wrap break-all">
              {job.tail.slice(-40).join("\n")}
            </pre>
          </details>
        )}
      </div>

      {/* Sweep history */}
      {form.strategy && (
        <div className="bg-panel border border-edge rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold">Sweep history — {form.strategy}</h3>
            <button
              onClick={() => refreshList(form.strategy)}
              className="text-xs text-slate-400 hover:text-emerald-300"
            >
              refresh
            </button>
          </div>
          {sweepList.length === 0 ? (
            <div className="text-sm text-slate-500">
              No sweeps yet for this strategy.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-slate-400">
                  <tr className="border-b border-edge">
                    <th className="text-left px-2 py-1">sweep_id</th>
                    <th className="text-left px-2 py-1">tag</th>
                    <th className="text-right px-2 py-1">cells</th>
                    <th className="text-right px-2 py-1">errors</th>
                    <th className="text-right px-2 py-1">median Sharpe</th>
                    <th className="text-right px-2 py-1">% positive</th>
                    <th className="text-right px-2 py-1">duration</th>
                    <th className="text-left px-2 py-1">created</th>
                  </tr>
                </thead>
                <tbody>
                  {sweepList.map((s) => (
                    <tr
                      key={s.sweep_id}
                      className={`border-b border-edge/40 cursor-pointer
                        ${selectedId === s.sweep_id
                          ? "bg-emerald-500/10"
                          : "hover:bg-bg/30"}`}
                      onClick={() => setSelectedId(s.sweep_id)}
                    >
                      <td className="px-2 py-1 font-mono text-slate-200">{s.sweep_id}</td>
                      <td className="px-2 py-1 font-mono text-slate-400">{s.tag || "—"}</td>
                      <td className="px-2 py-1 text-right font-mono">{s.n_cells ?? "—"}</td>
                      <td className={`px-2 py-1 text-right font-mono
                        ${(s.n_errors ?? 0) > 0 ? "text-amber-400" : "text-slate-500"}`}>
                        {s.n_errors ?? "—"}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">{fmt(s.global?.median_sharpe ?? null, 2)}</td>
                      <td className="px-2 py-1 text-right font-mono">{fmtPct(s.global?.pct_sharpe_positive ?? null, 0)}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-400">
                        {s.duration_s !== null ? `${s.duration_s}s` : "—"}
                      </td>
                      <td className="px-2 py-1 text-slate-500 font-mono">
                        {s.created_at?.slice(0, 19).replace("T", " ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Sweep result */}
      {sweep && (
        <div className="space-y-4">
          {/* Header */}
          <div className="bg-panel border border-edge rounded-lg p-4">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h3 className="font-semibold">{sweep.manifest.sweep_id}</h3>
                <div className="text-xs text-slate-400 font-mono mt-1">
                  {sweep.manifest.n_cells ?? "?"} cells · {sweep.manifest.n_errors ?? "?"} errors ·
                  {" tf="}{sweep.manifest.tf} ·
                  {" wf="}{sweep.manifest.no_wf ? "off" : sweep.manifest.walk_windows} ·
                  {" cost="}{sweep.manifest.cost_model} ·
                  {" "}{sweep.manifest.symbols.length} symbols ×
                  {" "}{sweep.manifest.periods.length} periods
                </div>
              </div>
              {report && (
                <div className="text-xs text-slate-400 font-mono">
                  median Sharpe <span className="text-slate-200">{fmt(report.global.median_sharpe, 2)}</span>
                  {" · "}
                  pct positive <span className="text-slate-200">{fmtPct(report.global.pct_sharpe_positive, 0)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Per-period breadth */}
          {report && (
            <div className="bg-panel border border-edge rounded-lg p-4">
              <h3 className="font-semibold mb-2">Breadth per period</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-slate-400">
                    <tr className="border-b border-edge">
                      <th className="text-left px-2 py-1">period</th>
                      <th className="text-right px-2 py-1">n_cells</th>
                      <th className="text-right px-2 py-1">% Sharpe&gt;0</th>
                      <th className="text-right px-2 py-1">% Return&gt;0</th>
                      <th className="text-right px-2 py-1">median Sharpe</th>
                      <th className="text-right px-2 py-1">IQR Sharpe</th>
                      <th className="text-right px-2 py-1">median MaxDD</th>
                      <th className="text-right px-2 py-1">median Return</th>
                      <th className="text-left px-2 py-1">top 3</th>
                      <th className="text-left px-2 py-1">bottom 3</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.per_period.map((p) => (
                      <tr key={p.period} className="border-b border-edge/40 font-mono">
                        <td className="px-2 py-1 text-slate-200">{p.period}</td>
                        <td className="px-2 py-1 text-right">{p.n_cells_ok}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(p.pct_sharpe_positive, 0)}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(p.pct_return_positive, 0)}</td>
                        <td className="px-2 py-1 text-right">{fmt(p.median_sharpe, 2)}</td>
                        <td className="px-2 py-1 text-right text-slate-400">{fmt(p.iqr_sharpe, 2)}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(p.median_max_dd, 1)}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(p.median_total_return, 1)}</td>
                        <td className="px-2 py-1 text-emerald-300/80">
                          {p.top.slice(0, 3).map((t) => `${t.symbol}(${fmt(t.sharpe, 2)})`).join(" ")}
                        </td>
                        <td className="px-2 py-1 text-rose-300/80">
                          {p.bottom.slice(0, 3).map((t) => `${t.symbol}(${fmt(t.sharpe, 2)})`).join(" ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Heatmap */}
          <div className="bg-panel border border-edge rounded-lg p-4">
            <h3 className="font-semibold mb-2">Heatmap</h3>
            <SweepHeatmap
              manifest={sweep.manifest}
              rows={cells}
              metric={metric}
              onMetricChange={setMetric}
              onCellClick={setActiveCell}
            />
            <div className="text-xs text-slate-500 mt-2">
              Click a cell to load its equity curve. Striped cells errored.
            </div>
          </div>

          {/* Cell drawer */}
          {activeCell && (
            <div className="bg-panel border border-edge rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">
                  <span className="font-mono">{activeCell.symbol}</span>
                  {" · "}
                  <span className="font-mono">{activeCell.period}</span>
                </h3>
                <button
                  onClick={() => { setActiveCell(null); setCellEquity(null); }}
                  className="text-xs text-slate-400 hover:text-rose-400"
                >
                  close
                </button>
              </div>
              {activeCell.error ? (
                <div className="text-rose-400 font-mono text-xs whitespace-pre-wrap">
                  {activeCell.error}
                </div>
              ) : (
                <>
                  <div className="text-xs font-mono text-slate-400 grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1">
                    <div>Sharpe <span className="text-slate-200">{fmt(activeCell.sharpe, 3)}</span></div>
                    <div>MaxDD <span className="text-slate-200">{fmtPct(activeCell.max_dd, 2)}</span></div>
                    <div>TotalRet <span className="text-slate-200">{fmtPct(activeCell.total_return, 2)}</span></div>
                    <div>n_trades <span className="text-slate-200">{activeCell.n_trades ?? "—"}</span></div>
                    <div>TiP <span className="text-slate-200">{fmtPct(activeCell.pct_time_in_position, 1)}</span></div>
                    <div>Profit factor <span className="text-slate-200">{fmt(activeCell.profit_factor, 2)}</span></div>
                    <div>Expectancy <span className="text-slate-200">{fmt(activeCell.expectancy, 4)}</span></div>
                    <div>InfoRatio <span className="text-slate-200">{fmt(activeCell.information_ratio, 2)}</span></div>
                    <div>CVaR-95 <span className="text-slate-200">{fmtPct(activeCell.cvar_95, 2)}</span></div>
                    <div>max participation <span className="text-slate-200">{fmt(activeCell.max_participation_pct, 2)}%</span></div>
                    <div>train Sharpe <span className="text-slate-200">{fmt(activeCell.train_sharpe, 2)}</span></div>
                    <div>duration <span className="text-slate-200">{activeCell.duration_s}s</span></div>
                  </div>
                  {cellEquity && cellEquity.timestamp.length > 0 && (
                    <Plot
                      data={[
                        {
                          x: cellEquity.timestamp, y: cellEquity.equity,
                          type: "scatter", mode: "lines", name: "equity",
                          line: { color: "#34d399", width: 1.5 },
                        },
                        {
                          x: cellEquity.timestamp, y: cellEquity.benchmark,
                          type: "scatter", mode: "lines", name: "benchmark",
                          line: { color: "#64748b", width: 1, dash: "dot" },
                        },
                      ]}
                      layout={{
                        autosize: true, height: 280,
                        margin: { l: 50, r: 20, t: 10, b: 30 },
                        paper_bgcolor: "rgba(0,0,0,0)",
                        plot_bgcolor: "rgba(0,0,0,0)",
                        font: { color: "#94a3b8", size: 10 },
                        xaxis: { gridcolor: "rgba(148,163,184,0.1)" },
                        yaxis: { gridcolor: "rgba(148,163,184,0.1)" },
                        legend: { orientation: "h", y: -0.15 },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  )}
                </>
              )}
            </div>
          )}

          {/* Per-symbol ranking */}
          {report && report.per_symbol.length > 0 && (
            <div className="bg-panel border border-edge rounded-lg p-4">
              <h3 className="font-semibold mb-2">Per-symbol stability</h3>
              <div className="overflow-x-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="text-slate-400 sticky top-0 bg-panel">
                    <tr className="border-b border-edge">
                      <th className="text-left px-2 py-1">symbol</th>
                      <th className="text-right px-2 py-1">n_periods</th>
                      <th className="text-right px-2 py-1">% positive periods</th>
                      <th className="text-right px-2 py-1">mean Sharpe</th>
                      <th className="text-right px-2 py-1">min</th>
                      <th className="text-right px-2 py-1">max</th>
                      <th className="text-right px-2 py-1">mean Return</th>
                      <th className="text-right px-2 py-1">worst MaxDD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.per_symbol.map((r) => (
                      <tr key={r.symbol} className="border-b border-edge/40 font-mono">
                        <td className="px-2 py-1 text-slate-200">{r.symbol}</td>
                        <td className="px-2 py-1 text-right">{r.n_periods}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(r.pct_positive_periods, 0)}</td>
                        <td className="px-2 py-1 text-right">{fmt(r.mean_sharpe, 2)}</td>
                        <td className="px-2 py-1 text-right text-slate-400">{fmt(r.min_sharpe, 2)}</td>
                        <td className="px-2 py-1 text-right text-slate-400">{fmt(r.max_sharpe, 2)}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(r.mean_total_return, 1)}</td>
                        <td className="px-2 py-1 text-right">{fmtPct(r.worst_max_dd, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Correlations */}
          {correlations && correlations.symbols.length >= 2 && (
            <details className="bg-panel border border-edge rounded-lg p-4">
              <summary className="cursor-pointer font-semibold">
                OOS-returns correlation matrix ({correlations.symbols.length} symbols)
              </summary>
              <div className="mt-3">
                <Plot
                  data={[{
                    z: correlations.matrix,
                    x: correlations.symbols,
                    y: correlations.symbols,
                    type: "heatmap",
                    colorscale: [
                      [0, "#dc2626"], [0.5, "#1f2937"], [1, "#10b981"],
                    ],
                    zmin: -1, zmax: 1,
                    showscale: true,
                  }]}
                  layout={{
                    autosize: true,
                    height: Math.max(300, Math.min(900, 20 + correlations.symbols.length * 16)),
                    margin: { l: 100, r: 20, t: 10, b: 100 },
                    paper_bgcolor: "rgba(0,0,0,0)",
                    plot_bgcolor: "rgba(0,0,0,0)",
                    font: { color: "#94a3b8", size: 9 },
                    xaxis: { tickangle: -60 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
                <div className="text-xs text-slate-500 mt-2">
                  Correlation of OOS bar returns across symbols. High global
                  correlation = strategy is effectively trading one beta (often BTC).
                </div>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
