# supertrend_adx_ema — Mean-Reversion BB+RSI (file repurposed)

## Current Strategy (champion: iter 8)
The `strategy.py` file actually implements a **mean-reversion** strategy
(Fast Bollinger Bands + RSI on BTC/ETH/SOL @ 1h), NOT a Supertrend+ADX+EMA
trend-follower. The previous program.md notes refer to a long-superseded
implementation. This file is the active hypothesis log for the current MR.

### Baseline state (iter 1)
- composite −0.585, mean per-window OOS Sharpe +0.98, **stitched 24mo −58.5%**
- Per-window OOS positive (mean alpha_sharpe +1.35) — edge exists in OOS slice
- TRAIN period catastrophic (sharpe −1.6, total return −51%) — MR fades the
  2024 bull rally relentlessly, gives back all OOS gains over full 24mo period
- Composite floored by negative stitched return

### Champion (iter 8): CT-dampener + ATR stop-loss
```
counter_trend_size = 0.25       # 25% sizing when trade opposes EMA200 regime
ema_trend_period   = 200
ATR stop           = 2.5*ATR adverse-move from entry
```
- composite −0.545 (+0.04 vs baseline)
- stitched 24mo −17.3% (down from −58.5%, **+41pp improvement**)
- OOS Sharpe 0.53, 84 trades, TIP 41.6%, DSR 0.31
- Audit PASSED, no lookahead. Funding −$21 (small).

## Iter Table

| Iter | Verdict | Composite | Stitched 24mo | OOS Sharpe | Note |
|-----:|:-------:|:---------:|:-------------:|:----------:|:-----|
| 1 | KEEP (baseline) | −0.585 | −58.5% | +0.98 | Two-sided BB(14,2.0)+RSI(10,30/70), vol-targeted. |
| 2 | REVERT | −0.815 | −4.2% | +0.45 | EMA200 hard regime gate (long-up, short-down only). Stitched nearly cured but TIP→11%, n_trades→13 — penalties kill composite. |
| 3 | REVERT | −0.585 | (=base) | — | Re-run of baseline (file reverted from iter2). |
| 4 | REVERT | −1.316 | −29.4% | −0.21 | LONG-ONLY MR. Shorts have OOS value in 2025 Q3-Q4 sell-off — cutting them tanks OOS Sharpe. |
| 5 | REVERT | (negative) | −36.0% | −1.11 | Loose bands (bb=1.5, rsi=40/60) + EMA gate — quality of MR signal needs tight extremes. |
| 6 | REVERT | −0.577 | −25.4% | +0.40 | CT-dampener 0.30 (no stop). Δ+0.008, just below KEEP threshold. Right direction. |
| 7 | REVERT | −1.95 | −9.6% | −0.32 | CT-damp 0.15 + extreme bands (bb=2.5, rsi=25/75) — bands too tight, n_trades→27. |
| 8 | **KEEP** | **−0.545** | **−17.3%** | **+0.53** | **Champion**: CT-damp 0.25 + 2.5×ATR stop. Stop caps tail losses, dampener cuts trend-fade exposure. |
| 9 | REVERT | −0.886 | −18.2% | +0.15 | Tighter stop (2.0×ATR) clips winners → OOS Sharpe collapses. |
| 10 | REVERT | −1.52 | −10.4% | −0.15 | Harsher CT-damp (0.10). Stitched best yet (−10%) but OOS Sharpe craters (W3 OOS −3.5). |

## What's been ruled out
- **Long-only**: shorts contribute meaningfully to OOS Sharpe in bear regimes.
- **Hard EMA regime gate**: kills TIP and trade count, triggers composite penalties.
- **Loose BB/RSI bands**: MR edge concentrated at extreme z-scores; loosening destroys signal-to-noise.
- **Very tight stops (2.0×ATR or below)**: clip winners faster than they save losers.
- **Stronger dampeners (≤0.15)**: linearly reduce stitched bleed but degrade per-window Sharpe even faster.
- **Tighter bands (BB=2.5, RSI=25/75)**: drops n_trades below penalty threshold.

## Honest assessment
The MR strategy has **genuine OOS edge** (mean alpha_sharpe +1.35 across walk-forward windows in baseline), but the full 24mo stitched return is structurally negative because:
1. The 2024 bull rally crushed two-sided MR through the train slice.
2. Crypto's positive drift + funding costs penalize MR's net-flat exposure.
3. Mean reversion at this frequency (1h, BB+RSI) is fading impulses — by definition takes the opposite side of momentum runs.

The CT-dampener + ATR stop combo (iter 8) is a Pareto improvement that gets composite past the +0.01 threshold, but **the strategy is not in "real edge" territory**: composite remains negative, stitched is still −17%, DSR is 0.31 (selection-bias warning).

## Recommended next direction
Stop optimizing this MR framework — improvements are sub-linear and the structural drag is fundamental. Either:
1. **Pivot to mean-reversion ONLY in flat-vol regimes** (skip both up-trend and down-trend; trade only in `|EMA-slope| < threshold`). Smaller universe of opportunities but much higher edge density.
2. **Switch to a different fundamental hypothesis** — e.g. funding-rate carry-and-fade, intraday volatility breakouts. The "BB+RSI MR on 3-symbol basket" hypothesis appears structurally capped at "marginally less bad".
3. **Manual holdout check** before any more iteration — if iter 8 is already a curve-fit, the +0.04 composite gain may not survive 2026 Q1.
