"""Метрики — распределение целиком, всегда против baseline.

Два правила, оплаченных ошибками:

**Win-rate — ловушка.** В линии pump нашлось правило с 83% выигрышных сделок и
отрицательным матожиданием: много мелких плюсов и редкий огромный минус. Поэтому
:func:`describe` печатает среднюю, медиану, долю выигрышей, std и хвостовой
квантиль **вместе** — по одной цифре решение не принимается. Расхождение
медианы и средней показывает, в какую сторону перекошен хвост.

**Условное число без безусловного бессмысленно.** «49% вверх после всплеска
объёма» ничего не значит рядом с «49.8% вверх в случайную минуту». Отсюда
:func:`compare`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Round-trip по альтам/мемам на perp: тейкерская комиссия обе стороны плюс
#: проскальзывание. Edge ниже этого — не edge.
COST_ROUND_TRIP = 0.0015
FEE_SIDE = 0.00075


def describe(pnl, label: str = "") -> pd.Series:
    """Распределение доходности одной строкой.

    ``mean/std`` — риск-скорректированная мера на сделку, именно она решает.
    Сглаживать хвост бессмысленно, если это убивает среднюю быстрее: так
    провалился scale-order в линии pump, и так выиграл стоп.
    """
    x = pd.Series(pnl).dropna().astype(float)
    if not len(x):
        return pd.Series({"n": 0}, name=label or "pnl")
    return pd.Series({
        "n": len(x),
        "mean%": x.mean() * 100,
        "median%": x.median() * 100,
        "win%": (x > 0).mean() * 100,
        "std%": x.std() * 100,
        "q10%": x.quantile(0.10) * 100,
        "q90%": x.quantile(0.90) * 100,
        "mean/std": x.mean() / x.std() if x.std() else np.nan,
        "sum%": x.sum() * 100,
    }, name=label or "pnl")


def compare(pnl, baseline, label: str = "сигнал", base_label: str = "baseline") -> pd.DataFrame:
    """Сигнал против безусловного baseline — с разницей средних и t-статистикой.

    ``t`` здесь груба намеренно: события перекрываются во времени и
    кластеризуются, поэтому независимость нарушена и настоящий null строится
    скользящими окнами. Читайте её как «стоит ли вообще смотреть дальше»,
    а не как p-value.
    """
    a = pd.Series(pnl).dropna().astype(float)
    b = pd.Series(baseline).dropna().astype(float)
    out = pd.concat([describe(a, label), describe(b, base_label)], axis=1)
    if len(a) > 1 and len(b) > 1:
        se = np.sqrt(a.var() / len(a) + b.var() / len(b))
        out.loc["Δmean%", label] = (a.mean() - b.mean()) * 100
        out.loc["t", label] = (a.mean() - b.mean()) / se if se else np.nan
    return out


def net(gross, n_sides: int = 2, slip: float = 0.0):
    """Вычесть издержки из валовой доходности."""
    return gross - n_sides * FEE_SIDE - slip


def by_window(df: pd.DataFrame, value: str, entry_col: str = "entry_ts") -> pd.DataFrame:
    """Разбить метрику по TRAIN / VALID / TEST — обязательная разбивка."""
    from .windows import ORDER, assign_window
    w = assign_window(df, entry_col)
    rows = {name: describe(df.loc[w == name, value], name)
            for name in ORDER if (w == name).any()}
    return pd.DataFrame(rows)
