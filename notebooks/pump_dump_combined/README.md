# pump_dump_combined — pump + dump portfolio research

Separate research thread (sibling to `../pump/` and `../dump/`). Same playbook
(`../pump/HOW_WE_WORK.md`): one honest step at a time, baseline, pooling, mind
costs, flag lookahead. Helper/data shared (`_lab.py`).

## The question
Pump-fade (SHORT after +5%/15m) and dump-bounce (LONG after −7%/15m) are
mechanically OPPOSITE mean-reversion streams. Hypothesis (user): they are
uncorrelated — possibly hedge each other's worst regimes (pump-shorts suffer in
a melt-up, dump-longs suffer in a crash; those are opposite regimes) — so combining
them yields a smoother curve and lower drawdown than either alone.

Both streams built with the SAME machinery as the final dump strategy (scale-in,
price-step fill, exit cluster_end+240, net fees), just mirrored in sign, on the
full 173-symbol universe 2024-2026. Core configs (no classifier/tail-limits) for a
clean correlation read; those improvements are orthogonal and can be layered later.

## Notebooks
- `00_pump_plus_dump` — both streams (full universe, scale-in price-step), daily-
  return correlation, combined 50/50 sleeves, regime-hedge check. **Result: thesis
  confirmed — daily corr = −0.35, combined 50/50 Sharpe 4.12 (vs 1.99 dump / 2.72
  pump alone), maxDD −3.6% (< both). Regime hedge literal: dump's worst days → pump
  positive & vice versa; both negative only 6.3% of days.** HONEST FLAG: the
  simplified pump mirror has marginal per-trade edge (+0.02%/trade w/o volume filter
  + classifier); its portfolio +165% is largely a capacity-clipping artifact (skips
  correlated melt-up losers) — pump's absolute level is NOT trustworthy yet. Robust
  takeaway = the negative correlation + diversification, not pump's absolute number.

- `01_faithful_combined` — **pump rebuilt faithfully (vol-gate +5%/15m AND vol>3×
  + walk-forward classifier) + tail-limits on both + reinvest + DCA.** Pump: vol-gate
  alone doesn't fix raw edge (−0.08%/trade); the CLASSIFIER does (pred>0+stop+KS →
  +0.69%/trade, win 61%; but OOS corr only +0.079 = thin/fragile). Dump after layers
  +2.57%. **Daily corr −0.50.** Reinvest 2%, $1000: dump $2550/CAGR50%/Sh1.97;
  pump $3818/78%/3.75; **COMBINED $11504/CAGR186%/Sharpe5.04/DD−9.5%.** DCA $100/mo
  ×28=$2800 → robot $12205 (4.36× cash). **HONEST: diversification is robust (corr
  −0.50, combined Sharpe≫each, DD lower); but the ABSOLUTE (186% CAGR, 4.36× DCA) is
  OPTIMISTIC — 2%-compound × ~10.8k trades is explosive, assumes infinite capacity/
  divisibility (microcaps cap it), pump rests on a thin classifier, Sharpe 5 is
  unrealistic live.** Read magnitude as a ceiling, not a forecast.

