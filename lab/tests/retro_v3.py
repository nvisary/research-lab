"""Ретро-аудит: движок против реальных книг pump_dump_v3.

Синтетические тесты в ``test_audit.py`` проверяют движок на выдуманных данных,
где ответ подогнан. Этот скрипт — честнее: он берёт настоящие книги закрытой
линии и проверяет, ловит ли движок **тот самый** лукахед, который в своё время
заметили только через два ноутбука после того, как он въехал.

    uv run python lab/tests/retro_v3.py

Ожидаемый результат — три пойманных нарушения. Если хоть одно проходит молча,
движок бракованный и доверять ему нельзя.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lab                                                   # noqa: E402
from lab.book import CAUSAL, EventBook, outcome               # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "notebooks/_archive/pump_dump_v3/_out"

HORIZONS = [15, 30, 60, 120, 240, 480, 720, 1440, 2880]
FEATURES = ["r1", "r3", "r5", "r15", "r30", "r60", "accel5", "accel", "surge15",
            "surge1", "rvol60", "volreg", "rng5", "rng15", "dist_hi240",
            "dist_lo60", "hi_d", "lo_d", "dstreak", "liq", "stream"]


def load_events() -> EventBook:
    df = pd.read_parquet(OUT / "events.parquet")
    # Книга v3 хранит только `entry` — момент исполнения. Сигнал был на баре
    # раньше: билдер решает на баре st и заливается по close[st+1].
    df = df.rename(columns={"entry": "entry_ts"})
    df["signal_ts"] = df["entry_ts"] - pd.Timedelta(minutes=1)
    schema = {c: CAUSAL for c in FEATURES if c in df.columns}
    schema |= {"sym": CAUSAL, "entry_ts": CAUSAL, "signal_ts": CAUSAL}
    # k и clen — глубина и длина кластера, они ИЗВЕСТНЫ ТОЛЬКО ПОСЛЕ его конца.
    # Именно нормировка на реализованное k убила v2, поэтому здесь они исходы.
    schema |= {"k": outcome(1), "clen": outcome(1), "stop_t": outcome(1)}
    schema |= {f"pnl{h}": outcome(h) for h in HORIZONS}
    return EventBook(df, schema, line="pump_dump_v3 (архив)")


def rule(n: int, title: str) -> None:
    print(f"\n{'═' * 72}\n  ПРОВЕРКА {n}. {title}\n{'═' * 72}")


def caught(exc: Exception | None, what: str) -> bool:
    if exc is None:
        print(f"  ✗✗ НЕ ПОЙМАНО: {what} — движок бракованный")
        return False
    print(f"  ✓ поймано: {str(exc).splitlines()[0][:150]}")
    return True


def main() -> int:
    if not (OUT / "events.parquet").exists():
        print(f"нет книг v3 в {OUT} — ретро-аудит пропущен")
        return 0

    ok = []
    lab.mode("explore")
    book = load_events()
    print(f"\nкнига: {book!r}")

    rule(1, "Аудит книги как она есть — что не объявлено")
    problems = lab.audit(book)
    ok.append(bool(problems))
    print(f"  → аудит нашёл {len(problems)} проблем(ы) в книге, "
          f"которая молча использовалась для выводов")

    rule(2, "Отбор по реализованной глубине кластера k (класс ошибки v2 nb07)")
    lab.mode("conclude")
    err = None
    try:
        book.where(lambda c: c.k >= 3, why="глубокие кластеры")
    except lab.AuditError as e:
        err = e
    ok.append(caught(err, "фильтр по k — длине кластера, известной лишь после его конца"))

    rule(3, "Отбор по исходу напрямую")
    err = None
    try:
        book.where(lambda c: c.pnl240 > 0, why="прибыльные события")
    except lab.AuditError as e:
        err = e
    ok.append(caught(err, "фильтр по pnl240"))

    rule(4, "Джойн из nb03: E ⋈ breed_preds, how='inner' (реальный баг)")
    ex = pd.read_parquet(OUT / "pump_exhaust.parquet").rename(columns={"entry": "entry_ts"})
    bp = pd.read_parquet(OUT / "breed_preds.parquet").rename(columns={"entry": "entry_ts"})
    print(f"  pump_exhaust: {len(ex):,} строк   breed_preds: {len(bp):,} строк")
    print(f"  breed_preds построен по событиям, ДОШЕДШИМ до +5% — условие на будущее")

    exb = EventBook(
        ex.assign(signal_ts=ex["entry_ts"] - pd.Timedelta(minutes=1), frac=1.0),
        {"sym": CAUSAL, "entry_ts": CAUSAL, "signal_ts": CAUSAL, "frac": CAUSAL,
         "k": outcome(1),
         **{c: outcome(240) for c in ex.columns if c.startswith(("stall", "cend"))}},
        line="pump_exhaust")

    err = None
    try:
        exb.join(bp[["sym", "entry_ts", "pred", "monster"]],
                 on=["sym", "entry_ts"], how="inner",
                 why="фильтр породы поверх точек истощения",
                 schema={"pred": CAUSAL, "monster": outcome(1440)})
    except lab.AuditError as e:
        err = e
    ok.append(caught(err, "inner join, сужающий выборку до дошедших до +5%"))

    rule(5, "Тот же джойн, объявленный честно — должен пройти")
    try:
        out = exb.join(bp[["sym", "entry_ts", "pred", "monster"]],
                       on=["sym", "entry_ts"], how="inner",
                       why="фильтр породы поверх точек истощения",
                       schema={"pred": CAUSAL, "monster": outcome(1440)},
                       narrowing="сознательно смотрим только подвыборку дошедших "
                                 "до +5%; торговых выводов на всей вселенной из "
                                 "неё не делаем")
        print(f"  ✓ прошло с объявленным сужением: {len(exb)} → {len(out)} "
              f"(−{out.trail[-1].dropped_pct:.1f}%)")
        print("\n" + out.provenance())
        ok.append(True)
    except lab.AuditError as e:
        print(f"  ✗ не должно было падать: {e}")
        ok.append(False)

    print(f"\n{'═' * 72}")
    print(f"  ИТОГ: {sum(ok)}/{len(ok)} проверок движка пройдено")
    print("═" * 72)
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
