"""Регрессии на аудит: три вектора лукахеда, каждый стоил линии.

Проверяется не «код не падает», а «код падает там, где раньше не падал никто».
Каждый тест назван по вскрытию, из которого он вырос.
"""
from __future__ import annotations

import pandas as pd
import pytest

import lab
from lab.book import CAUSAL, EventBook, outcome


def make_book(n: int = 100, **over) -> EventBook:
    sig = pd.date_range("2024-03-01", periods=n, freq="7h", tz="UTC")
    df = pd.DataFrame({
        "sym": [f"S{i % 7}USDT" for i in range(n)],
        "signal_ts": sig,
        "entry_ts": sig + pd.Timedelta(minutes=1),
        "frac": 1.0,
        "liq": [1e4 * (i + 1) for i in range(n)],
        "r15": [0.05 + i * 1e-4 for i in range(n)],
        "pnl240": [(-1) ** i * 0.01 for i in range(n)],
    })
    df = df.assign(**over) if over else df
    schema = {"sym": CAUSAL, "signal_ts": CAUSAL, "entry_ts": CAUSAL,
              "frac": CAUSAL, "liq": CAUSAL, "r15": CAUSAL,
              "pnl240": outcome(240)}
    return EventBook(df, schema, line="test")


@pytest.fixture(autouse=True)
def _explore():
    """Каждый тест стартует в разведке; строгость включается явно."""
    lab.mode("explore")
    yield
    lab.mode("explore")


# ---- вектор 1: отбор по исходу (pump_dump_v3 nb03) -------------------------

def test_where_on_outcome_blocks_in_conclude():
    book = make_book()
    lab.mode("conclude")
    with pytest.raises(lab.AuditError, match="исход"):
        book.where(lambda c: c.pnl240 > 0, why="прибыльные события")


def test_where_on_outcome_warns_in_explore():
    book = make_book()
    out = book.where(lambda c: c.pnl240 > 0, why="разведка")
    assert len(out) < len(book)          # работа продолжается
    assert out.trail[-1].dropped_pct > 0  # но сужение записано


def test_where_on_causal_is_allowed_in_conclude():
    book = make_book()
    lab.mode("conclude")
    out = book.where(lambda c: c.liq > 5e5, why="ликвидные символы")
    assert len(out) < len(book)
    assert "liq" in out.trail[-1].detail


def test_where_on_undeclared_column_is_a_violation():
    book = make_book()
    book.df["mystery"] = 1
    lab.mode("conclude")
    with pytest.raises(lab.AuditError, match="asof"):
        book.where(lambda c: c.mystery > 0, why="что это вообще")


def test_where_requires_why():
    with pytest.raises(ValueError):
        make_book().where(lambda c: c.liq > 0, why="")


# ---- вектор 2: сужение через джойн (pump_dump_v3 nb03, буквально) ----------

def test_inner_join_dropping_rows_blocks_without_declared_reason():
    """Точная форма бага: правая таблица — подвыборка по будущему условию."""
    book = make_book(100)
    reached = book.df.loc[book.df.pnl240 > 0, ["sym", "entry_ts"]].copy()
    reached["pred"] = 0.5
    lab.mode("conclude")
    with pytest.raises(lab.AuditError, match="отбросил"):
        book.join(reached, on=["sym", "entry_ts"], how="inner",
                  why="предсказания породы", schema={"pred": CAUSAL})


def test_join_narrowing_passes_when_declared():
    book = make_book(100)
    reached = book.df.loc[book.df.pnl240 > 0, ["sym", "entry_ts"]].copy()
    reached["pred"] = 0.5
    lab.mode("conclude")
    out = book.join(reached, on=["sym", "entry_ts"], how="inner",
                    why="предсказания породы", schema={"pred": CAUSAL},
                    narrowing="осознанно: анализ только этой подвыборки, "
                              "торговых выводов из неё не делаем")
    assert "осознанно" in out.trail[-1].detail


def test_left_join_keeps_all_rows():
    book = make_book(100)
    right = book.df[["sym", "entry_ts"]].head(30).copy()
    right["pred"] = 1.0
    lab.mode("conclude")
    out = book.join(right, on=["sym", "entry_ts"], how="left",
                    why="признак там, где он есть", schema={"pred": CAUSAL})
    assert len(out) == len(book)


