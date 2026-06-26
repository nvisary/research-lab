# pump_fade_xs — cross-sectional pump fade

## Origin

Ported from `notebooks/pump/` manual research (see `notebooks/pump/HOW_WE_WORK.md`).
The notebook study built a pump detector, established that price **fades** after
a pump (not continues), and converged on a tradeable rule, validated on three
independent periods (2024 H2, 2025, 2026) and under a realistic $1000/$20
portfolio sim with funding included.

This strategy exists to **cross-check the hand-rolled notebook backtest against
the repo's independent harness engine** — to rule out an implementation error
in the manual sim. It is a faithful port, not a fresh search.

## Rule

- **Detector (per symbol, 1m):** cumulative return over 15m > 5% AND
  15m summed volume > 3x (rolling-240m median minute volume × 15).
- **Entry:** SHORT on the bar after the trigger (one-bar execution lag).
- **Exit:** 3% close-based stop, else 4h (240-bar) time-exit.
- **Sizing:** RAW, 2% of equity per short (mirrors $20 on $1000).
- **Cooldown:** one event per ~2h per symbol; no overlapping entry per symbol.

## Notebook reference numbers (per-trade, pooled, NET of fees+funding)

| period | n     | mean/trade | win% | portfolio ($1000/$20) |
|--------|-------|-----------|------|------------------------|
| 2024 H2| 766   | ~+1.4%    | 56%  | —                      |
| 2025   | 2178  | +1.46%    | 56%  | +74% / MTM DD −9.3%    |
| 2026   | 971   | +1.78%    | 52%  | +43% (4mo)             |

Note: memecoins (1000PEPE/BONK/…) are NOT in this universe — their data ends
2025-09 and the harness period runs to 2026-01. The 29-symbol set is liquid
alts with full 2024-01..2026-01 coverage. Edge was not memecoin-exclusive in
the notebooks, but expect somewhat fewer/weaker events here.

## Iterations

| iter | verdict | composite | OOS sharpe | note |
|------|---------|-----------|-----------|------|
| 1 | KEEP (baseline) | -4.38 | -2.28 | port of notebook rule; DSR 0.08, PF 0.85, neg total return train+oos |

## Verdict on the hypothesis family — REFUTED (lookahead in the anchor)

The harness was right all along; the notebook edge was a lookahead artifact.

The pump condition (+5%/15m AND vol>3x) is true at MANY consecutive minutes. The
notebook dedup anchored each event at the minute with the **maximum 15-min
return** — which is ≈ the local top, and can only be identified AFTER the cluster
ends. That is forward-looking SELECTION. A real bot (and the harness, and the
live `pump_fade_bot`) enters at the **first** trigger.

Measured on 2025 clusters (3% stop, 4h exit) — see `notebooks/pump/09_3`:
- FIRST trigger (realistic):   **−0.53%/trade, win 37%**  → LOSES
- MAX-RET trigger (notebook):  +1.67%/trade, win 57%      → hindsight
- ACTUAL PEAK (oracle):        +2.25%/trade, win 63%

After the first signal the pump keeps running **+2.74% on average** (median 6,
mean 11 more minutes) before topping — so a short on the first signal is run over.
This is the real reason the harness (which enters at the first trigger) was
negative (OOS Sharpe −2.28, PF 0.85). The earlier "1-min fill timing" /
next-bar-open analyses were measured INSIDE the tainted max-ret anchor, so their
"edge survives" conclusion was illusory.

**Status: NOT a tradeable edge as specified.** Shorting the first pump detection
loses. The live paper bot trades this losing version — its PnL must not be read as
validation.

Methodological lesson: the deadliest lookahead is in event/anchor SELECTION, not
just `.shift()`. Choosing "the best point in a cluster" by an outcome-correlated
key (max return) needs the future even when each point's value is causal. Always
score an event strategy with the FIRST realizable trigger, never an aggregate-
selected one.

**Possible salvage (untested):** a non-lookahead EXHAUSTION entry — short only
after the pump stops making new highs (momentum rollover), not on the first trip.

## What to watch

- This is a **sparse, short-only** strategy → `oos_pct_time_in_position` will be
  low and the composite's `time_in_position_penalty` will bite. The cross-check
  target is the **raw OOS Sharpe / total return / equity shape / trade count**,
  not necessarily a KEEP verdict. If the harness shows a positive OOS Sharpe and
  positive total return shorting these pumps, the notebook implementation is
  corroborated.
