"""Spelled ordinals, up to 1000, as regexes: a cleaning rule names them in its pattern
(``{ordinals:en}``, ``{ordinals:fr}``), expanded when it compiles.

The words of an ordinal are separated by spaces or hyphens ("vingt et unième",
"vingt-et-unième", "twenty first", "twenty-first").
"""

from __future__ import annotations

import re
from functools import cache

_FR_UNITS = [
    "zéro",
    "un",
    "deux",
    "trois",
    "quatre",
    "cinq",
    "six",
    "sept",
    "huit",
    "neuf",
    "dix",
    "onze",
    "douze",
    "treize",
    "quatorze",
    "quinze",
    "seize",
    "dix-sept",
    "dix-huit",
    "dix-neuf",
]
_FR_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _fr_below_100(n: int) -> str:
    if n < 20:
        return _FR_UNITS[n]
    tens, unit = divmod(n, 10)
    if tens in (7, 9):  # soixante-dix…, quatre-vingt-dix…
        base = "soixante" if tens == 7 else "quatre-vingt"
        return f"{base}{' et ' if n == 71 else '-'}{_FR_UNITS[10 + unit]}"
    base = "quatre-vingt" if tens == 8 else _FR_TENS[tens]
    if unit == 0:
        return "quatre-vingts" if tens == 8 else base
    return f"{base}{' et ' if unit == 1 and tens != 8 else '-'}{_FR_UNITS[unit]}"


def french_cardinal(n: int) -> str:
    """1 ≤ n ≤ 1000, in words ("quatre-vingt-onze", "deux cents", "mille")."""
    if n == 1000:
        return "mille"
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _fr_below_100(rest)
    head = "cent" if hundreds == 1 else f"{_FR_UNITS[hundreds]} cent"
    if not rest:
        return head + ("s" if hundreds > 1 else "")
    return f"{head} {_fr_below_100(rest)}"


def french_ordinal(n: int) -> list[str]:
    """The ordinal(s) of n ≥ 2 in words ("deuxième", "second", "vingt et unième")."""
    if n == 1:
        return ["premier", "première"]
    word = french_cardinal(n)
    if word.endswith("quatre-vingts") or word.endswith("cents"):
        word = word[:-1]
    if word.endswith("cinq"):
        word += "u"
    elif word.endswith("neuf"):
        word = word[:-1] + "v"
    elif word.endswith("e"):
        word = word[:-1]
    out = [word + "ième"]
    if n == 2:
        out += ["second", "seconde"]
    return out


_EN_UNITS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_EN_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_EN_IRREGULAR = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}


def english_cardinals(n: int) -> list[str]:
    """1 ≤ n ≤ 1000 in words, with and without "and" ("one hundred (and) one")."""
    if n == 1000:
        return ["one thousand", "thousand"]
    hundreds, rest = divmod(n, 100)
    if rest < 20:
        tail = _EN_UNITS[rest]
    else:
        tens, unit = divmod(rest, 10)
        tail = _EN_TENS[tens] + (f"-{_EN_UNITS[unit]}" if unit else "")
    if not hundreds:
        return [tail]
    head = f"{_EN_UNITS[hundreds]} hundred"
    if not rest:
        return [head] + (["hundred"] if hundreds == 1 else [])
    return [f"{head} {tail}", f"{head} and {tail}"]


def english_ordinal(n: int) -> list[str]:
    out = []
    for word in english_cardinals(n):
        *before, last = re.split(r"(?<=[ -])", word)
        if last in _EN_IRREGULAR:
            last = _EN_IRREGULAR[last]
        elif last.endswith("y"):
            last = last[:-1] + "ieth"
        else:
            last += "th"
        out.append("".join(before) + last)
    return out


def _trie_regex(words: list[str]) -> str:
    """A compact regex matching exactly ``words`` (the separators " " and "-" match any run
    of spaces or hyphens; an accented "è"/"é" also matches the letter without accent)."""
    trie: dict = {}
    for w in words:
        node = trie
        for ch in re.sub(r"[ -]+", " ", w):
            node = node.setdefault(ch, {})
        node[""] = {}

    def char(ch: str) -> str:
        return {" ": r"[\s-]+", "è": "[eèÈ]", "é": "[eéÉ]"}.get(ch, re.escape(ch))

    def walk(node: dict) -> str:
        ends = "" in node
        branches = [char(ch) + walk(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else f"(?:{'|'.join(branches)})"
        if ends:
            return f"(?:{body})?"
        return body

    return walk(trie)


MAX = 1000


@cache
def word_lists() -> dict[str, str]:
    """The regexes a rule names in its pattern, by name (``{ordinals:fr}``)."""
    en = [w for n in range(1, MAX + 1) for w in english_ordinal(n)]
    fr = [w for n in range(1, MAX + 1) for w in french_ordinal(n)]
    return {
        "ordinals:en": _trie_regex(sorted(set(en))),
        "ordinals:fr": _trie_regex(sorted({*fr, *(w + "s" for w in fr)})),
    }


def expand(pattern: str) -> str:
    """``pattern`` with the word lists it names (``{ordinals:fr}``) as regexes."""
    if "{" not in pattern:
        return pattern
    for name, rx in word_lists().items():
        pattern = pattern.replace("{" + name + "}", f"(?:{rx})")
    return pattern
