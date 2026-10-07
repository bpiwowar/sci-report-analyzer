"""A venue's levels set by hand, by year range.

A level is ``{"type": "conference" | "journal", "rank": "A", "from": 2018, "to": None}``
(years included, either one open), with who set it (``by``), when (``on``, ISO date) and
why (``note``). The ranges never overlap; a paper of a year none covers takes the venue's
other ranking (its record, else automatic). The rank ``NONE`` says the venue was checked
and is in no ranking those years (unlike a venue no one looked at).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from itertools import pairwise
from typing import Any

from ..i18n import _

FIELDS = ("type", "rank", "from", "to", "by", "on", "note")
NONE = "none"  # the rank of a venue checked by hand and in no ranking


def clean(level: dict[str, Any]) -> dict[str, Any] | None:
    """A level with its known fields only, its years as ints (none without a rank)."""
    rank = str(level.get("rank") or "").strip()
    if not rank:
        return None
    out: dict[str, Any] = {"type": level.get("type") or "conference", "rank": rank}
    for f in ("from", "to"):
        if (y := level.get(f)) not in (None, ""):
            out[f] = int(y)
    for f in ("by", "on", "note"):
        if v := level.get(f):
            out[f] = str(v)
    return out


def clean_name(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A former name with its known fields only (none without a name)."""
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    out: dict[str, Any] = {"name": name}
    if short := str(entry.get("short_name") or "").strip():
        out["short_name"] = short
    for f in ("from", "to"):
        if (y := entry.get(f)) not in (None, ""):
            out[f] = int(y)
    for f in ("by", "on", "note"):
        if v := entry.get(f):
            out[f] = str(v)
    return out


def _start(level: dict[str, Any]) -> float:
    y = level.get("from")
    return float("-inf") if y is None else y


def _end(level: dict[str, Any]) -> float:
    y = level.get("to")
    return float("inf") if y is None else y


def normalized(
    levels: Iterable[dict[str, Any]] | None, cleaner=clean
) -> list[dict[str, Any]] | None:
    """The levels (or former names, ``cleaner=clean_name``) cleaned, by year; ``ValueError``
    when two ranges overlap or one ends before it starts. None when there is none."""
    out = sorted((c for lv in levels or () if (c := cleaner(lv))), key=_start)
    for lv in out:
        if _end(lv) < _start(lv):
            raise ValueError(
                _("the range {range} ends before it starts").format(range=range_label(lv))
            )
    for a, b in pairwise(out):
        if _start(b) <= _end(a):
            raise ValueError(
                _("the ranges {a} and {b} overlap").format(a=range_label(a), b=range_label(b))
            )
    return out or None


def at(levels: Iterable[dict[str, Any]] | None, year: int) -> dict[str, Any] | None:
    """The level of ``year`` (none: no range covers it)."""
    return next((lv for lv in levels or () if _start(lv) <= year <= _end(lv)), None)


def current(levels: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The level shown for the venue: this year's, else the latest range's."""
    if not levels:
        return None
    return at(levels, date.today().year) or max(levels, key=_end)


def covers_all(levels: list[dict[str, Any]] | None) -> bool:
    """Whether every year has a level (no year falls back to the venue's other ranking)."""
    if not levels:
        return False
    ordered = sorted(levels, key=_start)
    if _start(ordered[0]) != float("-inf") or _end(ordered[-1]) != float("inf"):
        return False
    return all(_start(b) == _end(a) + 1 for a, b in pairwise(ordered))


def range_label(level: dict[str, Any]) -> str:
    """Its years: "2018–", "–2017", "2015–2019", "2020" or "all years"."""
    lo, hi = level.get("from"), level.get("to")
    if lo is None and hi is None:
        return _("all years")
    if lo is not None and lo == hi:
        return str(lo)
    return f"{'' if lo is None else lo}–{'' if hi is None else hi}"


def rank_label(rank: str) -> str:
    """A level's rank as shown ("A", "Q1", "not ranked")."""
    return _("not ranked") if rank == NONE else rank


def label(levels: list[dict[str, Any]] | None) -> str:
    """The levels in a line: "B (–2017) · A (2018–)"."""
    if not levels:
        return ""
    if len(levels) == 1 and levels[0].get("from") is None and levels[0].get("to") is None:
        return rank_label(levels[0]["rank"])
    return " · ".join(f"{rank_label(lv['rank'])} ({range_label(lv)})" for lv in levels)
