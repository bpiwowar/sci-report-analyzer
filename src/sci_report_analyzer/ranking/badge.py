"""Badges (resolved venue rankings), rank categories and satellite tracks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any
from urllib.parse import quote

from ..i18n import N_, Labels, _
from .detection import Rule
from .matcher import Record, record_key

SOURCES = ("scimago", "core", "jcr", "openalex", "predatory", "manual", "archival")
TOGGLABLE_SOURCES = ("scimago", "core", "jcr", "openalex", "predatory")
SOURCE_LABELS = Labels(
    {
        "scimago": "Scimago (SJR)",
        "core": "CORE",
        "jcr": N_("JCR (imported)"),
        "openalex": N_("OpenAlex (live fallback)"),
        "predatory": N_("Predatory list"),
        "manual": N_("Manual"),
        "archival": N_("Archival"),
    }
)


@dataclass
class Badge:
    name: str
    source: str
    type: str
    sjr: float | None = None
    quartile: str | None = None
    hindex: int | None = None
    coreRank: str | None = None
    coreEdition: str | None = None
    coreId: str | None = None
    # Rank in each CORE edition listing the conference ({"CORE2018": "A", ...}).
    coreHistory: dict[str, str] | None = None
    # Scimago: the year of its fields, and its quartile each year ({"2021": "Q2", ...}).
    sjrYear: int | None = None
    sjrHistory: dict[str, str] | None = None
    impactFactor: float | None = None
    twoYearMeanCitedness: float | None = None
    worksCount: int | None = None
    score: float = 1.0
    exact: bool = True
    url: str | None = None
    recordKey: str | None = None
    corrected: bool = False
    predatory: bool = False
    predatoryUrl: str | None = None
    findings: bool = False
    manual: bool = False
    archival: bool = False
    forced: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Badge | None:
        if d is None:
            return None
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def copy(self, **changes: Any) -> Badge:
        d = self.to_dict()
        d.update(changes)
        return Badge(**d)

    def at_year(self, year: int | None) -> Badge:
        """The badge with the CORE rank of the edition in force in ``year``, or the Scimago
        quartile of the journal that year."""
        if self.sjrHistory and year is not None:
            y, q = sjr_quartile_at(self.sjrHistory, year)
            if (y, q) == (self.sjrYear, self.quartile):
                return self
            extra = {**self.extra, "sjrLatest": [self.sjrYear, self.quartile]}
            return self.copy(quartile=q, sjrYear=y, extra=extra)
        if not self.coreHistory or year is None:
            return self
        edition, rank = core_rank_at(self.coreHistory, year)
        if (edition, rank) == (self.coreEdition, self.coreRank):
            return self
        extra = {**self.extra, "coreLatest": [self.coreEdition, self.coreRank]}
        return self.copy(coreRank=rank, coreEdition=edition, extra=extra)

    @property
    def rank_label(self) -> str:
        if self.archival:
            return self.name or _("archival")
        if self.quartile:
            return f"JCR {self.quartile}" if self.source == "jcr" else self.quartile
        if self.coreRank:
            return f"CORE {self.coreRank}"
        if self.source == "openalex":
            return f"h {self.hindex}" if self.hindex is not None else "openalex"
        if self.predatory and self.source == "predatory":
            return _("⚠ predatory")
        return _("other")


# CORE editions (the year each one came out: it applies from then to the next one).
CORE_EDITIONS = (
    "CORE2008",
    "ERA2010",
    "CORE2013",
    "CORE2014",
    "CORE2017",
    "CORE2018",
    "CORE2020",
    "CORE2021",
    "CORE2023",
    "ICORE2026",
)


def edition_year(edition: str) -> int:
    m = re.search(r"\d{4}", edition)
    return int(m.group()) if m else 0


def core_rank_at(history: dict[str, str], year: int) -> tuple[str | None, str | None]:
    """(edition, rank) of a conference for a paper of ``year``.

    The edition in force is the last one out by then (the first one for older papers). A
    conference it doesn't list takes its rank from the closest edition before it (a gap),
    or after it when it wasn't ranked yet; one dropped since has no rank.
    """
    editions = sorted({*CORE_EDITIONS, *history}, key=edition_year)
    in_force = [e for e in editions if edition_year(e) <= year]
    current = in_force[-1] if in_force else editions[0]
    if current in history:
        return current, history[current]
    listed = sorted(history, key=edition_year)
    before = [e for e in listed if edition_year(e) < edition_year(current)]
    after = [e for e in listed if edition_year(e) > edition_year(current)]
    if before and not after:
        return current, None  # dropped
    e = before[-1] if before else after[0]
    return e, history[e]


def sjr_quartile_at(history: dict[str, str], year: int) -> tuple[int, str]:
    """(year, quartile) of a journal for a paper of ``year``: that year's, else the closest
    one before (not loaded, or not listed then), else the first one."""
    years = sorted(int(y) for y in history)
    before = [y for y in years if y <= year]
    y = before[-1] if before else years[0]
    return y, history[str(y)]


def sjr_periods(history: dict[str, str]) -> list[tuple[int, int, str]]:
    """A journal's quartiles over the years: (first year, last year, quartile)."""
    out: list[tuple[int, int, str]] = []
    for y in sorted(int(y) for y in history):
        q = history[str(y)]
        if out and out[-1][2] == q:
            out[-1] = (out[-1][0], y, q)
        else:
            out.append((y, y, q))
    return out


