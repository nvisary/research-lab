"""Аудит: два режима и проверка книги целиком.

Режимы существуют потому, что жёсткость нужна не везде. Разведка — это когда
вы щупаете данные и ещё не знаете, что ищете; формальности там душат и их
начинают обходить. Вывод — это когда считается доходность или строка идёт в
журнал; вот там цена ошибки уже оплачена трижды.

    lab.mode("explore")   # нарушение печатается и работа продолжается
    lab.mode("conclude")  # нарушение бросает AuditError

Правило простое: **переключайтесь в ``conclude`` перед тем, как посчитать
первое число, которое собираетесь кому-то показать** — включая себя через месяц.
"""
from __future__ import annotations

import warnings

import pandas as pd

_MODE = "explore"
_MODES = ("explore", "conclude")


class AuditError(AssertionError):
    """Нарушение контракта причинности в режиме вывода."""


def mode(name: str) -> str:
    """Переключить режим; без аргумента-опечатки вернуть текущий."""
    global _MODE
    if name not in _MODES:
        raise ValueError(f"режим должен быть одним из {_MODES}, получено {name!r}")
    _MODE = name
    print(f"[режим] {name}" + (" — нарушения блокируют" if name == "conclude"
                               else " — нарушения только печатаются"))
    return _MODE


def current_mode() -> str:
    return _MODE


def violation(msg: str) -> None:
    """Сообщить о нарушении: печать в разведке, исключение в выводе."""
    if _MODE == "conclude":
        raise AuditError(msg)
    warnings.warn(f"[аудит] {msg}", stacklevel=3)
    print(f"⚠ [аудит] {msg}")


# ---- проверка книги --------------------------------------------------------

def audit(book, *, verbose: bool = True) -> list[str]:
    """Полная проверка книги. Возвращает список проблем (пустой — всё чисто).

    Проверяется ровно то, что уже стоило закрытых линий:

    ``schema``   у каждой колонки объявлен asof — иначе нельзя отличить
                 признак от исхода, и рекордер :meth:`EventBook.where` слеп.
    ``entry``    вход строго позже сигнала — решение не может исполняться на
                 том же баре, который его определил.
    ``frac``     доля развёрнутого капитала объявлена и не больше единицы —
                 это ловушка на v2-класс ошибок, где цена входа честная, а
                 размер знает будущее.
    ``dupes``    одно событие не задвоено: задвоение тихо переоценивает и
                 доходность, и число наблюдений.
    ``windows``  события попадают в объявленные окна, а не мимо.
    """
    from .windows import assign_window  # локально: избегаем цикла импорта

    problems: list[str] = []
    ok: list[str] = []
    df = book.df

    def bad(msg):
        problems.append(msg)

    # 1. схема
    if book.undeclared:
        bad(f"asof не объявлен у {len(book.undeclared)} колонок: "
            f"{', '.join(book.undeclared[:8])}"
            + (" …" if len(book.undeclared) > 8 else ""))
    else:
        ok.append(f"asof объявлен у всех {len(df.columns)} колонок "
                  f"({len(book.causal)} причинных, {len(book.outcomes)} исходов)")

    # 2. обязательные колонки
    from .book import REQUIRED
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        bad(f"нет обязательных колонок: {', '.join(missing)}")
    else:
        ok.append("обязательные колонки на месте")

    # 3. вход строго позже сигнала
    if {"signal_ts", "entry_ts"} <= set(df.columns):
        s = pd.to_datetime(df["signal_ts"], utc=True)
        e = pd.to_datetime(df["entry_ts"], utc=True)
        n_bad = int((e <= s).sum())
        if n_bad:
            bad(f"{n_bad} событий входят не позже сигнала — вход на баре, "
                f"который сам определяет сигнал, забирает движение этого бара")
        else:
            lag = (e - s).dt.total_seconds().div(60)
            ok.append(f"вход всегда позже сигнала (медиана лага {lag.median():.0f} мин)")

    # 4. frac
    if "frac" in df.columns:
        f = pd.to_numeric(df["frac"], errors="coerce")
        if f.isna().any():
            bad(f"frac не определён у {int(f.isna().sum())} событий")
        elif (f > 1 + 1e-9).any():
            bad(f"frac > 1 у {int((f > 1).sum())} событий — развёрнуто больше "
                f"целевого размера, проверьте нормировку весов")
        else:
            ok.append(f"frac в пределах (медиана {f.median():.2f}, "
                      f"средняя {f.mean():.2f})")

    # 5. дубликаты
    keys = [c for c in ("sym", "entry_ts") if c in df.columns]
    if len(keys) == 2:
        n_dup = int(df.duplicated(keys).sum())
        if n_dup:
            bad(f"{n_dup} задвоенных событий по (sym, entry_ts)")
        else:
            ok.append("дубликатов событий нет")

    # 6. окна
    if "entry_ts" in df.columns and len(df):
        w = assign_window(df)
        n_out = int((w == "OUT").sum())
        counts = w.value_counts().to_dict()
        if n_out:
            bad(f"{n_out} событий вне объявленных окон "
                f"({n_out / len(df) * 100:.1f}%) — расширьте окна или обрежьте книгу")
        ok.append("окна: " + ", ".join(f"{k} {v}" for k, v in counts.items()
                                       if k != "OUT"))

    if verbose:
        print(f"── аудит книги {book.line or '?'} · {len(df)} строк ─────────")
        for m in ok:
            print(f"  ✓ {m}")
        for m in problems:
            print(f"  ✗ {m}")
        if not problems:
            print("  всё чисто")
        if book.trail:
            print(book.provenance())

    if problems and _MODE == "conclude":
        raise AuditError("книга не прошла аудит:\n  - " + "\n  - ".join(problems))
    return problems
