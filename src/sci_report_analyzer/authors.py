"""Author-name utilities: name keys and same-author tests."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .text import ascii_key, strip_diacritics


def natural_order(name: str) -> str:
    """ "Given Surname" order for names written "SURNAME Given" or "Surname, Given".

    A word in capitals (two letters or more) mixed with ordinary words is the surname, as in
    French administrative style ("NEVEOL Aurélie" -> "Aurélie NEVEOL").
    """
    name = " ".join(name.split())
    if name.count(",") == 1:
        last, first = (x.strip() for x in name.split(","))
        if last and first:
            return f"{first} {last}"
    words = name.split()

    def caps(w: str) -> bool:  # (not initials: "R.L. Gordon")
        letters = [c for c in w if c.isalpha()]
        return len(letters) >= 2 and all(c.isupper() for c in letters) and "." not in w

    upper = [w for w in words if caps(w)]
    if upper and len(upper) < len(words):
        return " ".join([w for w in words if not caps(w)] + upper)
    return name


def _fold(name: str) -> list[str]:
    s = strip_diacritics(natural_order(name)).lower()
    return [p for p in re.sub(r"[^a-z\s]", " ", s).split() if p]


def name_key(name: str) -> tuple[str, str]:
    """(surname, first initial), accent-/case-folded."""
    parts = _fold(name)
    if not parts:
        return "", ""
    return parts[-1], parts[0][0]


def surname(name: str) -> str:
    """The surname, whatever the order of the name ("Jane Doe", "Doe, Jane", "DOE Jane"),
    in lowercase ASCII, its parts joined ("Kronland-Martinet": "kronlandmartinet")."""
    words = [ascii_key(w) for w in natural_order(name).split()]
    return next((w for w in reversed(words) if w), "")


def same_author(a: str, b: str) -> bool:
    sa, ia = name_key(a)
    sb, ib = name_key(b)
    if not sa or sa != sb:
        return False
    return not ia or not ib or ia == ib


def author_position(authors: Iterable[str], names: Iterable[str]) -> int | None:
    """1-based position of the first author matching any of ``names``."""
    names = [n for n in names if n]
    for i, a in enumerate(authors, start=1):
        if any(same_author(a, n) for n in names):
            return i
    return None


def name_similarity(query: str, candidate: str) -> float:
    """Crude [0, 1] similarity between a searched name and a candidate profile name."""
    q, c = _fold(query), _fold(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if same_author(query, candidate):
        # Surname + initial agree; reward matching full given names.
        return 0.9 if set(q[:-1]) & set(c[:-1]) else 0.75
    shared = len(set(q) & set(c))
    return 0.5 * shared / max(len(set(q)), len(set(c)))


def fold_name(name: str) -> str:
    """Accent/case/punctuation-folded full name, for exact alias comparison."""
    return " ".join(_fold(name))
