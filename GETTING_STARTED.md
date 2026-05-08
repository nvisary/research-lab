# Getting started with researchlab

This guide walks a human (you) from a fresh clone to a running
LLM-driven research loop. The example agent is **Claude Code** —
substitute Cursor / Aider / Copilot CLI / your tool of choice; they
all read the same `CLAUDE.md` / `AGENTS.md` operator briefing.

> **Goal of the framework.** You write a *baseline* idea (or copy
> `strategies/ema_pilot` and modify it). The LLM agent edits a single
> file (`strategies/<name>/strategy.py`) one hypothesis at a time. The
> harness backtests on real Bybit perp data, scores the result, and
> keeps or reverts the change automatically. You spectate via a web
> dashboard, kill the loop when bored, and run a final out-of-sample
> sanity check on data the loop never touched.

---

## 0. Prerequisites

You need:

- **Python 3.11+** — the project targets 3.11–3.13.
- **uv** — fast Python project manager, comes with its own venv.
  Install with `winget install astral-sh.uv` (Windows) or
  `curl -LsSf https://astral.sh/uv/install.sh | sh` (Linux/macOS).
- **Node 20+** (only if you want the web dashboard). Skip if you'll
  drive everything from the CLI.
- **Git**.
- **Claude Code** — `npm i -g @anthropic-ai/claude-code` and authenticate
  per the [official docs](https://docs.claude.com/en/docs/claude-code).
  Other agents work too — the project ships a CLAUDE.md they can read.

Disk: budget **5 GB** for the BTC-only pilot, **30+ GB** if you
download all 195 perps for 2024–2026.

---

## 1. Clone & install

```bash
git clone <your-fork-url> researchlab
cd researchlab

# Python deps + venv. Reads pyproject.toml.
uv sync

# Frontend (optional — only if you want the dashboard).
cd frontend && npm install && cd ..
```

Verify:

```bash
uv run python -c "import vectorbt, pandas, ccxt; print('ok')"
```

If you'd rather keep raw market data outside the repo (faster disk,
shared mount), set:

```bash
export RESEARCHLAB_DATA_ROOT=/path/to/data    # macOS/Linux
$env:RESEARCHLAB_DATA_ROOT = "D:\researchlab" # Windows PowerShell
```

---

## 2. Download initial data

For a first run, BTC alone is enough:

```bash
# Hourly OHLCV → data/bybit/perp/1m/BTCUSDT/<YYYY-MM>.parquet (1m source,
# resampled to whatever TF the strategy declares).
uv run python -m datafeed.download_bybit \
    --symbol BTCUSDT --start 2024-01 --end 2026-04

# Funding-rate history (the harness automatically subtracts these from
# equity if the parquets are present; without them, your perp backtest
# silently ignores ~7%/yr of long-bias drag).
uv run python -m datafeed.download_bybit_funding \
    --symbol BTCUSDT --start 2024-01 --end 2026-04
```

Each takes a few minutes. The downloaders are **idempotent** — interrupt
freely, re-run picks up where it left off.

When you're ready for multi-symbol research, pull the survivor universe:

```bash
# 195 perps that existed before 2024-01 (no survivorship-mid-period bias)
uv run python -m datafeed.download_bybit --all \
    --launched-before 2024-01-01 --start 2024-01 --end 2026-04 --workers 8

uv run python -m datafeed.download_bybit_funding --all \
    --launched-before 2024-01-01 --start 2024-01 --end 2026-04 --workers 6
```

About 2–3 hours for klines, ~30 min for funding.

---

## 3. Verify the pilot runs

The repo ships a deliberately weak EMA-crossover pilot. Run one
iteration to make sure your install works end-to-end:

```bash
uv run python -m runner.iterate strategies/ema_pilot \
    --note "first run after clone"
```

Expected output (numbers may differ slightly with package versions):

```json
{
  "iter": 1,
  "verdict": "KEEP",
  "composite": -1.804,
  "best_before": null,
  "oos_sharpe": 0.0644,
  "oos_max_dd": 0.229,
  "oos_n_trades": 230,
  "dsr": 0.5545,
  "error": null
}
```

