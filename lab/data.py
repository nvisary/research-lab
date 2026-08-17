"""Доступ к данным и параллельный обход вселенной.

Замер на этой машине: один проход по всем 673 символам за 2024-01..2026-08 —
около 5.5 минут, из них 3.2 на чтение parquet и 2.3 на счёт. Всё в один поток
при 32 доступных ядрах, и каждый билдер линии платил это заново.

Важно не само время, а режим работы. При пяти минутах на прогон вопросы к
данным копятся в батч и часть просто не задаётся; при сорока секундах можно
спрашивать свободно — а именно свободное «покажи ещё срез» и находит вещи вроде
смены фауны пампов между полугодиями.

Символы независимы, так что обход — это ``ProcessPoolExecutor`` и ничего больше::

    # в файле _build_*.py (не в ячейке ноутбука — см. ниже про pickle)
    def rows_for(sym, df):
        return [{"sym": sym, ...} for ... in ...]

    rows = lab.scan(rows_for, start="2024-01-01", end="2026-08-01")

**Функция должна быть определена в модуле, а не в ячейке ноутбука** — процессы
получают её через pickle, а объекты из ``__main__`` ноутбука не пиклятся. Это не
ограничение движка, а причина, по которой тяжёлое считается билдером и кладётся
в parquet, а ноутбук читает готовое. Если очень нужно из ячейки — ``mode="thread"``
(поможет чтению, но не питоновским циклам).
"""
from __future__ import annotations

import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Callable

import pandas as pd

from datafeed import loader

from .windows import RAGGED_CUT

DEFAULT_START = "2024-01-01"
DEFAULT_END = "2026-08-01"


def symbols() -> list[str]:
    """Все символы с минутными данными на диске.

    Мёртвые директории (делистинги) НЕ выбрасываются: выжившие символы — это
    смещение выборки, а в фейд-стратегиях делистнутая после пампа монета обычно
    была бы прибыльным шортом.
    """
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def load(symbol: str, start: str = DEFAULT_START, end: str = DEFAULT_END,
         tf: str = "1min", cut_ragged: bool = True) -> pd.DataFrame:
    """OHLCV одного символа. Кешируется в пределах процесса.

    ``cut_ragged`` обрезает рваный июльский хвост 2026: докачка шла по алфавиту,
    и без обрезки последние дни содержат только конец азбуки.
    """
    df = _load_cached(symbol, start, end, tf)
    if cut_ragged and len(df):
        df = df[df.index <= RAGGED_CUT]
    return df


@lru_cache(maxsize=64)
def _load_cached(symbol: str, start: str, end: str, tf: str) -> pd.DataFrame:
    return loader.load(symbol, start, end, tf)


def cache_clear() -> None:
    """Сбросить кеш загрузки — после докачки или перезаписи сырья."""
    _load_cached.cache_clear()


def _work(args):
    fn, sym, start, end, tf, cut = args
    try:
        df = load(sym, start, end, tf, cut)
    except Exception as e:                                   # noqa: BLE001
        return sym, [], f"загрузка: {e}"
    if not len(df):
        return sym, [], "пусто"
    try:
        return sym, list(fn(sym, df)), None
    except Exception as e:                                   # noqa: BLE001
        return sym, [], f"счёт: {e}"


def scan(fn: Callable[[str, pd.DataFrame], list], syms: list[str] | None = None,
         start: str = DEFAULT_START, end: str = DEFAULT_END, tf: str = "1min",
         workers: int | None = None, mode: str = "process",
         cut_ragged: bool = True, quiet: bool = False) -> pd.DataFrame:
    """Обойти вселенную параллельно и собрать строки в один DataFrame.

    ``fn(sym, df)`` возвращает список словарей — по строке на событие. Символы,
    упавшие с ошибкой, не роняют прогон: они считаются и перечисляются в конце,
    потому что молча пропущенный символ — это смещение выборки, о котором вы
    не узнаете.
    """
    syms = syms or symbols()
    workers = workers or max(1, (os.cpu_count() or 4) - 2)
    if mode == "process":
        try:
            pickle.dumps(fn)
        except Exception:                                    # noqa: BLE001
            print("⚠ функция не пиклится (определена в ячейке?) — перехожу на потоки; "
                  "для полного ускорения вынесите её в _build_*.py")
            mode = "thread"

    pool = ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor
    payload = [(fn, s, start, end, tf, cut_ragged) for s in syms]
    rows, failed, empty, t0 = [], {}, 0, time.time()

    with pool(max_workers=workers) as ex:
        futs = [ex.submit(_work, p) for p in payload]
        for i, fut in enumerate(as_completed(futs), 1):
            sym, got, err = fut.result()
            if err == "пусто":
                empty += 1
            elif err:
                failed[sym] = err
            rows.extend(got)
            if not quiet and i % 100 == 0:
                print(f"  {i}/{len(syms)} символов, {len(rows)} строк, "
                      f"{time.time() - t0:.0f}с", flush=True)

    el = time.time() - t0
    if not quiet:
        print(f"[скан] {len(syms)} символов за {el:.0f}с на {workers} воркерах "
              f"({mode}) → {len(rows)} строк")
        if empty:
            print(f"  пустых символов: {empty}")
        if failed:
            print(f"  ✗ упало {len(failed)}: "
                  + ", ".join(f"{k} ({v})" for k, v in list(failed.items())[:5])
                  + (" …" if len(failed) > 5 else ""))
    return pd.DataFrame(rows)
