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
5. **Read the verdict JSON — including `diagnostics`.** Report `composite`,
   OOS sharpe, max DD, trades, DSR, **`oos_pct_time_in_position`**,
   **`oos_total_return`**, and what changed since the previous best.
   The `diagnostics` block surfaces per-window train/oos gaps, DSR trajectory,
   monthly streaks, fat-tail checks, and one-line `flags` (✓/⚠/✗/ℹ). **Always
   scan `flags` first** — they catch selection-bias, single-window-dominance,
   Sharpe-inflation-via-low-activity, and lossy-month patterns the aggregate
   metrics hide. **Sanity rule:** if `pct_time_in_position < 20%` or
   `total_return ≈ 0` while Sharpe > 1.0, treat the KEEP as suspect even if
   composite rose — you are gaming the score, not finding edge.
6. **Decide.** If verdict is `KEEP` or `BASELINE`, propose the next
   hypothesis in light of the new shape. If `REVERT`, the file has been
   restored — do NOT re-edit on top, re-read first. If `LOOKAHEAD_BUG`,
   read the audit message and fix the leak in your next attempt.
7. **Stop conditions.** No improvement in 10–20 iterations on the same
   hypothesis-family — write a paragraph in `program.md` summarizing what
   was ruled out, and ask the user for a new direction.
8. **Keep `program.md` current — hard rule.** Update it at the **end of
   every iteration batch** (5+ iters) AND at any single iter that
   produces a notable result (KEEP with breakthrough, surprising REVERT,
   newly ruled-out family). The minimum entry is one row in the iter
   table with verdict + composite + one-line note. The full "what's
   been ruled out" paragraph is required when stopping a hypothesis
   family. Never end a session leaving program.md staler than the
   running history.

## Commit rules

- **Never include a `Co-Authored-By: Claude ...` trailer** in commit
  messages. Write the message body and stop — no AI co-author line.

## Rules you cannot break

- Edit only `strategies/<name>/strategy.py` and `strategies/<name>/program.md`.
  Both are part of your workspace: `program.md` is your hypothesis log and
  "what's been ruled out" notebook — keep it current as iterations refute ideas.
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

# train-only parameter optimizer → universe of robust plateaus (METHODS.md §6.4)
# never sees OOS/holdout; read-only to the iter loop. pick a plateau center,
# set it in DEFAULT_PARAMS, then run runner.iterate to let OOS judge it.
uv run python -m runner.optimize strategies/<name> --params <p1> <p2>

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