If you see this, the pipeline (data → audit → walk-forward backtest →
funding adjustment → composite scoring → keep/revert → tearsheet) all
works.

A few side-effects landed in `strategies/ema_pilot/runs/`:

- `best.json` — current champion + full metrics + env reproduction block
- `history.jsonl` — append-only log, one line per iteration
- `best_strategy.py` — frozen copy, used to revert on `REVERT` verdicts
- `equity/iter_NNNN.parquet` — equity curves (per WF window)
- `trades/iter_NNNN.parquet` — full per-trade ledger
- `tearsheets/iter_NNNN.html` — standalone HTML report
- `last_audit.json` — sha256 + audit pass/fail

Run the **golden test** to confirm metrics are stable:

```bash
uv run pytest tests/ -q
```

---

## 4. Optional: launch the dashboard

Two terminals:

```bash
# Terminal 1 — JSON API
uv run uvicorn web.app:app --port 8000

# Terminal 2 — frontend with hot reload
cd frontend && npm run dev
```

Open `http://localhost:5173/`. Click into `ema_pilot` to see equity,
drawdown, history, trades, holdout, and an HTML tearsheet you can
download as PDF (top-right button → browser "Save as PDF").

For a single-process production-style serve, `npm run build` once and
just `uvicorn web.app:app --port 8000`. The FastAPI process serves
the built bundle from `frontend/dist/` at `/`.

> The dashboard is **localhost-only**. No auth, no rate limiting; do
> not expose. See *Known limitations* in [README.md](README.md).

---

## 5. Run the LLM research loop

### 5a. Open the project under Claude Code

From the project root:

```bash
claude
```

Claude Code automatically reads `CLAUDE.md` (the operator briefing).
That file points at `AGENTS.md` (the full contract) and `METHODS.md`
(the technique catalog). On first invocation Claude Code may ask
permission to run the relevant `uv` and `python` commands — approve
them once.

### 5b. The first conversation

A useful opening prompt:

> "Read CLAUDE.md and AGENTS.md. Look at the current state of
> `strategies/ema_pilot`. Propose a single-sentence hypothesis to
> improve composite, edit `strategy.py`, run iterate, and report
> the verdict in the 3-line format from CLAUDE.md."

