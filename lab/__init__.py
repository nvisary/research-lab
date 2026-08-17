"""lab — движок ручных исследований.

Точка входа для всего, что раньше жило в ``notebooks/<line>/`` как набор
копипастящихся ``_lab.py``. Три линии исследования умерли от лукахеда,
пойманного через два-три ноутбука после того, как он въехал, — поэтому здесь
дисциплина выражена **кодом**, а не призывом к внимательности.

Типичное начало ноутбука::

    import lab
    lab.mode("explore")                  # разведка: аудит предупреждает
    book = lab.EventBook.load("pump_v4")

    # фильтровать можно только по причинным колонкам — движок это проверяет
    small = book.where(lambda c: c.liq > 1e5, why="ликвидные символы")

    lab.mode("conclude")                 # вывод: аудит блокирует
    print(lab.describe(small.df.pnl240))

Читать: ``lab/README.md`` (что это), ``lab/PLAYBOOK.md`` (как работаем).
"""
from __future__ import annotations

from .checks import AuditError, audit, mode, current_mode
from .book import CAUSAL, EventBook, outcome
from .data import cache_clear, load, scan, symbols
from .metrics import COST_ROUND_TRIP, compare, describe
from .plots import show
from .windows import WINDOWS, assign_window, open_test, window_of

__all__ = [
    "AuditError", "audit", "mode", "current_mode",
    "EventBook", "CAUSAL", "outcome",
    "load", "scan", "symbols", "cache_clear",
    "describe", "compare", "COST_ROUND_TRIP",
    "show",
    "WINDOWS", "window_of", "assign_window", "open_test",
]

__version__ = "0.1.0"
