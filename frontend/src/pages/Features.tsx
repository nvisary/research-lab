/**
 * Features page — browse the feature store, preview values, check cache.
 *
 * Features are reusable, lookahead-safe time series computed from
 * OHLCV (and funding-rate, where available). They power the
 * meta-labeler and can be referenced by name from a strategy's
 * ``META_LABELER`` spec.
 */
import { useEffect, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import {
  api,
  type FeatureCoverageRow,
  type FeatureMeta,
  type FeaturePreview,
} from "../api";
import { fmt } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

export function Features() {
  const [features, setFeatures] = useState<FeatureMeta[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<FeaturePreview | null>(null);
  const [coverage, setCoverage] = useState<FeatureCoverageRow[]>([]);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [start, setStart] = useState("2025-06-01");
  const [end, setEnd] = useState("2025-07-01");
  const [tf, setTf] = useState("1h");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.featuresList()
      .then((f) => {
        setFeatures(f);
        if (f.length > 0) setSelected(f[0].name);
      })
      .catch((e) => setError(String(e)));
  }, []);

  async function loadPreview(name: string) {
    setLoading(true);
    setError(null);
    setPreview(null);
    setCoverage([]);
    try {
      const [pv, cov] = await Promise.all([
        api.featurePreview(name, symbol, start, end, tf),
        api.featureCoverage(name),
      ]);
      setPreview(pv);
      setCoverage(cov);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (selected) loadPreview(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const selectedMeta = features.find((f) => f.name === selected);

  return (
    <div className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-4">
      {/* Left rail: feature list */}
      <div className="bg-panel border border-edge rounded-lg p-3 h-fit">
        <h3 className="font-semibold mb-2">Features</h3>
        <ul className="space-y-1 text-sm">
          {features.map((f) => (
            <li key={f.name}>
              <button
                onClick={() => setSelected(f.name)}
                className={`w-full text-left px-2 py-1 rounded font-mono text-xs ${
                  selected === f.name
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "hover:bg-bg/40 text-slate-300"
                }`}
              >
                {f.name}
              </button>
            </li>
          ))}
        </ul>
        {features.length === 0 && !error && (
          <div className="text-xs text-slate-500">loading…</div>
        )}
      </div>

      {/* Right: detail panel */}
      <div className="space-y-4">
        {error && (
          <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded p-3 text-sm">
            {error}
          </div>
        )}

        {selectedMeta && (
          <div className="bg-panel border border-edge rounded-lg p-4">
            <h2 className="font-semibold font-mono">{selectedMeta.name}</h2>
            <p className="text-sm text-slate-300 mt-1">{selectedMeta.description}</p>
            <div className="text-xs text-slate-500 mt-2">
              deps: <span className="font-mono">{selectedMeta.deps.join(", ") || "—"}</span>
              {" · "}lookback: <span className="font-mono">{selectedMeta.lookback}</span>
              {" · "}source: <span className="font-mono">
                {selectedMeta.source_file?.split(/[\\/]/).pop()}
                {selectedMeta.source_line ? `:${selectedMeta.source_line}` : ""}
              </span>
            </div>
          </div>
        )}

        <div className="bg-panel border border-edge rounded-lg p-4">
          <h3 className="font-semibold mb-2">Preview</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 text-sm">
            <Field label="symbol">
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="bg-bg border border-edge rounded px-2 py-1 w-full font-mono"
              />
            </Field>
            <Field label="start">
              <input
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className="bg-bg border border-edge rounded px-2 py-1 w-full font-mono"
              />
            </Field>
            <Field label="end">
              <input
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className="bg-bg border border-edge rounded px-2 py-1 w-full font-mono"
              />
            </Field>
            <Field label="tf">
              <input
                value={tf}
                onChange={(e) => setTf(e.target.value)}
                className="bg-bg border border-edge rounded px-2 py-1 w-full font-mono"
              />
            </Field>
            <div className="flex items-end">
              <button
                onClick={() => selected && loadPreview(selected)}
                disabled={loading || !selected}
                className="bg-emerald-500/20 hover:bg-emerald-500/30 disabled:opacity-50 text-emerald-300 border border-emerald-500/40 rounded px-3 py-1.5 text-sm w-full"
              >
                {loading ? "loading…" : "compute"}
              </button>
            </div>
          </div>

          {preview && preview.n_points > 0 && (
            <>
              <Plot
                data={[{
                  x: preview.timestamp,
                  y: preview.values,
                  type: "scatter", mode: "lines",
                  line: { color: "#60a5fa", width: 1 },
                  name: preview.name,
                }] as any}
                layout={{
                  autosize: true, height: 280,
                  margin: { l: 60, r: 20, t: 10, b: 40 },
                  paper_bgcolor: "transparent",
                  plot_bgcolor: "transparent",
                  font: { color: "#cbd5e1", size: 11 },
                  xaxis: { gridcolor: "#1e293b" },
                  yaxis: { gridcolor: "#1e293b" },
                } as any}
                config={{ displayModeBar: false, responsive: true } as any}
                style={{ width: "100%" }}
              />
              <div className="grid grid-cols-5 gap-2 mt-2 text-xs">
                {["p05", "p25", "p50", "p75", "p95"].map((label, i) => (
                  <div key={label} className="text-center bg-bg/40 border border-edge rounded p-1.5">
                    <div className="text-slate-500">{label}</div>
                    <div className="font-mono text-slate-200">
                      {fmt(preview.quantiles_05_25_50_75_95[i], 4)}
                    </div>
                  </div>
                ))}
              </div>
              <div className="text-xs text-slate-500 mt-2">
                {preview.n_points} points · downsampled if &gt;2000.
              </div>
            </>
          )}
          {preview && preview.n_points === 0 && (
            <div className="text-sm text-amber-400">
              No data — symbol/period combination yields empty series.
            </div>
          )}
        </div>

        <div className="bg-panel border border-edge rounded-lg p-4">
          <h3 className="font-semibold mb-2">Cache coverage</h3>
          {coverage.length === 0 ? (
            <div className="text-xs text-slate-500">
              No cache yet for this feature. Caches build automatically when a
              preview or a meta-labeled iteration is run.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-slate-400 text-xs">
                  <tr className="border-b border-edge">
                    <th className="text-left px-2 py-1">symbol</th>
                    <th className="text-left px-2 py-1">tf</th>
                    <th className="text-right px-2 py-1">n_months</th>
                    <th className="text-left px-2 py-1">first</th>
                    <th className="text-left px-2 py-1">last</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.map((row, i) => (
                    <tr key={i} className="border-b border-edge/40">
                      <td className="px-2 py-1 font-mono">{row.symbol}</td>
                      <td className="px-2 py-1 font-mono">{row.tf}</td>
                      <td className="px-2 py-1 text-right">{row.n_months}</td>
                      <td className="px-2 py-1 font-mono text-slate-400">{row.first}</td>
                      <td className="px-2 py-1 font-mono text-slate-400">{row.last}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      {children}
    </label>
  );
}