Claude Code will:
1. Read the briefing files and the current strategy + history.
2. Propose one hypothesis (e.g., "Add an ADX > 20 trend filter to skip
   chop where the EMA crossover whipsaws").
3. Edit `strategy.py`.
4. Run `uv run python -m runner.iterate strategies/ema_pilot --note "..."`.
5. Report:
   ```
   iter 2: KEEP — composite -1.62 (Δ +0.18, DSR 0.62)
   hypothesis: ADX > 20 trend filter to skip chop
   next: try lifting the threshold to 25 to see if signal quality
         keeps improving or starts cutting genuine entries
   ```

Continue: "OK, do the next one." Claude Code keeps iterating until you
stop it or it concludes the family is exhausted.

### 5c. What to watch

- **Composite trend.** If it climbs steadily, the agent is finding edge.
  If it plateaus for 10+ iters, the family is stuck — ask the agent to
  write a "what's been ruled out" paragraph in `program.md` and try a
  fundamentally different angle (mean-reversion → trend-following,
  fixed sizing → vol targeting, single-symbol → cross-sectional).
- **DSR vs composite.** Composite rises with selection; DSR adjusts for
  the number of trials. If composite goes up and DSR goes down, you're
  curve-fitting through luck. Treat that "best" with skepticism.
- **Sharpe gap (train vs OOS).** Open the tearsheet for the latest
  best. If train Sharpe is +3 and OOS is 0, it's overfit even if
  composite improved.
- **Equity curve shape.** A smooth diagonal line is real. A flat line
  with one big jump is one trade pretending to be a strategy.

---

## 6. Final sanity check (holdout)

When you decide to stop iterating:

```bash
uv run python -m runner.holdout strategies/ema_pilot
```

This evaluates the current `best_strategy.py` on **2025-10 → 2026-04**,
data the iteration loop never touched. Output:

```json
{
  "iter": 12,
  "period": ["2025-10-01", "2026-05-01"],
  "composite_holdout": -0.91,
  "best_composite_train_val": -0.45,
  "sharpe": -0.78,
  "max_dd": 0.31,
  "n_trades": 1100
}
```

Compare:

- **Holdout composite ≈ train+val composite, both positive** → real edge.
- **Holdout meaningfully worse** → overfit. Either accept the
  degradation as your "true" expected forward Sharpe, or roll back to
  an older best with smaller train/holdout gap.
- **Holdout much better** → noise window; don't bank on it.

The holdout result lands in `runs/holdout/holdout_iter_NNNN.{json,parquet,html}`.
The dashboard's "Holdout sanity check" card shows it next to the
training metrics.

> **Important.** Run holdout **once** per declared-final strategy. If
> a holdout result ever feeds back into your iteration decisions, it
> stops being holdout. Treat the number you see as your last
> uncorrupted view of the strategy.

---

## 7. Common workflows

### Switching the decision timeframe

Open `strategies/<name>/strategy.py`, change:

```python
DEFAULT_TF = "5min"   # was "1h"
```

Re-run iterate. The harness reads `DEFAULT_TF`, loads 5-minute bars,
audits at 5m, walk-forwards at 5m. No CLI flag needed. The agent can
do this as a hypothesis.

### Multi-timeframe strategies

Use the helper inside `generate_signals` — never naive `df.resample`:

```python
from harness.utils import resample_higher

DEFAULT_TF = "5min"

def generate_signals(data, params):
    df = data["BTCUSDT"]
    df_4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    bull_4h = df_4h["close"] > df_4h["close"].ewm(span=50).mean()
    # ...
```

The helper auto-applies `.shift(1)` on the higher TF so the audit
doesn't reject your strategy. See `METHODS.md §1.2`.

### Spawning a new strategy

```bash
cp -r strategies/ema_pilot strategies/my_idea
# edit strategies/my_idea/{strategy.py, program.md}
rm -rf strategies/my_idea/runs/*  # fresh history
uv run python -m runner.iterate strategies/my_idea --note "baseline"
```

`program.md` is your hypothesis statement and rules-of-the-game for
the agent — keep it short and current.

### Debugging a `LOOKAHEAD_BUG` verdict

```bash
# Standalone audit with full diagnostic
uv run python -m runner.audit strategies/<name>
```

Read the offending bar in the output: `signal at <ts> changed from X
to Y when bar <ts> was perturbed`. The fix is almost always a missing
`.shift(1)` somewhere in `generate_signals`.

### Inspecting trades

The dashboard's "Trades" card has summary / winners / losers / all
tabs. For programmatic access:

```python
import pandas as pd
df = pd.read_parquet("strategies/<name>/runs/trades/iter_NNNN.parquet")
print(df[df["pnl_quote"] < 0].sort_values("pnl_quote").head(10))
```

---

## 8. Common pitfalls

- **Editing `harness/` to "fix" a number.** The harness is the judge;
  changing it invalidates every prior result. If you really suspect a
  harness bug, write a failing snapshot test first, then fix.
- **Looking at the holdout during iteration.** Don't. Once seen, it's
  no longer holdout.
- **Comparing best.json across major harness changes.** Numbers shift
  when you change funding handling, walk-forward windows, or the
  composite formula. After a structural change, wipe `runs/*` and
  re-baseline. The env block in `best.json` records what produced
  each number — diff it before you panic.
- **Multi-symbol Sharpe gains from "exotic" alts.** The universe is
  survivorship-biased — only currently-listed perps are included.
  Treat the alpha skeptically until we have historical listing
  snapshots.

---

## 9. Where to read next

- [`AGENTS.md`](AGENTS.md) — the contract. The complete operator
  manual for the LLM, but useful to skim as a human too.
- [`METHODS.md`](METHODS.md) — vocabulary of techniques: trend filters,
  vol targeting, cross-sectional ranking, PSR/DSR, costs.
- [`README.md`](README.md) — top-level overview + Known Limitations.
- [`CLAUDE.md`](CLAUDE.md) — the auto-loaded operator briefing for
  Claude Code. Other agents can be pointed at it manually.
