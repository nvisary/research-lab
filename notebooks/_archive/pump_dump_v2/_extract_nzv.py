"""Пер-эвентный гейт активности: доля минут с ненулевым объёмом в трейлинг-1440.

ЗАЧЕМ. Аудит данных (2026-08-02) нашёл, что 24.1% всех баров на диске имеют volume==0:
вселенная выросла с 218 до 543 символов в основном за счёт ~127 токенизированных акций
(WMT, BRKB, ASML, IBM, KLAC, TQQQ, EWJ…), которые торгуются только в часы американской сессии,
а ночь и выходные ДОБИТЫ плоскими барами (o=h=l=c=prev_close, volume=0).

Для памп-детектора это яд: гейт требует объём за 15м >= 3x медианы минутного объёма за 1440м.
У инструмента, стоящего 14 часов в сутки, эта медиана уползает к нулю, и гейт проходится
на открытии сессии автоматически. У дамп-ноги объёмного гейта нет вовсе, поэтому утренний
гэп читается как «дамп −7%».

Гейт по символам («выбросить акции») хуже, чем причинный пер-эвентный: последний работает
одинаково и для крипты, ушедшей в спячку, и не требует ручного списка. Считаем на каждое
событие долю ненулевых баров в трейлинг-1440 (окно, ЗАКАНЧИВАЮЩЕЕСЯ ДО бара-триггера —
тот же DAY-lookback, что у детектора, без заглядывания вперёд).

Пост-хок проход: события берутся из готовых v3-parquet, объём грузится по символу один раз.

    uv run python notebooks/pump_dump_v2/_extract_nzv.py
"""
import sys, time
sys.path.insert(0, "notebooks/pump_dump_v2")
from _lab import ohlcv
import numpy as np, pandas as pd

DAY = 1440
END = "2026-08-01"


def run():
    ev = []
    for name in ("pump", "dump"):
        d = pd.read_parquet(f"notebooks/pump_dump_v2/_out/{name}_signals_v3.parquet",
                            columns=["sym", "entry", "stream"])
        ev.append(d)
    E = pd.concat(ev, ignore_index=True)
    print(f"events {len(E)}, symbols {E.sym.nunique()}", flush=True)

    out = []
    t0 = time.time()
    for i, (sym, g) in enumerate(E.groupby("sym", sort=False), 1):
        try:
            df = ohlcv(sym, "2024-01-01", END, "1min")
        except Exception:
            continue
        v = df["volume"].to_numpy("float64")
        ts = df.index
        # накопленная сумма ненулевых баров -> доля в окне [t-DAY, t)
        nz = np.concatenate([[0], np.cumsum((v > 0).astype(np.int64))])
        # entry = ts[st+1]; выравниваем tz — .values роняет таймзону и ломает get_indexer
        ent_idx = pd.DatetimeIndex(g.entry)
        if ent_idx.tz is None and ts.tz is not None:
            ent_idx = ent_idx.tz_localize(ts.tz)
        elif ent_idx.tz is not None and ts.tz is None:
            ent_idx = ent_idx.tz_convert(None)
        pos = ts.get_indexer(ent_idx)
        for idx, ent in zip(pos, g.entry.values):
            if idx < 0:
                out.append((sym, ent, np.nan)); continue
            st = idx - 1                               # бар-триггер
            lo = max(0, st - DAY)
            share = (nz[st] - nz[lo]) / max(st - lo, 1)
            out.append((sym, ent, float(share)))
        if i % 100 == 0:
            print(f"  {i} symbols, {len(out)} events, {time.time()-t0:.0f}s", flush=True)

    N = pd.DataFrame(out, columns=["sym", "entry", "nzv1440"])
    p = "notebooks/pump_dump_v2/_out/nzv1440.parquet"
    N.to_parquet(p)
    print(f"\ncached -> {p}  ({len(N)} rows)")
    print(N.nzv1440.describe(percentiles=[.01, .05, .1, .25, .5, .75]).round(4).to_string())
    for thr in (0.3, 0.5, 0.7, 0.9):
        print(f"  events with nzv1440 >= {thr:.0%}: {(N.nzv1440 >= thr).mean()*100:5.1f}%")


if __name__ == "__main__":
    run()
