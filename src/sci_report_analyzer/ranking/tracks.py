"""Tracks: the satellite sessions of a venue (Findings, tutorials, demos, short papers…),
each with its names, colour and detection rules.

Their definitions are the settings' (``MatchSettings.tracks``, Settings → Tracks), in their
order (that of the distribution's categories, and in which they are detected); the built-in
ones are here (a code fallback: a saved list missing one gets it back). A track is known by
its id (``VenueKey.track``, a venue rule's track, ``Publication.track_override``).

A venue text is of a track when one of the track's rules (Python regexes, each general or of
a language) matches it. Findings is detected apart (``FINDINGS_ID``): a Findings volume is
also ranked as its main conference (see ``RankingService``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from pydantic import BaseModel, Field

from ..i18n import language

FINDINGS_ID = "findings"
# A publication's track override meaning "the main track" (no satellite track): see
# ``Publication.track_override``; never a track's id.
MAIN = "main"
FALLBACK_COLOUR = "#2f6fb0"


class TrackRule(BaseModel):
    """A regex finding a track in a venue text."""

    id: str
    pattern: str  # empty: matches nothing (a disabled rule)
    ignore_case: bool = True
    # The language whose words it matches (none: a general rule).
    language: str | None = None
    examples: list[str] = Field(default_factory=list)

    def compiled(self) -> re.Pattern[str] | None:
        """Its regex; none when invalid."""
        try:
            return re.compile(self.pattern, re.I if self.ignore_case else 0)
        except re.error:
            return None


class Track(BaseModel):
    """A track: its id, its name in each language ({"en": "Short", "fr": "Court"}), its
    colour (its chips, its categories' stripes) and its rules."""

    id: str
    names: dict[str, str] = Field(default_factory=dict)
    colour: str = FALLBACK_COLOUR
    rules: list[TrackRule] = Field(default_factory=list)

    def name(self, lang: str | None = None) -> str:
        """Its name in ``lang`` (that of the moment by default), else in English."""
        lang = lang or language()
        return self.names.get(lang) or self.names.get("en") or self.id.title()


def _rule(rid: str, pattern: str, lang: str | None, *examples: str) -> TrackRule:
    return TrackRule(id=rid, pattern=pattern, language=lang, examples=list(examples))


DEFAULT_TRACKS: tuple[Track, ...] = (
    Track(
        id=FINDINGS_ID,
        names={"en": "Findings", "fr": "Findings"},
        colour=FALLBACK_COLOUR,
        rules=[
            _rule(
                "track_findings",
                r"\bfindings\b",
                "en",
                "Findings of the Association for Computational Linguistics: ACL 2023",
            )
        ],
    ),
    Track(
        id="tutorial",
        names={"en": "Tutorial", "fr": "Tutoriel"},
        colour="#1a7f37",
        rules=[
            _rule("track_tutorial", r"\btutorials?\b", "en", "ECIR 2024 Tutorials"),
            _rule("track_tutorial_fr", r"\btutoriels?\b", "fr", "Foo 2024, tutoriels"),
        ],
    ),
    Track(
        id="demo",
        names={"en": "Demo", "fr": "Démo"},
        colour="#8a6fd0",
        rules=[
            _rule(
                "track_demo",
                r"\b(?:demos?|demonstrations?)\b",
                "en",
                "ACL 2023 (System Demonstrations)",
            ),
            _rule(
                "track_demo_fr",
                r"\b(?:démos?|démonstrations?)\b",
                "fr",
                "Foo 2024 (Démonstrations)",
            ),
        ],
    ),
    Track(
        id="short",
        names={"en": "Short", "fr": "Court"},
        colour="#d4a72c",
        rules=[
            _rule("track_short", r"\bshort papers?\b", "en", "ACL 2022 (Volume 2: Short Papers)"),
            _rule("track_short_fr", r"\barticles? courts?\b", "fr", "Foo 2024 (Articles courts)"),
        ],
    ),
)
DEFAULTS = {t.id: t for t in DEFAULT_TRACKS}
DEFAULT_RULES = {r.id: r for t in DEFAULT_TRACKS for r in t.rules}


def default_tracks() -> list[Track]:
    return [t.model_copy(deep=True) for t in DEFAULT_TRACKS]


def completed(tracks: Iterable[Track]) -> list[Track]:
    """The tracks as set (each id once, never ``MAIN``), a built-in one missing appended
    with its default."""
    out: dict[str, Track] = {}
    for t in tracks:
        if t.id and t.id != MAIN and t.id not in out:
            out[t.id] = t
    for d in DEFAULT_TRACKS:
        if d.id not in out:
            out[d.id] = d.model_copy(deep=True)
    return list(out.values())


def new_id(name: str, taken: Iterable[str]) -> str:
    """An id for a new track named ``name`` ("Industry papers" → "industry_papers")."""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", ascii_.lower()).strip("_") or "track"
    taken = {*taken, MAIN}
    out, n = base, 1
    while out in taken:
        n += 1
        out = f"{base}{n}"
    return out


def origin(track: Track) -> str:
    """ "default" (a built-in track as built in), "edited" (changed) or "added"."""
    d = DEFAULTS.get(track.id)
    if d is None:
        return "added"
    return "default" if track.model_dump() == d.model_dump() else "edited"


def rule_origin(rule: TrackRule) -> str:
    """ "default", "edited" or "added", as ``origin``."""
    d = DEFAULT_RULES.get(rule.id)
    if d is None:
        return "added"
    same = (rule.pattern, rule.ignore_case, rule.language) == (d.pattern, d.ignore_case, d.language)
    return "default" if same else "edited"


# ---- the tracks in force ------------------------------------------------------------------

# Those of the settings (``use``), with their regexes, compiled on first use.
_source: Callable[[], Iterable[Track]] | None = None
_compiled: tuple[list[Track], dict[str, list[re.Pattern[str]]]] | None = None


def _compile(tracks: Iterable[Track]) -> tuple[list[Track], dict[str, list[re.Pattern[str]]]]:
    tracks = completed(tracks)
    regexes: dict[str, list[re.Pattern[str]]] = {}
    for t in tracks:
        regexes[t.id] = []
        for r in t.rules:
            rx = r.compiled() if r.pattern else None
            if rx is None and r.pattern and (d := DEFAULT_RULES.get(r.id)):
                rx = d.compiled()  # an invalid built-in rule takes its default
            if rx is not None:
                regexes[t.id].append(rx)
    return tracks, regexes


def _in_force() -> tuple[list[Track], dict[str, list[re.Pattern[str]]]]:
    global _compiled
    if _compiled is None:
        _compiled = _compile(_source() if _source else ())
    return _compiled


def use(source: Callable[[], Iterable[Track]] | None) -> None:
    """Take the tracks in force from ``source`` (the settings), from their next use."""
    global _source
    _source = source
    reset()


def reset() -> None:
    """Forget the compiled tracks (the settings changed)."""
    global _compiled
    _compiled = None


@contextmanager
def using(tracks: Iterable[Track]) -> Iterator[None]:
    """The given tracks in force meanwhile (a preview of edited ones)."""
    global _compiled
    before = _compiled
    _compiled = _compile(tracks)
    try:
        yield
    finally:
        _compiled = before


def tracks() -> list[Track]:
    """The tracks in force, in their order."""
    return _in_force()[0]


def track_ids() -> list[str]:
    return [t.id for t in tracks()]


def get(track_id: str | None) -> Track | None:
    return next((t for t in tracks() if t.id == track_id), None)


def name(track_id: str, lang: str | None = None) -> str:
    """A track's name (an unknown one: its id)."""
    t = get(track_id)
    return t.name(lang) if t else track_id


def names() -> dict[str, str]:
    """Each track's name, in their order."""
    return {t.id: t.name() for t in tracks()}


def colour(track_id: str | None) -> str:
    t = get(track_id)
    return t.colour if t else FALLBACK_COLOUR


def colours() -> dict[str, str]:
    return {t.id: t.colour for t in tracks()}


def position(track_id: str) -> int | None:
    return next((i for i, t in enumerate(tracks()) if t.id == track_id), None)


def matches(track_id: str, text: str | None) -> re.Match[str] | None:
    """The leftmost match of the track's rules in ``text``."""
    found = (rx.search(text or "") for rx in _in_force()[1].get(track_id, ()))
    return min((m for m in found if m), key=lambda m: m.start(), default=None)


def detect(text: str | None, *, findings: bool = True) -> str | None:
    """The first track (in their order) one of whose rules matches ``text``, Findings last
    ("Findings of ACL: short papers" is a short paper); left out unless ``findings``."""
    for t in tracks():
        if t.id != FINDINGS_ID and matches(t.id, text):
            return t.id
    return FINDINGS_ID if findings and matches(FINDINGS_ID, text) else None


class TrackRegex:
    """A track's rules as set, looked up at each use: ``FINDINGS_RE.search(text)``."""

    def __init__(self, track_id: str) -> None:
        self.id = track_id

    def search(self, text: str) -> re.Match[str] | None:
        return matches(self.id, text)
