"""Translations (on the way to i18n): ``_("…")`` marks a user-facing string, translated by
the catalog of the language set (``SCI_REPORT_ANALYZER_LANG``, e.g. "fr"; none: English), if
there is one: ``locales/<lang>/LC_MESSAGES/messages.mo`` (gettext; the strings are
extracted with ``xgettext -k_ -kngettext:1,2``)."""

from __future__ import annotations

import gettext
import os
from pathlib import Path

LOCALES = Path(__file__).parent / "locales"

_translation: gettext.NullTranslations = gettext.NullTranslations()


def set_language(lang: str | None) -> None:
    """Translate into ``lang`` (none, or no catalog for it: English)."""
    global _translation
    _translation = gettext.translation(
        "messages", LOCALES, languages=[lang] if lang else [], fallback=True
    )


def _(text: str) -> str:
    return _translation.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


set_language(os.environ.get("SCI_REPORT_ANALYZER_LANG"))
