# Codex - research agent for `researchlab`

You are working in `researchlab`, a crypto-strategy auto-research framework.
This file is the operator-level briefing for Codex when invoked from the
project root. The primary contract lives in:

- [`AGENTS.md`](AGENTS.md) - hard rules, data split, scoring, workflow.
- [`METHODS.md`](METHODS.md) - vocabulary of strategy improvement techniques.
- [`README.md`](README.md) - project overview, quickstart, limitations.

Read `AGENTS.md` first, in full. Treat it as the source of truth.

## Default research loop

When the user asks to research or iterate on a strategy:

1. **Read state.** Inspect `strategies/<name>/program.md`,
   `strategies/<name>/strategy.py`, `strategies/<name>/runs/best.json`, and
   the recent tail of `strategies/<name>/runs/history.jsonl`.
2. **Measure the data first, THEN form one hypothesis.** Do not guess a number.
   Run a train-only EDA tool (see "Quantitative EDA" below), read its one-line
   summary, and let the measurement justify your change. The hypothesis must be
   one sentence, must make a prediction, and must be tied to that measurement or
   to current diagnostics / prior failures.
3. **Edit only `strategies/<name>/strategy.py`.** One scientific change at a
   time. Do not patch `harness/`, `runner/`, `datafeed/`, `web/`, `frontend/`,
   `tests/`, or other utilities around the bot during research.
4. **Run the script, read the script output.**

   ```bash
   uv run python -m runner.iterate strategies/<name> --note "<hypothesis>"
   ```

5. **Use logs, not the UI, for research decisions.** The dashboard is fine for
   human preview, but Codex should base research conclusions on the command
   stdout/stderr and JSON files written by the runner: `best.json`,
   `history.jsonl`, `last_audit.json`, and related run artifacts.
6. **Read the verdict fully.** Always check verdict, composite, OOS Sharpe,
   OOS max drawdown, trade count, DSR, `oos_pct_time_in_position`,
   `oos_total_return`, and `diagnostics.flags`.
7. **Respect runner state.** If verdict is `REVERT`, the file has already been
   restored. Re-read `strategy.py` before making any next edit. If verdict is
   `LOOKAHEAD_BUG`, fix the leak; never disable or bypass the audit.
8. **Stop when the family is exhausted.** After 10-20 unproductive iterations
   on the same hypothesis family, summarize what was ruled out and ask the
   user for a new direction.

## File boundaries

During strategy research, Codex may edit:

- `strategies/<name>/strategy.py`
- `strategies/<name>/program.md`
- `strategies/<name>/research/*.py` (scratch EDA tools only — see below)

Codex must not edit:

- `harness/`
- `runner/`
- `datafeed/`
- `web/`
- `frontend/`
- `tests/`
- other utilities around the bot

If a harness or utility bug appears likely, report it to the user instead of
patching it.

Documentation files such as this one may be edited only when the user
explicitly asks for documentation or operator-instruction changes.

## Quantitative EDA — how to measure before you guess

This is mandatory and you must follow it literally. The point is: do NOT keep
running `runner.iterate` with random parameter changes until the numbers look
nice. That is cheating the score. Instead, measure the data first.

A "research tool" is a small function that answers one question about the data
(volatility regimes, autocorrelation, funding). It runs on the **train slice
only** — it physically cannot see OOS or holdout — so using it is always safe.

Step by step, every time you want to change a strategy:

1. See what tools exist. Run exactly:

   ```bash
   uv run python -m runner.explore --list
   ```

2. Run one tool on the strategy. Example:

   ```bash
   uv run python -m runner.explore strategies/<name> --tool return_autocorr
   ```

   It prints a one-line summary and a JSON block with `metrics`. Read both.

3. To change a tool's setting, pass `--param key=value` (repeatable), e.g.:

   ```bash
   uv run python -m runner.explore strategies/<name> --tool vol_regime_split --param vol_window=48
   ```

4. Write what you measured into `program.md`, then write ONE hypothesis that
   predicts a result. Example: "return_autocorr lag-1 acf = -0.08 (mean-reverting)
   → a mean-reversion entry should KEEP with OOS Sharpe above the current best."

5. Make the one matching edit to `strategy.py`. Then run `runner.iterate`. The
   OOS verdict either confirms or refutes your prediction. Record which in
   `program.md`.

The three tools that ship today are `return_autocorr`, `vol_regime_split`, and
`funding_corr`. Run `--list` to get the current set; do not assume.

If NO existing tool answers your question, you may write a new one. Create a file
`strategies/<name>/research/<something>.py` with EXACTLY this shape:

```python
from harness.research import research_tool, ToolMeta, ResearchResult

@research_tool(ToolMeta(
    name="my_probe",                      # unique name you will pass to --tool
    question="one line: what does this measure?",
    params={"lookback": "bars in the estimate"},
    tags=["volatility"],
))
def my_probe(data, lookback: int = 30) -> ResearchResult:
    rets = data.returns()                  # train-only; do NOT open parquet files yourself
    value = float(rets.tail(lookback).std())
    return ResearchResult(summary=f"recent vol = {value:.5f}", metrics={"vol": value})
```

Then run it like any other tool:

```bash
uv run python -m runner.explore strategies/<name> --tool my_probe --param lookback=50
```

Rules for tools you write, no exceptions:

- Get data ONLY from the `data` argument: `data.ohlcv(sym)`, `data.funding(sym)`,
  `data.returns(sym)`, `data.close(sym)`. These are already clipped to train.
- NEVER open parquet files, NEVER read `data/`, NEVER touch any holdout path.
- A tool only measures and returns numbers. It must not edit `strategy.py`, must
  not run a backtest, must not write files.

## Holdout discipline

Do not run `runner.holdout` during iteration and do not inspect
`strategies/<name>/runs/holdout/` as part of the optimization loop. Holdout is
for the user to trigger manually after research is complete.

## Useful commands

```bash
# TRAIN-ONLY quantitative EDA — measure before guessing (see section above)
uv run python -m runner.explore --list
uv run python -m runner.explore strategies/<name> --tool <tool> [--param k=v ...]

# one iteration with audit, walk-forward, funding-adjusted equity
uv run python -m runner.iterate strategies/<name> --note "<hypothesis>"

# standalone lookahead audit
uv run python -m runner.audit strategies/<name>

# one-shot backtest without keep/revert or history append
uv run python -m harness.backtest strategies/<name>

# holdout - user-triggered only, not during iteration
uv run python -m runner.holdout strategies/<name>
```

## Reporting format

After each iteration, report tersely:

```text
iter N: <verdict> - composite <C> (delta vs prior best <dC>, DSR <D>)
hypothesis: <one sentence stating what was tested>
diagnosis: <one sentence from diagnostics.flags / OOS shape>
next: <one sentence proposing the next hypothesis, or "stuck - please pick">
```

Do not bury the verdict in prose. The operator may read many iterations in a
row.

## Commit rules

Do not add AI co-author trailers to commit messages.