def core_periods(history: dict[str, str]) -> list[tuple[int, int, str]]:
    """A conference's CORE ranks over its editions: (first year, last year, rank), the
    consecutive editions with the same rank together."""
    out: list[tuple[int, int, str]] = []
    for e in sorted(history, key=edition_year):
        y, rank = edition_year(e), history[e]
        if out and out[-1][2] == rank:
            out[-1] = (out[-1][0], y, rank)
        else:
            out.append((y, y, rank))
    return out


def source_url(rec: Record) -> str | None:
    src = rec.get("source")
    if src == "scimago":
        if rec.get("sourceId"):
            return f"https://www.scimagojr.com/journalsearch.php?q={rec['sourceId']}&tip=sid"
        return f"https://www.scimagojr.com/journalsearch.php?q={quote(rec['name'])}&tip=jou"
    if src == "core":
        q = (rec.get("aliases") or [None])[0] or rec["name"]
        edition = rec.get("coreEdition") or "all"
        return f"https://portal.core.edu.au/conf-ranks/?search={quote(q)}&by=all&source={edition}"
    if src == "jcr":
        return f"https://jcr.clarivate.com/jcr/search-results?journal={quote(rec['name'])}"
    return rec.get("url")


def badge_from_record(rec: Record, score: float, exact: bool) -> Badge:
    return Badge(
        name=rec["name"],
        source=rec["source"],
        type=rec.get("type", "journal"),
        sjr=rec.get("sjr"),
        quartile=rec.get("quartile"),
        hindex=rec.get("hindex"),
        coreRank=rec.get("coreRank"),
        coreEdition=rec.get("coreEdition"),
        coreId=rec.get("coreId"),
        coreHistory=rec.get("coreHistory"),
        impactFactor=rec.get("impactFactor"),
        score=score,
        exact=exact,
        url=source_url(rec),
        recordKey=record_key(rec),
        sjrYear=rec.get("sjrYear"),
        sjrHistory=rec.get("sjrHistory") or None,
        extra={"aliases": rec.get("aliases") or []},
    )


def badge_from_level(level: dict[str, Any]) -> Badge:
    is_journal = level.get("type") == "journal"
    return Badge(
        name=level.get("name") or "",
        source="manual",
        type=level.get("type", "conference"),
        quartile=level["rank"] if is_journal else None,
        coreRank=None if is_journal else level["rank"],
        manual=True,
    )


ARCHIVAL_LABELS = {
    "corr": "arXiv",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "medrxiv": "medRxiv",
    "chemrxiv": "ChemRxiv",
    "ssrn": "SSRN",
    "zenodo": "Zenodo",
    "figshare": "figshare",
    "osf": "OSF",
    "vixra": "viXra",
    "psyarxiv": "PsyArXiv",
    "socarxiv": "SocArXiv",
    "eartharxiv": "EarthArXiv",
    "techrxiv": "TechRxiv",
    "authorea": "Authorea",
    "repec": "RePEc",
    "hal": "HAL",
}


def badge_archival(key: str) -> Badge:
    first = key.split(" ")[0]
    return Badge(
        name=ARCHIVAL_LABELS.get(first, "archival"),
        source="archival",
        type="journal",
        archival=True,
    )


# ---- Categories (distribution bar) ---------------------------------------------------------


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    colour: str
    base_key: str
    track: str | None = None
    workshop: bool = False  # a workshop, ranked as its main conference
    edited: bool = False  # proceedings edited (chair), ranked as their venue

    @property
    def striped(self) -> bool:
        return bool(self.track or self.workshop or self.edited)


# One hue ramp per family, darkest for the best level: journals from dark green to light
# yellow-green, conferences from dark violet to light blue.
BASE_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("q1", "Q1", "#0f5a3a"),
    ("q2", "Q2", "#2f8f4e"),
    ("q3", "Q3", "#7cbd4a"),
    ("q4", "Q4", "#c9e37a"),
    ("as", "CORE A*", "#3b1a8a"),
    ("a", "CORE A", "#5b3fc4"),
    ("b", "CORE B", "#4a7fd8"),
    ("c", "CORE C", "#9cc8f2"),
)


def text_colour(background: str) -> str:
    """Black or white, whichever reads better on a ``#rrggbb`` background (WCAG contrast)."""
    lin = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in (int(background[i : i + 2], 16) / 255 for i in (1, 3, 5))
    ]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#000" if luminance > 0.179 else "#fff"


