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

## 5. The three-layer data split (read this once, refer back as needed)

Before running the agent, you need a clear mental model of how data is
sliced. It's the single most important concept in the framework — every
metric you'll see in the dashboard depends on it.

### Why split data at all

When you backtest a strategy on historical data, the strategy gets
**fit to that data**. Parameters, indicators, filters — anything you
or the agent picked while looking at historical charts is implicitly
tuned to past patterns. That fit is not edge; it's memorization.

The classic defense is to **hide a slice from the optimizer** and only
score on that slice. If the strategy works on the hidden slice too, the
edge probably generalizes. If not, you found an overfit.

### Three layers, ranked by trustworthiness

| Layer | Period (defaults) | Who sees it | Purpose |
|---|---|---|---|
| **Train** | First 75% of each WF window | Strategy, agent, you | Indicator warmup, fitting |
| **OOS** (also called "validation") | Last 25% of each WF window | Agent, dashboard, `composite` | Validates each iteration |
| **Holdout** | 2026-01-01 → 2026-04-30 (4 mo) | **Nobody during iteration** | Final, honest sanity check |

Default `runner.iterate` covers `2024-01-01 → 2026-01-01` (24 months).
With `walk_windows=4` (also default), this is sliced into 4 windows of
~6 months each, and **inside every window** the first ~4.5 months are
train and the last ~1.5 months are OOS:

```
   w0:  Jan 2024 ──────────── Jun 2024
                  │   TRAIN   │ OOS │
                  │  ~4 mo    │~1.3m│
                              ↑
                     red dashed line in the chart
                     (75% mark = train→OOS cutoff)
```

The red dashed lines on the equity / drawdown charts mark this
**train→OOS cutoff inside every window**. They are NOT window
boundaries — those are the lighter grey vertical lines, with `w0` /
`w1` / `w2` / `w3` labels at the top of the chart.

The numbers you see in the Best card as `OOS Sharpe`, `OOS MaxDD`,
`OOS trades` are computed **only on the OOS slices**. Train numbers
(`train sharpe`, etc.) are shown for diagnostic comparison only — they
do not enter the score.

### How `composite` uses these slices

`composite` is the single number that drives keep/revert. With the
default walk-forward setup:

```
per_window_composite[i] = OOS_Sharpe[i] − 0.5·OOS_MaxDD[i] − low_trades_penalty[i]
composite                = mean(per_window_composite) − 0.5·std(per_window_composite)
```

That `−0.5·std` term penalizes strategies whose Sharpe is high in one
window and bad in others. A strategy with `mean=+0.5` but `std=2.0` is
worse than a strategy with `mean=+0.3` and `std=0.1` — the latter is
boring but consistent, and consistency is what survives forward.

### Why holdout is a separate, third layer

After 20–30 iterations the agent has seen the OOS metric **dozens of
times**. Even if the agent never opened a chart of that exact slice, it
saw it summarized in `composite`, in `history.jsonl`, in `best.json`.
Selection-bias kicks in: with N noisy attempts, the apparent best is
biased upward purely by luck.

The **Deflated Sharpe Ratio** (`DSR` column in History) tries to
correct for this — `DSR < 0.5` means "this best is most likely a
noise-fit artifact". But DSR is still computed on data the agent has
been seeing. The only fully-honest answer is: evaluate the *final*
strategy on a slice the loop has **never** touched.

That's the holdout (default `2026-01-01 → 2026-04-30`, ~4 months).
`runner.iterate` never reads it; it lives behind a separate command
(`runner.holdout`) that you run manually when you decide iteration is
done.

### Exam analogy

| | School analogy |
|---|---|
| **Train** | The textbook you studied from |
| **OOS / val** | A practice exam from the same semester. Same teacher, similar problems, but you didn't see the questions before — and you got your score back. |
| **Holdout** | The actual final exam, opened from a sealed envelope, taken once. |

- Good train, bad OOS → you memorized the textbook, didn't learn
  the subject. (overfit)
- Good OOS, bad holdout → you took 100 practice exams and the best
  one happened by chance. (selection bias — DSR ≈ how to detect it)
- Good train, OOS, AND holdout → you actually learned.

### What to look at on the dashboard

For any iter you're inspecting:

1. **`train sharpe` vs `OOS sharpe`** — gap > 1.0 is overfitting.
2. **WF OOS sharpe `mean ± std`** — if `std` is comparable to `mean`,
   one window is carrying the average. Click *per-window composite* to
   see the breakdown — `[3.98, 0.84, -0.93, -1.80]` means *one* of four
   windows did the work.
3. **`DSR`** in History — color-coded. `>0.95` is real evidence;
   `<0.5` is most-likely noise. Watch DSR fall as iteration count
   climbs even if `composite` rises — that's the selection tax.
4. **Holdout vs train+val composite** — see §7. If holdout is much
   worse, you found a noise-fit artifact, not an edge.

---

## 6. Run the LLM research loop

### 6a. Open the project under Claude Code

From the project root:

```bash
claude
```

Claude Code automatically reads `CLAUDE.md` (the operator briefing).
That file points at `AGENTS.md` (the full contract) and `METHODS.md`
(the technique catalog). On first invocation Claude Code may ask
permission to run the relevant `uv` and `python` commands — approve
them once.

### 6b. The first conversation

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

### 6c. What to watch

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

## 7. Final sanity check (holdout)

When you decide to stop iterating:

```bash
uv run python -m runner.holdout strategies/ema_pilot
```

This evaluates the current `best_strategy.py` on **2026-Q1 + Apr**
(2026-01-01 → 2026-05-01), data the iteration loop never touched. Output:

```json
{
  "iter": 12,
  "period": ["2026-01-01", "2026-05-01"],
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

## 8. Common workflows

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

## 9. Common pitfalls

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

## 10. Where to read next

- [`AGENTS.md`](AGENTS.md) — the contract. The complete operator
  manual for the LLM, but useful to skim as a human too.
- [`METHODS.md`](METHODS.md) — vocabulary of techniques: trend filters,
  vol targeting, cross-sectional ranking, PSR/DSR, costs.
- [`README.md`](README.md) — top-level overview + Known Limitations.
- [`CLAUDE.md`](CLAUDE.md) — the auto-loaded operator briefing for
  Claude Code. Other agents can be pointed at it manually.
