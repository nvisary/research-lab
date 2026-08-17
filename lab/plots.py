"""Графики: единственный приём, который делает совместную работу зрячей.

Картинка рендерится инлайн для человека И кладётся файлом в ``_out/<name>.png``,
который читает Claude — base64-картинки внутри ``.ipynb`` для чтения слишком
велики. Заканчивайте каждую рисующую ячейку ``lab.show("имя")`` вместо
``plt.show()``, иначе обсуждать график будет только один из двоих.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

plt.rcParams.update({
    "figure.figsize": (11, 4.2),
    "figure.dpi": 110,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
})

#: Куда падают картинки. Устанавливается ноутбуком линии через ``lab.plots.OUT``
#: или берётся как ``_out`` рядом с текущей директорией.
OUT: Path | None = None


def out_dir() -> Path:
    d = OUT or Path.cwd() / "_out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def show(name: str = "plot", fig=None) -> Path:
    """Отрисовать инлайн и сохранить в ``_out/<name>.png``."""
    fig = fig or plt.gcf()
    fig.tight_layout()
    path = out_dir() / f"{name}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"[сохранено] {path}")
    return path
