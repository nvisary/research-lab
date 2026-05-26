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
2. **Form one hypothesis.** It must be explainable in one sentence and tied
   to current diagnostics or prior failures.
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

## Holdout discipline

Do not run `runner.holdout` during iteration and do not inspect
`strategies/<name>/runs/holdout/` as part of the optimization loop. Holdout is
for the user to trigger manually after research is complete.

## Useful commands

```bash
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
