"""Bot-faithful signal builder (v3) — реальная рампа + окно по июль 2026.

ЗАЧЕМ. `_build_signals.py` (v2) моделирует scale-in только в ЦЕНЕ: усредняет по ВСЕМ заливкам
кластера с весами 1..k, а движок потом ставит полный `f*eq`. Живой бот так не может (nb05).
Здесь воспроизводится то, что бот делает на самом деле (`pump_fade_bot/strategy.py:_next_add`,
`core.py:Position`):

  base = target * scalein_base_frac
  add_i = min(base*i, target - notional)     # i = 1,2,3,…  «позже крупнее»
  → доли транша от target: 0.05, 0.10, 0.15, 0.20, 0.25, 0.25(обрезан) при base=0.05
  → накопленно: 5%, 15%, 30%, 50%, 75%, 100% — полный размер РОВНО на 6-м транше

Отсюда два следствия, которых в v2 нет:
  (1) РАЗМЕР. Развёрнутая доля капитала = min(1, base*k(k+1)/2), а не 1 всегда.
  (2) ЦЕНА. Как только target набран, бот БОЛЬШЕ НЕ ДОЛИВАЕТ, даже если кластер продолжает
      триггерить. Значит средняя цена считается только по ПЕРВЫМ N заливкам, а не по всем k.
      Это бьёт по пампу: у него median k=4, но mean 6.6 и max 76, а поздние заливки — самые
      выгодные для шорта (цена выше). v2 взвешивает их тяжелее всех; бот их не получает.
      Число используемых заливок зависит от base: при 0.05 их 6, при 0.40 — всего 2.
      Поэтому фронт-лоад НЕ бесплатен: быстрее набираешь размер, но хуже средняя цена.

Пишем сетку по `scalein_base_frac` (как v2 писал сетку по givebacks) → для каждого события
avg/frac/pnl/exit при каждом base, плюс колонки `*_et` = конвенция эталона (все заливки,
frac=1) для валидации: они обязаны совпасть с `pnl6` из v2-parquet.

ЧТО СОЗНАТЕЛЬНО НЕ МЕНЯЛОСЬ (остаточные расхождения, каждое — в пользу бота или нейтрально):
  • Заливка по цене СЛЕДУЮЩЕГО бара `c[p+1]` — конвенция эталона (консервативно; бот берёт
    close бара-триггера, это ~+0.5pp в пользу бота).
  • Стоп сканируется с `last+1`, т.е. с конца кластера. Бот проверяет стоп и ВНУТРИ кластера
    на нетриггерных барах, и его стоп едет вместе с растущей средней (`core.py:stop_price` —
    property от `avg_entry`). Здесь стоп считается от финальной средней. Отдельная задача:
    менять по одному, иначе разницу не разложить.
  • Дамп-бот доливает ещё до 10 мин ПОСЛЕ последнего триггера (кластер жив); здесь заливки
    обрезаны по `last`. Пропущенные заливки были бы ниже по цене → консервативно.

Выход: `_out/{pump,dump}_signals_v3.parquet`. Старые v2-файлы НЕ трогаются — nb00–nb05
продолжают воспроизводиться.

    uv run python notebooks/pump_dump_v2/_build_signals_v3.py [--symbols N] [--end YYYY-MM-DD]
"""
import sys, time, argparse
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv, list_symbols
import numpy as np, pandas as pd

# всё 1:1 с _build_signals.py
from _build_signals import (clusters, fills_time, fills_price, pump_feats, dump_feats,
                            PF, DF, WIN, H, COOLDOWN, DAY, FEE,
                            THR_P, THR_D, VOL_MULT, DELTA, CSTOP_P, CSTOP_D, SLIP)

# Сетка scalein_base_frac (1.00 = всё первым траншем). Нижняя половина добавлена после nb06:
# у пампа медиана k велика, и МЕНЬШАЯ база пропускает больше заливок → средняя цена ближе к
# эталонной. У дампа наоборот (k мал, капитал не разворачивается) — ему нужна большая.
# Поэтому оптимум почти наверняка РАЗНЫЙ по ногам, и нижний край сетки обязателен.
BASES = [0.01, 0.02, 0.03, 0.05, 0.10, 0.20, 0.40, 0.60, 1.00]
END_DEFAULT = "2026-08-01"


def ramp_fills(prices, base):
    """Доли траншей по логике бота. Возвращает (веса_использованных, накопленная_доля).

    add_i = min(base*i, 1 - cum); прекращаем, как только cum == 1 или заливки кончились.
    base=None → конвенция эталона: все заливки, веса 1..k, frac=1.
    """
    if base is None:
        w = np.arange(1, len(prices) + 1, dtype=float)
        return w / w.sum(), 1.0
    ws = []; cum = 0.0
    for i in range(1, len(prices) + 1):
        if cum >= 1.0 - 1e-12:
            break
        add = min(base * i, 1.0 - cum)
        ws.append(add); cum += add
    ws = np.array(ws)
    return (ws / ws.sum() if ws.sum() > 0 else ws), cum


