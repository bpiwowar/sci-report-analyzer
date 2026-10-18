"""The words finding the passages of a person's documents about their career (see
career_extract), by language, editable (Settings → Local LLM): headings (a page with one
among its first lines is sent whole), positions and diplomas (a line with one and a year is
sent). One word or phrase per line, compared without accents, nor case unless it has capitals
(``CV``: only in capitals); ``*``: any letters (e.g. ``recrut*``). Those of every language
are used together (a document may mix them)."""

from __future__ import annotations

import re
import unicodedata
from functools import cache

from .db.app_settings import get_setting, set_setting
from .i18n import N_

_SETTING = "career_words"
GROUPS = {
    "headings": N_("Headings (among the first lines of a page: sent whole)"),
    "positions": N_("Positions (a line with a year)"),
    "diplomas": N_("Diplomas (a line with a year)"),
}
LANGUAGES = {"en": "English", "fr": "Français"}

# (specific words: generic ones, e.g. “since”, “director”, “master”, match too many lines
# with a year, e.g. those of committees, teaching or students)
DEFAULTS: dict[str, dict[str, list[str]]] = {
    "en": {
        "headings": [
            "curriculum vitae",
            "CV",
            "résumé",
            "career",
            "professional experience",
            "employment",
            "positions held",
            "appointments",
            "education",
            "degrees",
            "biography",
            "biographical sketch",
        ],
        "positions": [
            "professor",
            "associate professor",
            "assistant professor",
            "full professor",
            "lecturer",
            "senior lecturer",
            "research scientist",
            "research fellow",
            "research director",
            "research engineer",
            "postdoc*",
            "post-doc*",
            "doctoral candidate",
            "sabbatical",
            "tenure*",
            "appointed",
            "promoted",
        ],
        "diplomas": [
            "phd",
            "ph.d",
            "doctorate",
            "dissertation",
            "habilitation",
            "msc",
            "master's degree",
            "master of science",
            "engineering degree",
            "bsc",
        ],
    },
    "fr": {
        "headings": [
            "curriculum vitae",
            "CV",
            "parcours",
            "parcours professionnel",
            "carrière",
            "expérience professionnelle",
            "situation professionnelle",
            "fonctions occupées",
            "postes occupés",
            "diplômes",
            "titres universitaires",
            "cursus",
            "notice biographique",
        ],
        "positions": [
            "professeur*",
            "maître de conférences",
            "maîtresse de conférences",
            "mcf",
            "ater",
            "enseignant-chercheur*",
            "enseignante-chercheuse",
            "chargé de recherche",
            "chargée de recherche",
            "directeur de recherche",
            "directrice de recherche",
            "ingénieur de recherche",
            "ingénieure de recherche",
            "ingénieur d'études",
            "post-doctora*",
            "postdoc*",
            "contrat doctoral",
            "cifre",
            "délégation",
            "crct",
            "détachement",
            "recruté*",
            "titularisé*",
            "promu*",
        ],
        "diplomas": [
            "doctorat",
            "thèse de doctorat",
            "docteur",
            "hdr",
            "habilitation à diriger des recherches",
            "diplôme de master",
            "master recherche",
            "dea",
            "diplôme d'ingénieur",
            "magistère",
            "agrégation",
        ],
    },
}


def normalize(text: str) -> str:
    """(compared, see pattern: no accents, single spaces)"""
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def fold(text: str) -> str:
    """(compared: lower case, no accents, single spaces)"""
    return normalize(text.casefold())


def words() -> dict[str, dict[str, list[str]]]:
    """The words by language and group (those saved, else the defaults)."""
    try:
        saved = get_setting(_SETTING, {}) or {}
    except Exception:  # (no database: e.g. in unit tests)
        saved = {}
    return {
        lang: {g: list(saved.get(lang, {}).get(g, DEFAULTS[lang][g])) for g in GROUPS}
        for lang in LANGUAGES
    }


def _changed(values: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    """The groups changed from the defaults."""
    out: dict[str, dict[str, list[str]]] = {}
    for lang in LANGUAGES:
        for g in GROUPS:
            ws = [w.strip() for w in values.get(lang, {}).get(g, []) if w.strip()]
            if ws != DEFAULTS[lang][g]:
                out.setdefault(lang, {})[g] = ws
    return out


def is_default(values: dict[str, dict[str, list[str]]]) -> bool:
    return not _changed(values)


def save_words(values: dict[str, dict[str, list[str]]]) -> None:
    """(a group as the default one: not saved, following the defaults)"""
    set_setting(_SETTING, _changed(values))
    _pattern.cache_clear()


@cache
def _pattern(group: str, items: tuple[str, ...]) -> re.Pattern | None:
    alternatives = []
    for w in items:
        parts = [re.escape(x).replace(r"\*", r"\w*") for x in normalize(w).split()]
        if parts:  # (with capitals: only with its case)
            body = r"\s+".join(parts)
            alternatives.append(body if any(c.isupper() for c in w) else f"(?i:{body})")
    if not alternatives:
        return None
    body = "|".join(sorted(alternatives, key=len, reverse=True))
    if group == "headings":  # (at the start of a line, after a number: “2. Parcours”)
        return re.compile(rf"^\s*(\d+[.)]?\s*)?({body})(?!\w)")
    return re.compile(rf"(?<!\w)({body})(?!\w)")


def pattern(group: str) -> re.Pattern | None:
    """What finds a group's words (of every language) in a normalized text (see normalize)."""
    items = tuple(w for by_group in words().values() for w in by_group[group])
    return _pattern(group, items)
