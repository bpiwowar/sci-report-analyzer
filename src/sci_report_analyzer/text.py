"""Text helpers shared by the app's modules (folding, acronyms, regexes)."""

from __future__ import annotations

import unicodedata


def ascii_fold(text: str) -> str:
    """The text in ASCII: accents stripped, other non-ASCII characters dropped ("Névéol":
    "Neveol", "Guðmundsson": "Gumundsson"); case and punctuation kept."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
