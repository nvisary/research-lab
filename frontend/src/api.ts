export type StrategySummary = {
  name: string;
  best_composite: number | null;
  best_iter: number | null;
  n_iters: number;
};

export type Metrics = {
  sharpe?: number;
  sortino?: number;
  calmar?: number;
  max_dd?: number;
  cagr?: number;
  total_return?: number;
  turnover?: number;
  hit_rate?: number;
  n_trades?: number;
  n_periods?: number;
};

export type HistoryRow = {
  iter: number;
  verdict: "KEEP" | "REVERT" | "BASELINE" | "ERROR" | string;
  composite: number | null;
  best_before: number | null;
  params: Record<string, unknown> | null;
  metrics_oos: Metrics;
  metrics_train: Metrics;
  wf_aggregate: WfAggregate;
  note: string;
  finished: string;
  error: string | null;
};

export type Best = {
  iter: number;
  composite: number;
  params: Record<string, unknown>;
  symbols: string[];
  tf: string;
  period: [string, string];
  metrics: { train: Metrics; oos: Metrics };
  wf_aggregate: WfAggregate;
  note: string;
  saved_at: string;
} | null;

export type StrategyDetail = {
  name: string;
  best: Best;
  history: HistoryRow[];
  program_md: string;
  strategy_py: string;
};

export type EquityWindow = {
  window: number;
  timestamp: string[];
  equity: number[];
  benchmark: number[];
  split_cutoff: string | null;
};

export type EquityCurve = {
  iter: number;
  windows: EquityWindow[];
  n_windows: number;
  // legacy fields mirror the first window for back-compat
  timestamp: string[];
  equity: number[];
  benchmark: number[];
  split_cutoff: string | null;
};

export type WfAggregate = {
  mean_sharpe: number;
  std_sharpe: number;
  median_sharpe: number;
  mean_max_dd: number;
  worst_max_dd: number;
  mean_n_trades: number;
  n_windows: number;
  window_composites?: number[];
} | null;

export type Job = {
  id: string;
  cmd: string[];
  status: "pending" | "running" | "done" | "failed";
  created_at: string;
  finished_at: string | null;
  exit_code: number | null;
  tail: string[];
};

const j = async <T>(p: string, init?: RequestInit): Promise<T> => {
  const r = await fetch(p, init);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${p}`);
  return r.json();
};

export type HoldoutReport = {
  report: {
    iter: number;
    ran_at: string;
    period: [string, string];
    tf: string;
    symbols: string[];
    params: Record<string, unknown>;
    metrics: Metrics;
    composite: number | null;
    best_composite_train_val: number | null;
  };
  equity: { timestamp: string[]; equity: number[]; benchmark: number[] } | null;
} | null;

export const api = {
  strategies: () => j<StrategySummary[]>("/api/strategies"),
  strategy: (name: string) => j<StrategyDetail>(`/api/strategies/${name}`),
  equity: (name: string, iter: number) =>
    j<EquityCurve>(`/api/strategies/${name}/equity/${iter}`),
  iterate: (
    name: string,
    body: { start: string; end: string; tf: string; walk: number; note: string }
  ) =>
    j<Job>(`/api/strategies/${name}/iterate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  holdout: (name: string, body: { start: string; end: string; tf: string }) =>
    j<Job>(`/api/strategies/${name}/holdout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  holdoutReport: (name: string) =>
    j<HoldoutReport>(`/api/strategies/${name}/holdout`),
  job: (id: string) => j<Job>(`/api/jobs/${id}`),
};
