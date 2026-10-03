"""Групповое переименование: шаблон с подстановками и предпросмотр.

Подстановки в шаблоне:
  *     — старое имя без расширения
  [E]   — расширение без точки
  [N]   — счётчик (начало/шаг настраиваются)
  [N03] — счётчик с дополнением нулями до 3 знаков (любая ширина)
"""

from __future__ import annotations

from .i18n import tr

import os
import re

_COUNTER = re.compile(r"\[N(\d*)\]")


def build_rename(names: list[str], template: str,
                 start: int = 1, step: int = 1) -> list[tuple[str, str]]:
    """Вернуть пары (старое, новое); ValueError — при дублях новых имён."""
    if not template:
        raise ValueError(tr("пустой шаблон"))
    seen: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    counter = start
    for name in names:
        base, ext = os.path.splitext(name)
        new = template.replace("*", base).replace("[E]", ext[1:])
        new = _COUNTER.sub(
            lambda m: f"{counter:0{int(m.group(1)) if m.group(1) else 0}d}", new)
        if new in seen:
            raise ValueError(tr("дубликат нового имени: {new!r} ({first!r} и {name!r})").format(new=new, first=seen[new], name=name))
        seen[new] = name
        pairs.append((name, new))
        counter += step
    return pairs