def test_join_rejects_duplicate_column_names():
    book = make_book()
    right = book.df[["sym", "entry_ts"]].copy()
    right["liq"] = 1.0
    with pytest.raises(ValueError, match="уже есть"):
        book.join(right, on=["sym", "entry_ts"], why="дубль",
                  schema={"liq": CAUSAL})


# ---- вектор 3: лукахед в размере (pump_dump_v2 nb07) -----------------------

def test_frac_above_one_is_caught():
    book = make_book()
    book.df.loc[:5, "frac"] = 1.4     # веса нормированы на реализованное k
    problems = lab.audit(book, verbose=False)
    assert any("frac > 1" in p for p in problems)


def test_missing_frac_is_caught():
    book = make_book()
    book.df = book.df.drop(columns=["frac"])
    book.schema.pop("frac")
    problems = lab.audit(book, verbose=False)
    assert any("frac" in p for p in problems)


# ---- вход не позже сигнала (pump nb09_2 / harness shift) -------------------

def test_entry_on_signal_bar_is_caught():
    book = make_book()
    book.df["entry_ts"] = book.df["signal_ts"]
    problems = lab.audit(book, verbose=False)
    assert any("не позже сигнала" in p for p in problems)


# ---- прочая гигиена книги --------------------------------------------------

def test_duplicate_events_are_caught():
    book = make_book(20)
    book.df = pd.concat([book.df, book.df.head(3)], ignore_index=True)
    problems = lab.audit(book, verbose=False)
    assert any("задвоен" in p for p in problems)


def test_undeclared_columns_are_caught():
    book = make_book()
    book.df["extra"] = 1
    problems = lab.audit(book, verbose=False)
    assert any("asof не объявлен" in p for p in problems)


def test_clean_book_passes_in_conclude():
    book = make_book()
    lab.mode("conclude")
    assert lab.audit(book, verbose=False) == []


def test_audit_raises_in_conclude_when_dirty():
    book = make_book()
    book.df["extra"] = 1
    lab.mode("conclude")
    with pytest.raises(lab.AuditError):
        lab.audit(book, verbose=False)


# ---- провенанс -------------------------------------------------------------

def test_provenance_records_the_chain():
    book = make_book(100)
    b2 = book.where(lambda c: c.liq > 2e5, why="ликвидность")
    b3 = b2.where(lambda c: c.r15 > 0.055, why="сильный триггер")
    text = b3.provenance()
    assert "ликвидность" in text and "сильный триггер" in text
    assert len(b3.trail) == 2


def test_add_column_declares_asof():
    book = make_book()
    b2 = book.add("hour", book.df.entry_ts.dt.hour, asof=CAUSAL, why="час суток")
    assert b2.schema["hour"] == CAUSAL
    lab.mode("conclude")
    assert len(b2.where(lambda c: c.hour < 12, why="первая половина")) > 0


def test_outcome_rejects_nonpositive_horizon():
    with pytest.raises(ValueError):
        outcome(0)


# ---- окна ------------------------------------------------------------------

def test_windows_are_contiguous_and_ordered():
    from lab.windows import ORDER, WINDOWS
    for a, b in zip(ORDER, ORDER[1:]):
        assert WINDOWS[a][1] == WINDOWS[b][0], "между окнами не должно быть дыр"


def test_window_boundary_belongs_to_the_later_window():
    assert lab.window_of("2025-07-01") == "VALID"
    assert lab.window_of("2025-06-30 23:59") == "TRAIN"


def test_test_look_ledger_counts_from_one(tmp_path):
    """Счётчик открытий holdout считает строки данных, не шапку таблицы."""
    assert lab.open_test("demo", "первый взгляд", root=tmp_path) == 1
    assert lab.open_test("demo", "второй", root=tmp_path) == 2
    from lab.windows import count_test_looks
    text = (tmp_path / "demo" / "TEST_LOOKS.md").read_text(encoding="utf-8")
    assert count_test_looks(text) == 2
    assert "первый взгляд" in text
