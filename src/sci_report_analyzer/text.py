"""Text helpers shared by the app's modules (folding, acronyms, regexes)."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_COMBINING = re.compile("[̀-ͯ]")


def strip_diacritics(text: str) -> str:
    """Accents stripped ("Névéol": "Neveol"); the other characters kept ("ð", "张")."""
    return _COMBINING.sub("", unicodedata.normalize("NFD", text))


def ascii_fold(text: str) -> str:
    """The text in ASCII: accents stripped, compatibility forms decomposed ("ﬁ": "fi"), the
    other non-ASCII characters dropped ("Guðmundsson": "Gumundsson"); case kept."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def ascii_key(text: str) -> str:
    """Lowercase ASCII letters and digits only ("Kronland-Martinet": "kronlandmartinet")."""
    return re.sub(r"[^a-z0-9]+", "", ascii_fold(text).lower())


def is_acronym(word: str) -> bool:
    """Whether a word looks like an acronym: two capitals or more ("ACL", "SIGIR", "NeurIPS")."""
    return sum(c.isupper() for c in word) >= 2


@lru_cache(maxsize=1024)
def safe_compile(pattern: str, ignore_case: bool = False, flags: int = 0) -> re.Pattern[str] | None:
    """A regex compiled (cached); none when invalid."""
    try:
        return re.compile(pattern, flags | (re.I if ignore_case else 0))
    except re.error:
        return None