- `02_realistic_capacity` — **capacity cap + market-impact slippage → believable
  absolute + the SCALING WALL.** Median coin liquidity ~$2.7–3.1k/min (thin
  microcaps). Same combined stream, reinvest 2%, by START capital: $1k→CAGR 140%/
  Sharpe 4.34; $10k→99%/3.53; $50k→64%/2.74; $200k→36%/2.04; **$1M→11.6%/Sharpe 0.99
  (69% of trades capacity-capped, $744k slippage).** Strategy does NOT scale — a
  small-capital niche (exactly why such edge isn't arbitraged by large money).
  **Realistic DCA $100/mo: $2,800 in → $9,064 by 2026 (3.24×), vs optimistic $12,205**
  — account stays small so capacity barely binds (4% capped). Robust: scaling wall +
  DCA shape. Caveats: slippage params estimated, latency/fills unmodeled, thin pump
  classifier, live=0 → $1k/140% is an in-sample ceiling.

- `03_position_sizing` — **сплит размера позиции при резерве 10% (нетронутый капитал).**
  Параметризованный движок: `n` одновременных × `p` доля эквити, открытие только если
  `задействовано+новая ≤ 90% эквити`. Свип (все 90%): 45×2% Sh4.84/DD−10% → 30×3%
  5.05/−11% → 18×5% 5.48/−18% → 9×10% **6.51**/−26% → 3×30% 5.84/**−43%**. Концентрация
  растит Sharpe до ~9×10% (крупнее ставка × реальный edge + защитный клиппинг ёмкости,
  %взято 92%→44%), дальше maxDD рвётся. **maxDD — честная цена концентрации; final$ при
  концентрации = фантазия (компаундинг × нарушение стены ёмкости nb02), игнорировать.**
  Рекоменд. по риск-профилю: 30×3% ≈ текущему но с резервом; 18×5% баланс; 9×10% пик
  Sharpe но хрупкий хвост short-vol. Cache: `_out/comb_trades.parquet` (faithful combined).
  **Под моделью ёмкости (nb02, +liq в кэше) вывод ПЕРЕВОРАЧИВАЕТСЯ: 18×5% хуже 50×2% по
  Sharpe на ВСЕХ уровнях капитала** (на $1k 3.81 vs 4.32, DD −20% vs −9.5%, слиппедж ×8,
  capped 29% vs 12%); концентрация бьёт лимит участия раньше → CAGR пересекает 50×2% вниз
  ~$150k, на $1M почти мёртв (+0.3%/Sh0.10/89% urезано). 18×5% оправдан только на $1–5k и
  только ради ×2 роста ценой ×2 просадки. Для риск-профиля и масштаба много мелких лучше.
  **НО в DCA-режиме ($500 старт + $500/мес, без изъятий, capacity-модель) разворот №2:**
  счёт остаётся малым → стена ёмкости почти не бьёт → 18×5% даёт **$70.7k vs $38.5k**
  (4.87× vs 2.65× на внесённые $14.5k) при **равном Sharpe** (3.78 vs 3.73), ценой −21% vs
  −9.5% DD и ×4 слиппеджа. Концентрация выгодна в накоплении малым счётом, невыгодна на
  крупном lump-sum. **ИТОГ — 3 рабочих режима (DCA+capacity, резерв 10%): 🟢 Безопасный
  50×2% (DD −10%, 2.65×), 🟡 Средний 30×3% (DD −12%, 3.59×, Sharpe 3.83 = пик), 🔴 Агрессивный
  18×5% (DD −21%, 4.87×).** Ключ: Sharpe плоский ~3.7–3.8 по всей лесенке → это одна ручка
  громкости (концентрация ↔ просадка), не три стратегии. Дальше 18×5% (12×7.5%/9×10%) DD
  рвётся −29…−34%, capped 49–60%, Sharpe падает. Привязывать режим к размеру счёта, не
  переключать на ходу; агрессивный хрупок к неучтённому short-vol хвосту.

- `04_faithful_v2_baseline` — **1:1 с боевым ботом.** pump = настоящая модель из `notebooks/pump`
  (3% стоп + time-scale-in + WF-clf, из `pump_features_3y.npz`); dump = пересобран ровно по
  пайплайну бота (`_out/dump_faithful.parquet`: price-step + 20% стоп + **12 фич med240, БЕЗ KS**).
  Прежние nb00–03 собирали pump зеркалом dump'а (20% стоп) — упрощение, не бот. **Baseline $1000,
  2%/поз, реинвест: $13.4k (+1235%), Sharpe 4.74, maxDD −13.2%**, 10096 сигналов, 7502 сделки
  (pump 5365/dump 2137), 26% пропущено по капиталу, max 50 / 100% в моменте, в среднем ~4%.
  **Эффект убирания KS:** dump kept +5.48%/трейд (KS выкидывал прибыльные кластерные дни,
  dump nb09: 97% прибыли в топ-20 днях), но DD глубже (−13.2 vs −11.9) и пропусков 26% vs 11%.
  Комиссии 0.15% вшиты; слиппедж/ёмкость НЕ учтены (идеал — см. nb02).
- `05_modes_on_faithful` — **3 режима на combined 1:1-с-ботом** (рамка nb04, резерв 10%), полный
  срез на каждый. 🟢 2% Sh5.13/DD−11.5% → 🟡 3% Sh6.45/DD−11.2% → 🔴 5% Sh7.23/DD−9.6%
  (final $12k/$28k/$174k — потолок без ёмкости). **Sharpe растёт с концентрацией, НО idealized-DD
  обманчив:** агрессивный показывает DD −9.6% < безопасного −11.5% — артефакт движка без ёмкости
  (меньше слотов → больше пропусков коррелированных dump-кластеров → ниже DD). Под реальной
  ёмкостью (nb02/03) наоборот хуже (~−21%). win/avg/PF неизменны (~61%/+1.6%/2.1). **Per-mode
  риск надо брать из capacity/DCA, перепрогнанной на этом combined (ещё не сделано).**

## State / next
Diversification CONFIRMED (corr −0.50). Pump faithful. **Believable absolute now
bounded by the scaling wall: it's a small-capital strategy (CAGR collapses 140%→12%
from $1k→$1M); realistic $100/mo DCA ≈ $9k by 2026 (in-sample ceiling).** Next:
anti-overfit of thin pump classifier; slippage-param sensitivity; graduation to
strategies/ + live paper bot (small size, default-on tail limits).
