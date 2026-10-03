"""First level of classification: the *kind* of publication (mostly, of its venue).

international / national conference, workshop or journal · preprint · book · book chapter ·
edited proceedings · software · dataset · thesis · other.
The rank (CORE / quartile) is the second level; each kind can map to a default level
(e.g. every national conference counts as a given level) unless overridden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..i18n import N_, Labels
from .badge import Badge

KINDS: dict[str, str] = Labels(
    {
        "intl_conference": N_("International conference"),
        "intl_workshop": N_("International workshop"),
        "intl_journal": N_("International journal"),
        "natl_conference": N_("National conference"),
        "natl_workshop": N_("National workshop"),
        "natl_journal": N_("National journal"),
        "preprint": N_("Preprint"),
        "book": N_("Book"),
        "chapter": N_("Book chapter"),
        "proceedings": N_("Edited proceedings / volume"),
        "software": N_("Software"),
        "dataset": N_("Dataset"),
        "thesis": N_("Thesis (PhD & other)"),
        "other": N_("Other (report…)"),
    }
)
KIND_SHORT = Labels(
    {
        "intl_conference": N_("Intl. conf."),
        "intl_workshop": N_("Intl. workshop"),
        "intl_journal": N_("Intl. journal"),
        "natl_conference": N_("Natl. conf."),
        "natl_workshop": N_("Natl. workshop"),
        "natl_journal": N_("Natl. journal"),
        "preprint": N_("Preprint"),
        "book": N_("Book"),
        "chapter": N_("Chapter"),
        "proceedings": N_("Proceedings (ed.)"),
        "software": N_("Software"),
        "dataset": N_("Dataset"),
        "thesis": N_("Thesis"),
        "other": N_("Other"),
    }
)
# Unranked international venues: a muted tone of their family's ramp (conferences violet to
# blue, journals green; see BASE_CATEGORIES).
KIND_COLOUR = {
    "intl_conference": "#a3b1dc",
    "intl_workshop": "#c3cde8",
    "intl_journal": "#a3c28a",
    "natl_conference": "#bf8700",
    "natl_workshop": "#d4a72c",
    "natl_journal": "#9a6700",
    "preprint": "#afb8c1",
    "book": "#bf3989",
    "chapter": "#e26ba8",
    "proceedings": "#f0a3c8",
    "software": "#1b7c83",
    "dataset": "#3192aa",
    "thesis": "#8250df",
    "other": "#d0d7de",
}

WORKSHOP_KINDS = ("intl_workshop", "natl_workshop")
# Not papers in a venue: never ranked by it (software on Zenodo is not a preprint). Edited
# proceedings keep their venue's rank, in categories of their own (editing SIGIR's
# proceedings is not a SIGIR paper, but counts more than editing a C conference's).
UNRANKED_KINDS = ("software", "dataset")
# A publication's kind, never a venue's: a venue is not an edited volume (its proceedings,
# edited by someone, are among its records), nor a thesis.
PUBLICATION_ONLY_KINDS = ("proceedings", "thesis")
VENUE_KINDS = Labels({k: v for k, v in dict.items(KINDS) if k not in PUBLICATION_ONLY_KINDS})
# Not in a venue at all: a book's or a chapter's "venue" is its own title, series or
# publisher; a thesis', its university.
NO_VENUE_KINDS = ("book", "chapter", "thesis")
# Conference-like kinds (a paper in them is in "proceedings"; levels are CORE ranks).
CONFERENCE_LIKE = ("intl_conference", "natl_conference", *WORKSHOP_KINDS)

# A workshop: "Workshop on ...", "Trustworthy AI @ ACM Multimedia", "... co-located with
# ...". Its rank is that of its main conference.
WORKSHOP_RE = re.compile(
    r"\bworkshops?\b|\bateliers?\b|(?-i:(?<=\w)\s*@\s*(?=[A-Z]))|\bco-located\b"
    r"|\bin conjunction with\b",
    re.I,
)
# The main conference named in a workshop text ("X @ SIGIR 2021", "co-located with ECIR").
_HOST_RE = re.compile(
    r"(?-i:(?<=\w)\s*@\s*)(?P<a>[^,:;()]+)|(?:co-located|in conjunction) with (?:the )?"
    r"(?P<b>[^,:;()]+)",
    re.I,
)


def host_text(venue: str | None) -> str | None:
    """The main conference a workshop text names, if any."""
    m = _HOST_RE.search(venue or "")
    if not m:
        return None
    return (m.group("a") or m.group("b") or "").strip() or None


DEFAULT_NATIONAL_KEYWORDS = [
    "conférence",
    "journées",
    "journée",
    "colloque",
    "atelier",
    "rencontres",
    "francophone",
    "national",
    "nationale",
    "congrès",
    "CORIA",
    "TALN",
    "EGC",
    "CAp",
    "RJCIA",
    "INFORSID",
    "revue",
    "RSTI",
    "TSI",
]
DEFAULT_INTERNATIONAL_KEYWORDS = [
    "international",
    "european",
    "world",
    "asia",
    "pacific",
    "americas",
    "ACM",
    "IEEE",
    "AAAI",
    "IFIP",
    "Springer",
]

_CONFERENCE_RE = re.compile(
    r"\b(conf(erence)?|symposium|workshops?|proceedings|proc\.|meeting|congress|colloquium|"
    r"conférence|colloque|journées|atelier|rencontres|forum|summit)\b",
    re.I,
)
_JOURNAL_RE = re.compile(
    r"\b(journal|transactions|trans\.|revue|letters|review|magazine|annals|bulletin|"
    r"quarterly|j\.)\b",
    re.I,
)
# Document types (DBLP / HAL / OpenAlex / ORCID) that are neither papers in a conference
# nor in a journal.
# Edited volumes: proceedings, edited books (DBLP, HAL, Crossref).
_PROCEEDINGS_DOC_TYPES = {"Editorship", "DOUV", "proceedings", "edited-book"}
# Books and book chapters (edited volumes excluded).
_CHAPTER_DOC_TYPES = {"Incollection", "COUV", "book-chapter", "BookSection", "book-section"}
_BOOK_DOC_TYPES = _CHAPTER_DOC_TYPES | {
    "Book",
    "Incollection",
    "OUV",
    "COUV",
    "book",
    "book-chapter",
    "BookSection",
}
# Software and datasets (Zenodo / DataCite, HAL, ORCID, OpenAlex, Semantic Scholar; DBLP's
# "Data" covers both).
_SOFTWARE_DOC_TYPES = {"software", "Software", "SOFTWARE", "ComputationalNotebook"}
_DATASET_DOC_TYPES = {"dataset", "Dataset", "data-set", "Data"}
# Theses (DBLP, HAL's PhD thesis and habilitation "HDR", OpenAlex, ORCID): a thesis even when
# another source calls it a book (Semantic Scholar does for HAL's habilitations).
_THESIS_DOC_TYPES = {
    "Phdthesis",
    "Mastersthesis",
    "THESE",
    "HDR",
    "dissertation",
    "dissertation-thesis",
}
_OTHER_DOC_TYPES = {
    "Reference",
    "MEM",
    "REPORT",
    "LECTURE",
    "PATENT",
    "OTHER",
    "report",
    "editorial",
}
_CONFERENCE_DOC_TYPES = {"Inproceedings", "COMM", "POSTER", "conference-paper", "Conference"}
_JOURNAL_DOC_TYPES = {"Article", "ART", "journal-article", "article", "JournalArticle"}


def is_edited_volume(doc_type: str | None) -> bool:
    """An edited volume's record (proceedings, edited book), not a paper's."""
    return bool(set((doc_type or "").replace(",", " ").split()) & _PROCEEDINGS_DOC_TYPES)


def data_kind(doc_type: str | None) -> str | None:
    """ "software" or "dataset" when the document types say so (and none says it is a
    paper)."""
    types = set((doc_type or "").replace(",", " ").split())
    if types & (
        _CONFERENCE_DOC_TYPES | _JOURNAL_DOC_TYPES | _BOOK_DOC_TYPES | _PROCEEDINGS_DOC_TYPES
    ):
        return None
    if types & _SOFTWARE_DOC_TYPES:
        return "software"
    if types & _DATASET_DOC_TYPES:
        return "dataset"
    return None


@dataclass(frozen=True)
class KindEvidence:
    venue: str | None
    venue_type: str | None = None  # "conference" / "journal" hint from the source
    doc_type: str | None = None
    archival: bool = False


def _keyword_hit(text: str, words: list[str]) -> bool:
    for w in words:
        if not w:
            continue
        # Acronym-like keywords are matched case-sensitively, words case-insensitively.
        flags = 0 if w.isupper() or re.search(r"[A-Z].*[A-Z]", w) else re.I
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, flags):
            return True
    return False


def detect_kind(
    badge: Badge | None,
    ev: KindEvidence,
    *,
    national_keywords: list[str],
    international_keywords: list[str],
    unknown_scope: str = "international",
) -> str:
    """Classify a publication's venue (first level)."""
    if kind := data_kind(ev.doc_type):  # before preprints: Zenodo is an archive
        return kind
    if ev.archival or (badge is not None and badge.archival):
        return "preprint"
    venue = ev.venue or ""
    doc_types = set((ev.doc_type or "").replace(",", " ").split())
    if doc_types & _PROCEEDINGS_DOC_TYPES:
        return "proceedings"
    if doc_types & _THESIS_DOC_TYPES:
        return "thesis"

    # Books and chapters (unless a ranking lists the venue, e.g. LNCS proceedings).
    if doc_types & _BOOK_DOC_TYPES and not (badge and (badge.coreRank or badge.quartile)):
        return "chapter" if doc_types & _CHAPTER_DOC_TYPES else "book"

    # Workshops (unless a journal ranking lists the venue).
    journal_ranked = badge is not None and badge.source in ("scimago", "jcr") and badge.quartile
    if WORKSHOP_RE.search(venue) and not journal_ranked:
        natl = _keyword_hit(venue, national_keywords) or (
            unknown_scope == "national" and not _keyword_hit(venue, international_keywords)
        )
        return "natl_workshop" if natl else "intl_workshop"

    # Conference or journal?
    if badge is not None and badge.coreRank and badge.source in ("core", "manual"):
        form = "conference"
    elif ev.venue_type == "conference":
        # The sources say so (Scimago also lists some proceedings, e.g. NeurIPS's).
        form = "conference"
    elif badge is not None and badge.source in ("scimago", "jcr") and badge.quartile:
        form = "journal"
    elif doc_types & _CONFERENCE_DOC_TYPES:
        form = "conference"
    elif doc_types & _JOURNAL_DOC_TYPES or ev.venue_type == "journal":
        form = "journal"
    elif _CONFERENCE_RE.search(venue):
        form = "conference"
    elif _JOURNAL_RE.search(venue):
        form = "journal"
    elif doc_types & _OTHER_DOC_TYPES or not venue:
        return "other"
    else:
        form = "conference" if badge is not None and badge.type == "conference" else "journal"
    if not venue and doc_types & _OTHER_DOC_TYPES:
        return "other"

    # International or national?
    ranked = badge is not None and (
        (badge.coreRank and badge.source == "core")
        or (badge.quartile and badge.quartile.startswith("Q") and badge.source != "manual")
    )
    if ranked:
        scope = "intl"  # CORE / Scimago / JCR list international venues
    elif _keyword_hit(venue, national_keywords):
        scope = "natl"
    elif _keyword_hit(venue, international_keywords):
        scope = "intl"
    else:
        scope = "natl" if unknown_scope == "national" else "intl"
    return f"{scope}_{form}"
