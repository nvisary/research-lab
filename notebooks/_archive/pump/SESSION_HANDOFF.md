# Session handoff — pump-fade research (paste this to bootstrap a new session)

You are continuing a quant-research collaboration in `C:\projects\researchlab`.
Before doing anything, read these in order:
1. `notebooks/pump/HOW_WE_WORK.md` — the working playbook + full research journal (notebooks 00–14).
2. This file (the compressed state below).
3. `strategies/pump_fade_xs/program.md` — the harness cross-check verdict.

## Who you're working with
A **frontend developer learning quantitative finance** — smart, fast, but new to
the math and feeling some impostor syndrome (unjustified: he personally caught a
lookahead bug the model missed). He reasons mechanistically and is epistemically
skeptical — that's his superpower. Honor it.

## How we work (contract)
- **One small experiment at a time.** Explain results slowly, plain language,
  define jargon with intuition. He explicitly asked for "медленнее, менее технически".
- **Always compare to a baseline. Pool for sample size. Distrust win-rate** (look
  at median/std/q10 + mean/std). Mind ~0.1–0.2% costs. Flag lookahead + caveats.
- **Take his ideas seriously and test them honestly even if you expect failure** —
  his "scale-in" and "classify pumps" ideas were the breakthroughs. Reject bad
  ideas plainly but explain the mechanism.
- **Mechanical loop:** write/edit `.ipynb` in `notebooks/pump/` →
  `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/pump/<nb>.ipynb`
  → Read the `.ipynb` for text/tables → end every plot with `show("name")` (saves
  `_out/name.png`) and Read that PNG to see the chart. Import helper with
  `import sys; sys.path.insert(0,'.'); from _lab import *` (cwd = notebooks/pump).
  Cache heavy computes to `_out/*.npz`.
- Language: he writes Russian; reply in Russian.

## The research story (compressed)
Goal: exploit crypto pumps. Built a detector (cumulative +5%/15m AND 15m volume
> 3× rolling-median minute volume) → found price **fades** after a pump.
- Pooled across ~140 alt-perps, 3 periods (2024/2025/2026); looked like +77%/yr.
- **THE KEY LESSON (he caught it):** the headline edge was a **lookahead in the
  dedup ANCHOR** — events were anchored at each pump cluster's max-15m-return
  minute (≈ the top, knowable only in hindsight). A realistic FIRST-trigger entry
  LOSES (−0.53%/trade); after the first signal the pump runs +2.74% more before
  topping. The repo harness (`pump_fade_xs`, OOS Sharpe −2.28) was right all along.
  Deadliest lookahead lives in event/anchor SELECTION, not a single `.shift()`.
- **Salvage that works (his idea):** lookahead-free **scale-in** — add a ramped
  short tranche on EACH cluster trigger (averages entry up toward the peak), cap =
  1 position, 3% stop on avg entry (armed after the cluster stops firing), 4h exit.
  → +0.6–0.8%/trade net, positive every year.
- **Best lever (his "crazy" idea):** a **classifier** on 12 causal features at the
  trigger (r1..r30, accel, surge, vol-regime, range, dist-from-hi/lo) separates
  reverters from runners. Walk-forward (4 folds) holds; trading model>0:
  **~+1.7%/trade net, +115% vs +98% trade-all, maxDD −3.8% vs −6.3%.** Top
  features: r30/r3/r5 (run shape) + surge1 + dist-from-low.

## Current honest state of the edge
- Lookahead-free, walk-forward-validated. **~+1.7%/trade net; ~+60–68%/yr on
  $1000 with $20/trade; ~4% realized drawdown** (MTM a bit deeper).
- It is a **short-volatility** strategy: steady premium, rare big losses. The
  smooth low-DD curve is BOTH the attraction AND the warning. Worst-case is the
  fat tail NOT in the 2024-26 sample (a market-wide alt-mania). 
- **Leverage caution:** do NOT slap on 5×. Low historical DD understates the real
  tail; short-vol + leverage = ruin risk + liquidation on thin microcaps. Right
  path: prove live at 1× small size, measure real slippage + worst cluster, then
  modest fractional-Kelly (1.5–2×) at most. Capacity is limited (thin coins).
- Costs checked: funding is a tiny drag (−0.06%/trade); limit orders LOSE
  (adverse selection — fade entries want TAKER).

## Where things live
- `notebooks/pump/` — notebooks 00–14, `_lab.py`, `HOW_WE_WORK.md`, `_out/` (cache, gitignored).
- `strategies/pump_fade_xs/` — harness port (cross-check, verdict REFUTED→explained).
- `C:\projects\pump_fade_bot` — live paper-trading bot (separate git repo): asyncio
  + ccxt + Bybit public WS + FastAPI dashboard (:8200). Trades the scale-in +
  classifier version. `python train_model.py` builds `model/pump_clf.pkl` from
  `data/pump_features.npz`, then `python main.py`. The user is running it live.
- Both committed (researchlab `63d1e6e`, bot initial commit). Not pushed.

## Open next steps (his queue)
1. **CVD / order-flow features** — add taker buy/sell (needs tick/aggTrade data;
   bot can compute live from the trade WS) to strengthen the classifier. Highest ceiling.
2. **Regime filter** — cut alt-mania clusters (the only source of drawdowns).
3. **Leverage/tail stress test** — simulate a cluster 2–3–5× worse than Feb-2025
   on 1×/2×/5× to find the ruin boundary before any leverage.
4. **Dumps (symmetry)** — long on dump exhaustion for an uncorrelated stream.
5. Live confirmation from the running bot (compare real fills vs trigger close).

## First action in the new session
Greet him, confirm you've read `HOW_WE_WORK.md`, and ask which of the open steps
he wants — or what the live bot has shown since. Keep the pace slow and honest.
