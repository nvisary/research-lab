# index_leadlag — alts vs. self-built crypto index

## Thesis

Crypto alts tend to lead/lag the broad market on short horizons. Build a
top-30 USDT-perp index weighted by trailing-30d dollar volume (cap proxy);
when the index moves but an individual alt has not yet caught up, expect a
catch-up move — fade the spread, not the level.

This is **lead-lag mean reversion against an internal benchmark**, distinct
from per-symbol price-z (`zscore_mr_alts`) or return-z (`zret_mr_alts`)
strategies: those measure overextension vs. the symbol's own history. Here
the reference is the market itself, so the signal survives when *every*
alt is trending in the same direction (own-z would say "all overextended"
but market-relative-z says "balanced — no signal").

## Signal

For each symbol on 1h bars:

- `spread_L = log_ret_L(alt) − log_ret_L(idx)` over a lookback L
- `z = (spread − rolling_mean) / rolling_std` over a longer window
- LONG  when `z < -entry_k` AND `idx_ret_L > +idx_move`  (index up, alt lagged)
- SHORT when `z > +entry_k` AND `idx_ret_L < -idx_move`  (index down, alt held)
- EXIT  when `|z| < exit_k`
- Equal cash split (raw 1/n per symbol).

## Universe

Top-30 USDT-perp by typical Bybit dollar-volume, all with verified full
2024-01 → 2026-04 monthly coverage. Includes BTC and ETH — they are part of
the index but also tradable (with 30 names the self-reference leak is tiny).

Future variants the user wants to explore:
- top-5 only (BTC/ETH/SOL/XRP/DOGE-ish) — a "blue-chip" market index, alts
  traded relative to that.

## Iterations

| iter | verdict | composite | DSR  | note |
|------|---------|-----------|------|------|
|   1  | KEEP    | -1.006    | 0.31 | baseline: top-30, dvol-weighted, L=24h, zw=1w, k=1.8. PF 0.94, base set. |
|   2  | REVERT  | -6.484    | 0.00 | top-7: n_trades упал до 46, корреляция убила spread, BTC сам себя фейдил. |
|   3  | REVERT  | -2.356    | 0.01 | beta-adjusted spread: шум оценки > сигнал, β≈1 для cap-weighted. |
|   4  | REVERT  | -1.713    | 0.03 | 4h trend-gate: PF чуть улучшилась но n_trades упал, фат-tail зависимость. |
|   5  | REVERT  | -2.263    | 0.05 | momentum-direction: **7/12 regions healthy** (best!), но single-window dom. |
|   6  | REVERT  | -3.015    | 0.00 | L=6h: 2/12 regions, L=24h эмпирически правильный horizon. |
|   7  | REVERT  | -2.699    | 0.12 | cross-section z: близко (DSR 0.12, sh -0.23), но self-reference искажал. |
|   8  | KEEP    | -0.916    | 0.05 | **exclude-own index (jackknife)**: backbone стал корректным. |
|   9  | KEEP    | **0.001** | 0.12 | **cross-section z поверх exclude-own**: первый положительный композит. |
|  10  | REVERT  | -5.546    | 0.00 | momentum на новом backbone: 6/12 regions но W3 67% доминирует, MR правильный. |
|  11  | REVERT  | -3.602    | 0.02 | vol-target sizing: сжимает сигнал пропорционально, bleed не лечит. |

## What's been ruled out (alt-vs-index family)

- **Сужение universe до top-7** — корреляция между голубыми фишками убивает spread и снижает n_trades ниже порога штрафа.
- **Beta-adjusted spread** — для cap-weighted top-30 β≈1, rolling-оценка добавляет шум.
- **Lookback L=6h** — слишком короткий; 24h эмпирически правильный horizon.
- **Momentum sign** (на обоих backbone'ах) — даёт шире регионную дисперсию, но единственное окно доминирует композит. MR-направление побеждает на этом TF/lookback'е.
- **Vol-target sizing** (annual 30%) — душит сигнал пропорционально, не различает «плохой риск» и «хороший риск».

## What's been validated

- **Exclude-own (jackknife) index** — структурно необходимо: убирает self-reference, делает спреды сравнимыми.
- **Cross-sectional z** — поверх jackknife даёт rank на однородном распределении.
- **MR-направление**, **L=24h**, **top-30 universe**, **dvol-weighted**.

## Current best (iter 9)

Жив, но edge маленький (composite 0.001, PF 0.87). Основной bleed концентрируется в **bull/v4** (high-vol bull) sharpe -7.5 — там shorts на лидеров получают катастрофически: лидеры продолжают расти, MR-фейд проигрывает.

## What's been ruled out

- **Сужение universe до top-7** — корреляция между голубыми фишками убивает spread и снижает n_trades ниже порога штрафа. Top-30 эмпирически лучше top-7 для построения индекса.
- **Beta-adjusted spread** — для cap-weighted top-30 индекса β большинства компонент ≈1, rolling-оценка добавляет больше шума, чем фиксит. Не имеет смысла на этом TF и этой вселенной.

## Open observations (regime decomposition)

Diagnostics показывают одну сильную закономерность:
- В **flat-trend** бакетах sharpe стабильно −4…−7 во всех vol-уровнях.
- В **bear** и **bull** trend бакетах — смешанные, иногда положительные.
- Текущий `idx_move > 0.5%` гейт на L-баровом движении индекса слишком короткий, чтобы отфильтровать flat-режим: индекс может несколько раз пересечь 0.5% порог внутри широкого боковика.

Следующая логичная гипотеза: **trend-regime гейт на higher-TF (4h slope of log_idx)** — только торговать когда индекс действительно тренди́т на 4h, не на 24h-окне. Это убирает bleed в боковиках без ручной калибровки конкретных дат.
