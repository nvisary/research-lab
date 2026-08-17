"""CLI движка — намеренно тонкий.

    uv run python -m lab audit <book.parquet>   # проверить книгу и её манифест
    uv run python -m lab new <line>             # каркас новой линии
    uv run python -m lab lines                  # что есть и в каком состоянии

Всё остальное — это библиотека, вызываемая из ноутбука или билдера. Командами
сделано только то, что должно быть механическим и одинаковым каждый раз.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LINES = Path(__file__).resolve().parent / "lines"

README_TEMPLATE = """# {line} — журнал линии

Начато {date}. Движок: [`lab/`](../../README.md), правила работы —
[`lab/PLAYBOOK.md`](../../PLAYBOOK.md).

## Вопрос

<Одно предложение: какое явление проверяем и почему думаем, что там есть edge.>

## Гипотеза и kill-критерий

| # | гипотеза | что предсказывает | чем убивается |
|---|---|---|---|
| 1 | <...> | <...> | <...> |

Kill-критерий пишется ДО прогона. Без него любой результат можно объявить
частичным успехом, и линия живёт вечно.

## Конвенции

- Окна — из `lab.WINDOWS` (TRAIN 2024-01..2025-07 · VALID ..2026-02 · TEST ..2026-08).
- TEST открывается только через `lab.open_test("{line}", "зачем")`.
- Каждая книга собирается билдером `_build_*.py` и сохраняется через
  `EventBook.save()` — с манифестом.

## Унаследовано (что уже закрыто и сюда не возвращаемся)

См. [`notebooks/_archive/README.md`](../../../notebooks/_archive/README.md).

## Журнал

| nb | что | вердикт |
|---|---|---|
"""

NB_TEMPLATE = """{{
 "cells": [
  {{"cell_type": "markdown", "metadata": {{}},
   "source": ["# {line} · nb00 — <вопрос этого ноутбука>\\n",
              "\\n",
              "Один ноутбук — один вопрос. Вердикт в конце, строкой в README."]}},
  {{"cell_type": "code", "execution_count": null, "metadata": {{}}, "outputs": [],
   "source": ["import sys; sys.path.insert(0, r'{repo}')\\n",
              "import lab\\n",
              "from pathlib import Path\\n",
              "lab.plots.OUT = Path.cwd() / '_out'\\n",
              "lab.mode('explore')"]}},
  {{"cell_type": "code", "execution_count": null, "metadata": {{}}, "outputs": [],
   "source": ["# книга + аудит до любых выводов\\n",
              "# book = lab.EventBook.read('_out/events.parquet', line='{line}')\\n",
              "# lab.audit(book)"]}}
 ],
 "metadata": {{"kernelspec": {{"display_name": "Python 3 (ipykernel)",
   "language": "python", "name": "python3"}}, "language_info": {{"name": "python"}}}},
 "nbformat": 4, "nbformat_minor": 5
}}
"""


def cmd_audit(args) -> int:
    from .checks import audit as run_audit
    from .book import EventBook
    p = Path(args.path)
    if not p.exists():
        print(f"нет такого файла: {p}")
        return 2
    book = EventBook.read(p)
    problems = run_audit(book)
    return 1 if problems else 0


def cmd_new(args) -> int:
    import pandas as pd
    d = LINES / args.line
    if d.exists():
        print(f"линия {args.line} уже существует: {d}")
        return 2
    (d / "_out").mkdir(parents=True)
    repo = str(Path(__file__).resolve().parents[1]).replace("\\", "/")
    (d / "README.md").write_text(
        README_TEMPLATE.format(line=args.line,
                               date=pd.Timestamp.utcnow().strftime("%Y-%m-%d")),
        encoding="utf-8")
    (d / "nb00_start.ipynb").write_text(
        NB_TEMPLATE.format(line=args.line, repo=repo), encoding="utf-8")
    print(f"создана линия {args.line}:\n  {d / 'README.md'} — журнал, "
          f"заполните вопрос и kill-критерий\n  {d / 'nb00_start.ipynb'} — каркас")
    return 0


def cmd_lines(args) -> int:
    from .windows import count_test_looks
    if not LINES.exists() or not any(LINES.iterdir()):
        print("линий пока нет — начните с: uv run python -m lab new <имя>")
        return 0
    for d in sorted(p for p in LINES.iterdir() if p.is_dir()):
        nbs = sorted(d.glob("nb*.ipynb"))
        books = sorted(d.glob("_out/*.parquet"))
        looks = d / "TEST_LOOKS.md"
        n_looks = count_test_looks(looks.read_text(encoding="utf-8")) if looks.exists() else 0
        print(f"{d.name}: {len(nbs)} ноутбуков, {len(books)} книг, "
              f"TEST открывали {n_looks} раз")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="проверить книгу событий и её манифест")
    a.add_argument("path")
    a.set_defaults(func=cmd_audit)

    n = sub.add_parser("new", help="создать каркас новой линии")
    n.add_argument("line")
    n.set_defaults(func=cmd_new)

    ls = sub.add_parser("lines", help="показать линии и их состояние")
    ls.set_defaults(func=cmd_lines)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
