"""EventBook — таблица событий, каждая колонка которой знает, КОГДА она известна.

Мотивация — три вскрытия подряд, все с разным вектором:

* ``pump`` nb09_3: якорь события выбирался как «минута с максимальным
  15-минутным ростом» — это вершина, выбранная задним числом.
* ``pump_dump_v2`` nb07: веса траншей нормировались на реализованную длину
  кластера. Цена входа честная, размер — нет.
* ``pump_dump_v3`` nb03: ``E.merge(B, how='inner')`` молча сузил выборку до
  событий, доживших до +5%. Лукахед приехал через джойн.

Общее у всех трёх: величина из будущего попадала не в формулу доходности, а в
**выбор** — какой момент считать событием, какой размер поставить, какие строки
оставить в выборке. Отсюда контракт:

    у каждой колонки объявлен ``asof`` — за сколько минут до входа (или после)
    она становится известна. ``asof <= 0`` — причинная, её можно использовать
    для решения. ``asof > 0`` — исход, его можно только измерять.

Фильтровать, группировать, сортировать и обучать модель разрешено **только по
причинным колонкам**, и движок это проверяет: :meth:`EventBook.where` подсовывает
в ваш предикат рекордер, который запоминает каждое обращение к колонке.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import checks as _checks
from .windows import assign_window

#: Колонка известна в момент решения. Ставьте это всему, что считается из
#: прошлого: признаки бара-триггера, ликвидность, символ, час суток.
CAUSAL = 0


def outcome(minutes: int) -> int:
    """Отметить колонку как исход, известный через ``minutes`` после входа.

    ``schema={"pnl240": outcome(240)}`` читается как «доходность на горизонте
    240 минут» и автоматически запрещает фильтрацию по ней.
    """
    if minutes <= 0:
        raise ValueError("исход не может быть известен до входа; для причинных "
                         "колонок используйте CAUSAL или отрицательный asof")
    return minutes


#: Колонки, без которых книга не книга.
REQUIRED = {
    "sym":       CAUSAL,   # символ
    "signal_ts": CAUSAL,   # бар, на котором принято решение
    "entry_ts":  CAUSAL,   # бар, по которому реально вошли (строго позже)
    "frac":      CAUSAL,   # доля целевого размера, реально развёрнутая
}


class _Recorder:
    """Прокси над DataFrame, запоминающий, к каким колонкам обращались.

    Нужен, чтобы поймать лукахед в момент написания предиката, а не через два
    ноутбука. В строгом режиме падает сразу на обращении к колонке-исходу —
    с указанием, какая именно и когда она становится известна.
    """

    def __init__(self, df: pd.DataFrame, schema: dict[str, int]):
        object.__setattr__(self, "_df", df)
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "touched", set())

    def __getitem__(self, key):
        if isinstance(key, str):
            self.touched.add(key)
            asof = self._schema.get(key)
            if asof is None:
                _checks.violation(
                    f"колонка {key!r} использована в предикате, но её asof не "
                    f"объявлен в схеме книги — неизвестно, причинная она или исход")
            elif asof > 0:
                _checks.violation(
                    f"колонка {key!r} — исход (известна через {asof} мин после "
                    f"входа), по ней нельзя отбирать выборку: это тот самый "
                    f"selection-lookahead, что убил nb03 линии pump_dump_v3")
        return self._df[key]

    def __getattr__(self, name):
        if name.startswith("_") or name == "touched":
            raise AttributeError(name)
        return self[name]

    def __setattr__(self, name, value):
        raise AttributeError("рекордер только для чтения — стройте маску, "
                             "не меняйте книгу внутри предиката")


@dataclass
class _Step:
    """Одна операция над книгой — строка провенанса."""
    op: str
    why: str
    before: int
    after: int
    detail: str = ""

    @property
    def dropped_pct(self) -> float:
        return 0.0 if not self.before else (1 - self.after / self.before) * 100


@dataclass
class EventBook:
    """Книга событий: данные + схема ``asof`` + история того, как её сужали."""

    df: pd.DataFrame
    schema: dict[str, int]
    line: str = ""
    meta: dict = field(default_factory=dict)
    trail: list[_Step] = field(default_factory=list)

    # ---- интроспекция ------------------------------------------------------

    @property
    def causal(self) -> list[str]:
        """Колонки, по которым разрешено принимать решения."""
        return sorted(c for c, a in self.schema.items() if a <= 0)

    @property
    def outcomes(self) -> list[str]:
        """Колонки-исходы: только измерять, никогда не отбирать."""
        return sorted(c for c, a in self.schema.items() if a > 0)

    @property
    def undeclared(self) -> list[str]:
        """Колонки в данных, для которых asof не объявлен."""
        return sorted(set(self.df.columns) - set(self.schema))

    def __len__(self) -> int:
        return len(self.df)

    def __repr__(self) -> str:
        return (f"EventBook(line={self.line!r}, rows={len(self.df)}, "
                f"causal={len(self.causal)}, outcomes={len(self.outcomes)})")

    # ---- операции, которые нельзя сделать молча -----------------------------

    def where(self, predicate: Callable[[_Recorder], pd.Series], why: str) -> EventBook:
        """Сузить выборку предикатом от причинных колонок.

        ``predicate`` получает рекордер: обращайтесь к колонкам как ``c.liq``
        или ``c["liq"]``. Каждое обращение проверяется по схеме, попытка
        отобрать по исходу — нарушение.

            b2 = book.where(lambda c: c.liq > 1e5, why="ликвидные символы")

        ``why`` обязателен и попадает в провенанс: через месяц «почему тут
        12 043 строки вместо 70 858» должно отвечаться чтением, а не раскопками.
        """
        if not why or not why.strip():
            raise ValueError("укажите why= — зачем сужается выборка")
        rec = _Recorder(self.df, self.schema)
        mask = predicate(rec)
        mask = mask.fillna(False).astype(bool) if hasattr(mask, "fillna") else mask
        out = self.df[mask]
        step = _Step("where", why, len(self.df), len(out),
                     detail="по колонкам: " + ", ".join(sorted(rec.touched)))
        return self._advance(out, step)

    def join(self, right: pd.DataFrame, on, why: str, schema: dict[str, int],
             how: str = "left", narrowing: str | None = None) -> EventBook:
        """Приклеить колонки справа, объявив их ``asof`` и учтя потерю строк.

        Джойн — самый недооценённый вектор лукахеда: строки исчезают молча, а
        исчезают они не случайно. Если правая таблица сама была построена по
        условию на будущее («события, дошедшие до +5%»), то ``how='inner'``
        превращает её в фильтр по будущему — ровно это случилось в nb03.

        Потеря строк требует явного ``narrowing=`` с объяснением, почему
        выпавшие строки НЕ отобраны будущим.
        """
        if not why or not why.strip():
            raise ValueError("укажите why= — зачем нужен этот джойн")
        dup = set(schema) & set(self.schema)
        if dup:
            raise ValueError(f"колонки {sorted(dup)} уже есть в книге — "
                             f"переименуйте, иначе схема станет двусмысленной")
        out = self.df.merge(right, on=on, how=how)
        step = _Step("join", why, len(self.df), len(out),
                     detail=f"how={how}, ключ={on}, +{len(schema)} колонок")
        if len(out) < len(self.df):
            if narrowing is None:
                _checks.violation(
                    f"джойн отбросил {step.dropped_pct:.1f}% строк "
                    f"({len(self.df)} → {len(out)}), причина не объявлена. "
                    f"Если правая таблица — подвыборка по будущему условию, это "
                    f"selection-lookahead. Объявите narrowing='...' или how='left'")
            else:
                step.detail += f"; сужение обосновано: {narrowing}"
        book = self._advance(out, step)
        book.schema = {**self.schema, **schema}
        return book

    def add(self, name: str, values, asof: int, why: str = "") -> EventBook:
        """Добавить колонку, сразу объявив, когда она известна."""
        if name in self.schema:
            raise ValueError(f"колонка {name!r} уже объявлена (asof={self.schema[name]})")
        df = self.df.copy()
        df[name] = values
        book = self._advance(df, _Step("add", why or f"новая колонка {name}",
                                       len(self.df), len(df),
                                       detail=f"{name} asof={asof}"))
        book.schema = {**self.schema, name: asof}
        return book

    def _advance(self, df: pd.DataFrame, step: _Step) -> EventBook:
        b = EventBook(df.reset_index(drop=True), dict(self.schema), self.line,
                      dict(self.meta), [*self.trail, step])
        if _checks.current_mode() == "explore" and step.dropped_pct > 0:
            print(f"[выборка] {step.op}: {step.before} → {step.after} "
                  f"(−{step.dropped_pct:.1f}%) — {step.why}")
        return b

    # ---- провенанс ----------------------------------------------------------

    def provenance(self) -> str:
        """Человекочитаемая история: как из полной книги получилась текущая."""
        if not self.trail:
            return f"{self.line}: {len(self.df)} строк, без сужений"
        lines = [f"{self.line}: провенанс выборки"]
        for i, s in enumerate(self.trail, 1):
            arrow = f"{s.before} → {s.after}"
            pct = f" (−{s.dropped_pct:.1f}%)" if s.dropped_pct > 0 else ""
            lines.append(f"  {i}. {s.op}: {arrow}{pct} — {s.why}")
            if s.detail:
                lines.append(f"     {s.detail}")
        return "\n".join(lines)

    def by_window(self) -> pd.Series:
        """Сколько событий в каждом окне — первый вопрос к любой книге."""
        return assign_window(self.df).value_counts()

    # ---- хранение -----------------------------------------------------------

    def save(self, path: str | Path, params: dict | None = None,
             builder: str = "") -> Path:
        """Записать parquet и рядом манифест ``<name>.meta.json``.

        Манифест отвечает на вопрос, который однажды уже закрыл целую линию:
        «этой книге вообще можно верить — каким кодом и из какого сырья она
        собрана?» В v2 16 770 из 21 359 файлов сырья были переписаны под уже
        готовыми кешами, и заметили это не сразу.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_parquet(path)
        meta = {
            "line": self.line,
            "builder": builder,
            "built_at": pd.Timestamp.utcnow().isoformat(),
            "git_sha": _git_sha(),
            "rows": int(len(self.df)),
            "symbols": int(self.df["sym"].nunique()) if "sym" in self.df else None,
            "span": _span(self.df),
            "windows": {k: int(v) for k, v in self.by_window().items()},
            "schema": self.schema,
            "params": params or {},
            "trail": [vars(s) for s in self.trail],
            **self.meta,
        }
        path.with_suffix(".meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")
        print(f"[записано] {path} — {len(self.df)} строк, манифест рядом")
        return path

    @classmethod
    def read(cls, path: str | Path, line: str = "") -> EventBook:
        """Прочитать книгу вместе со схемой из манифеста."""
        path = Path(path)
        df = pd.read_parquet(path)
        mpath = path.with_suffix(".meta.json")
        if mpath.exists():
            meta = json.loads(mpath.read_text(encoding="utf-8"))
            schema = {k: int(v) for k, v in meta.get("schema", {}).items()}
            return cls(df, schema, line or meta.get("line", ""), meta)
        _checks.violation(
            f"у {path.name} нет манифеста {mpath.name}: схема asof неизвестна, "
            f"проверить причинность колонок невозможно")
        return cls(df, {}, line)


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[1],
                              timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _span(df: pd.DataFrame) -> list[str] | None:
    for c in ("entry_ts", "entry"):
        if c in df.columns and len(df):
            t = pd.to_datetime(df[c], utc=True)
            return [str(t.min()), str(t.max())]
    return None