_QUARTILE_KEYS = {"Q1": "q1", "Q2": "q2", "Q3": "q3", "Q4": "q4"}
_CORE_KEYS = {"A*": "as", "A": "a", "B": "b", "C": "c"}
KIND_ORDER = (
    "intl_conference",
    "intl_journal",
    "intl_workshop",
    "natl_conference",
    "natl_journal",
    "natl_workshop",
    "shared_task",
    "preprint",
    "book",
    "chapter",
    "proceedings",
    "software",
    "dataset",
    "thesis",
    "other",
)
# Ranked categories first, then unranked papers by venue kind ("k_<kind>").
RANK_ORDER = (
    "q1",
    "q2",
    "q3",
    "q4",
    "as",
    "a",
    "b",
    "c",
    *(f"k_{k}" for k in KIND_ORDER),
    "other",
    "unranked",
)
OTHER_COLOUR = "#57606a"
UNRANKED_COLOUR = "#d0d7de"
PREDATORY_COLOUR = "#cf222e"

TRACK_LABEL = Labels(
    {
        "findings": "Findings",
        "tutorial": N_("Tutorial"),
        "demo": N_("Demo"),
        "short": N_("Short"),
    }
)
# Workshops are a venue kind (ranked as their main conference), not a track.
TRACK_ORDER = ("findings", "tutorial", "demo", "short")
# Their regexes (Settings → Detection rules), tried in this order.
_TRACK_RE = tuple((Rule(f"track_{t}"), t) for t in ("tutorial", "demo", "short"))
FINDINGS_RE = Rule("track_findings")


def detect_track(venue: str | None) -> str | None:
    for rx, track in _TRACK_RE:
        if rx.search(venue or ""):
            return track
    return None


def _base(badge: Badge | None) -> tuple[str, str, str]:
    if badge is None:
        return "unranked", _("unranked"), UNRANKED_COLOUR
    key = _QUARTILE_KEYS.get(badge.quartile or "") or _CORE_KEYS.get(badge.coreRank or "")
    if key:
        for k, label, colour in BASE_CATEGORIES:
            if k == key:
                return k, label, colour
    return "other", _("other"), OTHER_COLOUR


def category_of(badge: Badge | None, track: str | None, kind: str | None = None) -> Category:
    """Distribution category: the rank when there is one, else the venue kind."""
    base_key, _label, colour = _base(badge)
    if kind in ("intl_workshop", "natl_workshop") and base_key not in ("other", "unranked"):
        # Its own category: "Workshop A*" (the rank of its main conference).
        cat = Category(f"workshop:{base_key}", "", colour, base_key, None, True)
    elif kind == "proceedings" and base_key not in ("other", "unranked"):
        # Its own category: "Proc. (ed.) CORE A*" (chairing an A* conference counts more).
        cat = Category(f"edited:{base_key}", "", colour, base_key, None, edited=True)
    else:
        if base_key in ("other", "unranked") and kind:
            from .kinds import KIND_COLOUR

            base_key, colour = f"k_{kind}", KIND_COLOUR[kind]
        key = f"{track}:{base_key}" if track else base_key
        cat = Category(key, "", colour, base_key, track)
    return replace(cat, label=category_label(cat))


def category_label(cat: Category) -> str:
    """``cat``'s label, in the language of the moment."""
    base = cat.base_key
    if base.startswith("k_"):
        from .kinds import KIND_SHORT

        base_label = KIND_SHORT[base[2:]]
    elif base in ("other", "unranked"):
        base_label = _("other") if base == "other" else _("unranked")
    else:
        base_label = next(label for k, label, _c in BASE_CATEGORIES if k == base)
    if cat.workshop:
        return _("Workshop {category}").format(category=base_label)
    if cat.edited:
        return _("Proc. (ed.) {category}").format(category=base_label)
    if not cat.track:
        return base_label
    name = TRACK_LABEL.get(cat.track, cat.track.title())
    # An unranked conference track reads "Intl. demo", not "Demo Intl. conf.".
    if base == "k_intl_conference":
        return _("Intl. {track}").format(track=name.lower())
    if base == "k_natl_conference":
        return _("Natl. {track}").format(track=name.lower())
    return _("{track} {category}").format(track=name, category=base_label)


def category_order(c: Category) -> int:
    track_pos = (TRACK_ORDER.index(c.track) + 1 if c.track in TRACK_ORDER else 6) if c.track else 0
    if c.workshop:
        track_pos = 8
    if c.edited:
        track_pos = 9
    return RANK_ORDER.index(c.base_key) * 10 + track_pos


# What each level means (shown next to manual-level editors and on the help page).
LEVEL_HELP: dict[str, str] = Labels(
    {
        "Q1": N_(
            "Journal quartile 1: top 25% of its subject category (Scimago SJR or JCR impact "
            "factor)."
        ),
        "Q2": N_("Journal quartile 2: top 25–50% of its category."),
        "Q3": N_("Journal quartile 3: top 50–75% of its category."),
        "Q4": N_("Journal quartile 4: bottom 25% of its category."),
        "A*": N_("CORE A*: flagship conference, a leading venue in its discipline area (~top 7%)."),
        "A": N_("CORE A: excellent conference, highly respected in its area (~next 17%)."),
        "B": N_("CORE B: good conference, well regarded in its area (~next 27%)."),
        "C": N_("CORE C: ranked conference meeting minimum standards."),
    }
)
