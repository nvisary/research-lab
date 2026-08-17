"""Окна TRAIN / VALID / TEST — единственный источник правды.

В старом мире ``def window(t)`` копипастилась в каждый ноутбук (nb00, nb01,
nb02, nb04 линии pump_dump_v3), а nb03 вообще получал колонку ``win`` через
merge из соседнего parquet. Одна правка границы — и ноутбуки молча разъезжались.
Здесь граница объявлена один раз.

Про TEST. Эрозия holdout асимметрична: смотреть на него, чтобы **убить**
кандидата, почти безопасно; смотреть, чтобы **выбрать** параметр или воскресить
идею, — фатально. Поэтому TEST не заблокирован, но каждое открытие пишется в
журнал линии через :func:`open_test`, чтобы «сколько раз мы туда смотрели» был
фактом в файле, а не воспоминанием.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

#: Границы окон. Полуинтервалы [начало, конец): точка ровно на границе
#: принадлежит следующему окну.
WINDOWS: dict[str, tuple[str, str]] = {
    "TRAIN": ("2024-01-01", "2025-07-01"),
    "VALID": ("2025-07-01", "2026-02-01"),
    "TEST":  ("2026-02-01", "2026-08-01"),
}

#: Июльский хвост 2026 обрезан рвано по алфавиту — символы в конце азбуки
#: докачаны дальше остальных. Резать здесь, иначе последние дни смещены.
RAGGED_CUT = pd.Timestamp("2026-07-31 11:00", tz="UTC")

ORDER = ["TRAIN", "VALID", "TEST"]


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def window_of(t) -> str:
    """Имя окна для одной метки времени; ``"OUT"`` — вне всех окон."""
    t = _ts(t)
    for name in ORDER:
        lo, hi = WINDOWS[name]
        if _ts(lo) <= t < _ts(hi):
            return name
    return "OUT"


def assign_window(df: pd.DataFrame, col: str = "entry_ts") -> pd.Series:
    """Колонка с именем окна для каждой строки — векторно."""
    t = pd.to_datetime(df[col], utc=True)
    out = pd.Series("OUT", index=df.index, dtype=object)
    for name in ORDER:
        lo, hi = WINDOWS[name]
        out[(t >= _ts(lo)) & (t < _ts(hi))] = name
    return out


def count_test_looks(text: str) -> int:
    """Сколько записей в журнале открытий — только строки данных, не шапка."""
    return sum(1 for ln in text.splitlines()
               if re.match(r"^\|\s*\d+\s*\|", ln))


def open_test(line: str, reason: str, root: Path | None = None) -> int:
    """Записать открытие TEST в журнал линии и вернуть номер по счёту.

    Ничего не блокирует — только делает счёт видимым. Формулируйте ``reason``
    как намерение: «убить кандидата X» безопасно, «выбрать горизонт» — нет.

        lab.open_test("pump_v4", "убить ride-кандидата, VALID уже отрицателен")
    """
    root = root or Path(__file__).resolve().parent / "lines"
    d = root / line
    d.mkdir(parents=True, exist_ok=True)
    ledger = d / "TEST_LOOKS.md"
    if not ledger.exists():
        ledger.write_text(
            f"# Открытия TEST — линия `{line}`\n\n"
            "Каждая строка = один раз, когда на holdout посмотрели. Смотреть,\n"
            "чтобы убить кандидата, — почти безопасно; чтобы выбрать параметр —\n"
            "фатально. Счёт нужен, чтобы этот баланс был виден.\n\n"
            "| # | когда | зачем |\n|---|---|---|\n",
            encoding="utf-8",
        )
    text = ledger.read_text(encoding="utf-8")
    n = count_test_looks(text) + 1
    stamp = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M")
    ledger.write_text(text + f"| {n} | {stamp} | {reason} |\n", encoding="utf-8")
    print(f"[TEST look #{n}] {line}: {reason}")
    return n