def hold_to_horizon(c, h, l, avg, en, ex, side, cstop):
    """Катастроф-стоп (интрабар + гэп-филл + слип) иначе выход на горизонте.
    1:1 с `_build_signals.exit_rollover` при g=inf."""
    for t in range(en + 1, ex + 1):
        if side > 0:
            if h[t] / avg - 1 >= cstop:
                return t, min(-cstop, -(c[t] / avg - 1)) - SLIP
        else:
            if l[t] / avg - 1 <= -cstop:
                return t, min(-cstop, (c[t] / avg - 1)) - SLIP
    raw = c[ex] / avg - 1
    return ex, (-raw if side > 0 else raw)


def build_leg(side, symbols, end):
    thr = THR_P if side > 0 else THR_D
    cstop = CSTOP_P if side > 0 else CSTOP_D
    gate = side > 0
    names = PF if gate else DF
    rows = []; loaded = 0
    for sym in symbols:
        try:
            df = ohlcv(sym, "2024-01-01", end, "1min")
        except Exception:
            continue
        c = df["close"].to_numpy("float64"); v = df["volume"].to_numpy("float64")
        h = df["high"].to_numpy("float64"); l = df["low"].to_numpy("float64")
        ts = df.index; n = len(c); dv = c * v; loaded += 1
        for (st, last, cond) in clusters(c, thr, side):
            ex = last + 1 + H
            if st < DAY or st + 1 >= n or ex >= n:
                continue
            if gate:
                base_v = np.median(v[max(0, st - DAY):st]) if st > 60 else np.nan
                surge15 = v[st - 14:st + 1].sum() / (base_v * 15) if base_v and base_v > 0 else np.nan
                if not (surge15 >= VOL_MULT):
                    continue
                pos = fills_time(c, st, last, cond, n)
            else:
                pos = fills_price(c, st, last, n)
            if not pos:
                continue
            pr = np.array([c[p + 1] for p in pos])          # конвенция эталона: следующий close
            feats = pump_feats(c, v, h, l, st) if gate else dump_feats(c, v, h, l, st)
            liq = np.median(dv[max(0, st - DAY):st])
            d = dict(sym=sym, entry=ts[st + 1], liq=liq, k=len(pos),
                     stream=("pump" if side > 0 else "dump"))
            d.update({names[i]: feats[i] for i in range(len(names))})
            for tag, b in [("et", None)] + [(f"b{j}", bb) for j, bb in enumerate(BASES)]:
                w, frac = ramp_fills(pr, b)
                avg = float((w * pr[:len(w)]).sum())
                exi, pnl_raw = hold_to_horizon(c, h, l, avg, last, ex, side, cstop)
                d[f"avg_{tag}"] = avg
                d[f"frac_{tag}"] = frac
                d[f"kused_{tag}"] = len(w)
                d[f"pnl_{tag}"] = pnl_raw - 2 * FEE
                d[f"exit_{tag}"] = ts[exi]
            rows.append(d)
        if loaded % 100 == 0:
            print(f"  [{'pump' if side>0 else 'dump'}] {loaded} symbols, {len(rows)} events", flush=True)
    return pd.DataFrame(rows).dropna().sort_values("entry").reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=0)
    ap.add_argument("--end", default=END_DEFAULT)
    a = ap.parse_args()
    syms = list_symbols()
    if a.symbols:
        syms = syms[:a.symbols]
    print(f"v3 build: {len(syms)} symbols, window 2024-01-01 .. {a.end}, bases {BASES}", flush=True)
    for side, name in ((+1, "pump"), (-1, "dump")):
        t0 = time.time()
        X = build_leg(side, syms, a.end)
        suf = f"_{a.symbols}" if a.symbols else ""
        X.to_parquet(f"notebooks/pump_dump_v2/_out/{name}_signals_v3{suf}.parquet")
        print(f"\n{name}: {len(X)} events in {time.time()-t0:.0f}s -> _out/{name}_signals_v3{suf}.parquet")
        print(f"  {X.entry.min()} .. {X.entry.max()}")
        for tag in ["et"] + [f"b{j}" for j in range(len(BASES))]:
            lab = "etalon" if tag == "et" else f"base={BASES[int(tag[1:])]:.2f}"
            print(f"  {lab:12s} kused med {X[f'kused_{tag}'].median():4.0f}  frac mean "
                  f"{X[f'frac_{tag}'].mean()*100:5.1f}%  pnl mean {X[f'pnl_{tag}'].mean()*100:+6.2f}%  "
                  f"median {X[f'pnl_{tag}'].median()*100:+6.2f}%", flush=True)
