"""Translations: ``_("…")`` marks a user-facing string, translated into the language set
(Settings → the header's language menu; ``SCI_REPORT_ANALYZER_LANG`` overrides it) by its
catalog ``locales/<lang>/LC_MESSAGES/messages.po`` (none: English, the source language).

Strings are extracted with ``scripts/i18n.sh`` (pybabel: ``_``, ``ngettext`` and ``N_``,
which marks a string translated later, e.g. a label table built at import time: its
entries are shown through ``_()``).
"""

from __future__ import annotations

import gettext
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCALES = Path(__file__).parent / "locales"
LANGUAGES = {"en": "English", "fr": "Français"}
SETTING_KEY = "language"  # an AppSetting: {"lang": "fr"}
ENV = "SCI_REPORT_ANALYZER_LANG"

_translation: gettext.NullTranslations = gettext.NullTranslations()
_lang = "en"
_catalogs: dict[str, gettext.NullTranslations] = {}


def catalog(lang: str | None) -> gettext.NullTranslations:
    """The translations into ``lang`` (English, or no catalog: the strings themselves)."""
    lang = lang or "en"
    if lang not in _catalogs:
        po = LOCALES / lang / "LC_MESSAGES" / "messages.po"
        if lang == "en" or not po.exists():
            _catalogs[lang] = gettext.NullTranslations()
        else:
            from babel.messages.mofile import write_mo
            from babel.messages.pofile import read_po

            with po.open("rb") as f:
                cat = read_po(f, locale=lang)
            buf = io.BytesIO()
            write_mo(buf, cat)
            buf.seek(0)
            _catalogs[lang] = gettext.GNUTranslations(buf)
    return _catalogs[lang]


def set_language(lang: str | None) -> None:
    """Translate into ``lang`` (none, or an unknown one: English)."""
    global _translation, _lang
    _lang = lang if lang in LANGUAGES else "en"
    _translation = catalog(_lang)


@contextmanager
def using(lang: str) -> Iterator[None]:
    """Translate into ``lang`` within the block (e.g. a text in a language of its own)."""
    saved = _lang
    set_language(lang)
    try:
        yield
    finally:
        set_language(saved)


def language() -> str:
    return _lang


def load_language() -> None:
    """The language of the settings (the environment's, if set)."""
    from .db.app_settings import get_setting

    lang = os.environ.get(ENV) or (get_setting(SETTING_KEY) or {}).get("lang")
    set_language(lang)


def save_language(lang: str) -> None:
    from .db.app_settings import set_setting

    set_setting(SETTING_KEY, {"lang": lang})
    set_language(lang)


def _(text: str) -> str:
    return _translation.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def N_(text: str) -> str:
    """Marks ``text`` for extraction only: it is translated where shown, with ``_()``."""
    return text


class Labels(dict):
    """A label table built at import time (its strings marked with ``N_``): its values are
    translated when read, into the language of the moment."""

    def __getitem__(self, key):
        return _(super().__getitem__(key))

    def get(self, key, default=None):
        return _(v) if (v := super().get(key)) is not None else default

    def values(self):
        return [self[k] for k in self]

    def items(self):
        return [(k, self[k]) for k in self]

    def __iter__(self):
        return super().__iter__()

    def copy(self):
        return Labels(super().items())

    def __or__(self, other):
        return {**dict(self.items()), **other}

    def __ror__(self, other):
        return {**other, **dict(self.items())}


set_language(os.environ.get(ENV))
