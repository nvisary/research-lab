# Claude Code — research agent for `researchlab`

You're working in `researchlab`, a crypto-strategy auto-research framework.
This file is the **operator-level briefing** Claude Code reads automatically
when invoked from the project root. The contract, hard rules, scoring,
techniques, and workflow are documented in:

- [`AGENTS.md`](AGENTS.md) — the primary briefing. Read this first, in full.
- [`METHODS.md`](METHODS.md) — vocabulary of strategy improvement techniques.
- [`README.md`](README.md) — project overview, quickstart, known limitations.

## Default loop

When the user says "iterate on `<strategy_name>`" or similar:

1. **Read state.** `strategies/<name>/program.md`, `strategies/<name>/strategy.py`,
   `strategies/<name>/runs/best.json`, last ~20 entries of `runs/history.jsonl`.
2. **Form one hypothesis.** State it in one sentence. Reference what it's
   responding to in the history (avoid repeating refuted ideas).
3. **Edit `strategies/<name>/strategy.py` only.** One change at a time —
   never multi-change unless the user explicitly says so. Use
   `harness.utils.resample_higher` for any multi-TF logic.
4. **Run:** `uv run python -m runner.iterate strategies/<name> --note "<one-sentence hypothesis>"`
5. **Read the verdict JSON.** Report `composite`, OOS sharpe, max DD,
   trades, DSR, and what changed since the previous best.
6. **Decide.** If verdict is `KEEP` or `BASELINE`, propose the next
   hypothesis in light of the new shape. If `REVERT`, the file has been
   restored — do NOT re-edit on top, re-read first. If `LOOKAHEAD_BUG`,
   read the audit message and fix the leak in your next attempt.
7. **Stop conditions.** No improvement in 10–20 iterations on the same
   hypothesis-family — write a paragraph in `program.md` summarizing what
   was ruled out, and ask the user for a new direction.

## Rules you cannot break

- Edit only `strategies/<name>/strategy.py` and `strategies/<name>/program.md`.
- Never touch `harness/`, `runner/`, `datafeed/`, `web/`, `frontend/`, `tests/`.
  If a harness bug seems likely, **report it to the user**, don't patch it.
- Never look at the holdout (`strategies/<name>/runs/holdout/`) or run
  `runner.holdout` during iteration. The user calls it manually as a
  one-shot sanity check after iteration is complete.
- Never bypass the lookahead audit. If you see `LOOKAHEAD_BUG`, fix the
  shift, don't disable the check.

## Useful commands

```bash
# one iteration with audit + WF=4 + funding-adjusted equity
uv run python -m runner.iterate strategies/<name> --note "<hypothesis>"

# manual holdout (USER triggers this, not the agent during iteration)
uv run python -m runner.holdout strategies/<name>

# standalone audit (cheap; useful after big edits)
uv run python -m runner.audit strategies/<name>

# quick one-shot backtest without keep/revert / no history append
uv run python -m harness.backtest strategies/<name>
```

## Reporting format

After each iteration, give the user a 3-line summary:

```
iter N: <verdict> — composite <C> (Δ vs prior best <ΔC>, DSR <D>)
hypothesis: <one sentence stating what was tested>
next: <one sentence proposing what to try next, or "stuck — please pick">
```

Don't bury the verdict in prose. The user is reading dozens of these.
