"""Venue-string normalization.

All regexes use ``re.ASCII`` so that ``\\b``/``\\d`` behave like JavaScript's.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from pydantic import BaseModel, Field

# Words (and period-abbreviations of them) that carry no discriminating signal
# for venue matching. Discriminating abbreviations like "Comput." / "Inf." are
# resolved by prefix-aware matching in the matcher instead.
STOPWORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "for",
        "on",
        "in",
        "a",
        "an",
        "to",
        "international",
        "intl",
        "int",
        "natl",
        "national",
        "journal",
        "jrnl",
        "transactions",
        "trans",
        "proceedings",
        "proc",
        "conference",
        "conf",
        "symposium",
        "symp",
        "annual",
        "ann",
        "ieee",
        "acm",
        "society",
        "soc",
        "association",
        "assoc",
    ]
)

# Preprint servers / archival repositories that aren't ranked venues.
ARCHIVAL = frozenset(
    [
        "corr",
        "arxiv",
        "biorxiv",
        "medrxiv",
        "chemrxiv",
        "ssrn",
        "preprint",
        "preprints",
        "researchgate",
        "zenodo",
        "figshare",
        "osf",
        "vixra",
        "psyarxiv",
        "socarxiv",
        "eartharxiv",
        "techrxiv",
        "authorea",
        "repec",
        "hal",
        "eprint",
        "eprints",
    ]
)

_COMBINING = re.compile("[̀-ͯ]")
_NON_ALNUM = re.compile(r"[^a-z\d]+", re.ASCII)
_SPACES = re.compile(r"\s+")


def strip_diacritics(s: str) -> str:
    return _COMBINING.sub("", unicodedata.normalize("NFD", s))


def normalize(raw: str | None) -> str:
    """Lowercase, de-accented, punctuation-stripped, single-spaced."""
    if not raw:
        return ""
    s = strip_diacritics(str(raw).lower())
    s = s.replace("&", " and ")
    s = _NON_ALNUM.sub(" ", s)
    return _SPACES.sub(" ", s.strip())


def tokenize(raw: str | None) -> list[str]:
    """Normalized tokens with single chars and stopwords removed."""
    norm = normalize(raw)
    if not norm:
        return []
    return [t for t in norm.split(" ") if len(t) > 1 and t not in STOPWORDS]


def token_key(token: str) -> str:
    """Short index key so abbreviations and full words share a bucket."""
    return token[:3]


def tokens_match(a: str, b: str) -> bool:
    """Equal, or one is a (≥3 chars) prefix of the other ("comput" ↔ "computation")."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long.startswith(short)


def is_non_venue(norm: str) -> bool:
    """Whether a normalized venue is a preprint/archival repository."""
    if not norm:
        return False
    return norm.split(" ")[0] in ARCHIVAL


def _caps(token: str) -> int:
    return len(re.findall(r"[A-Z]", token))


class NormRule(BaseModel):
    """A venue-text normalization rule: a regex substitution (Python syntax, ``\\1``
    back-references), applied in order to the venue texts of the sources.

    Rules compile with ``re.ASCII`` (``\\b``, ``\\d``, ``\\w`` behave like JavaScript's).
    """

    id: str
    name: str
    description: str = ""
    pattern: str
    replacement: str = " "
    ignore_case: bool = False
    enabled: bool = True
    # Sources whose venue texts the rule applies to (empty: every source).
    sources: list[str] = Field(default_factory=list)
    # The language whose words the rule removes (Settings → Cleaning rules, by language);
    # none: a general rule.
    language: str | None = None
    example: str | None = None

    def compiled(self) -> re.Pattern[str] | None:
        try:
            rx = re.compile(self.pattern, re.ASCII | (re.I if self.ignore_case else 0))
            rx.sub(self.replacement, "")  # validates the replacement's group references
        except (re.error, IndexError):
            return None
        return rx

    def applies_to(self, source: str | None) -> bool:
        return self.enabled and (not self.sources or source in self.sources)

    def apply(self, text: str) -> str:
        rx = self.compiled()
        return rx.sub(self.replacement, text) if rx else text


# Word boundaries around digits (ASCII, as in JavaScript).
_B0, _B1 = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"

# Spelled ordinals, by their ending: "…th" (fourth, fourteenth, twentieth, hundredth;
# not "North", "Health": the stems are those of numbers), first / second / third.
ORDINAL_WORDS_EN = (
    r"(?:[a-z]+-)?(?:first|second|third|"
    r"(?:four|fif|six|seven|eigh|nin|ten|eleven|twelf|[a-z]+teen|[a-z]+ie|hundred)th)"
)
# "…ième" (deuxième, quatorzième, vingt-et-unième), premier / première.
ORDINAL_WORDS_FR = r"(?:[a-z-]+i[eè]me|premi(?:er|[eè]re))s?"
# An event word after an ordinal: "First Workshop on…", not the "Second Language" of a title.
_EVENT_EN = (
    r"(?=\s+(?:annual|international|national|european|asian|joint|acm|ieee|conference|"
    r"workshop|symposium|meeting|congress|colloquium|edition|forum|summit)\b)"
)

LANGUAGE_RULES: tuple[NormRule, ...] = (
    NormRule(
        id="ordinalWordsEn",
        name="Spelled ordinals",
        description="Remove ordinals in words (…th, first, second, third) before an event "
        "word (Annual, Conference, Workshop…).",
        pattern=rf"\b{ORDINAL_WORDS_EN}\b{_EVENT_EN}",
        ignore_case=True,
        language="en",
        example="Fourteenth ACM Conference on Recommender Systems",
    ),
    NormRule(
        id="ordinalWordsFr",
        name="Spelled ordinals",
        description="Remove ordinals in words (…ième, premier, première).",
        pattern=rf"(?<![a-zà-ÿ]){ORDINAL_WORDS_FR}(?![a-zà-ÿ])",
        ignore_case=True,
        language="fr",
        example="Quatorzième conférence en recherche d'information",
    ),
)

DEFAULT_NORM_RULES: tuple[NormRule, ...] = (
    *LANGUAGE_RULES,
    NormRule(
        id="parentheses",
        name="Parentheses",
        description="Remove parenthesised text (one level of nesting).",
        pattern=r"\((?:[^()]|\([^()]*\))*\)",
        example="Neural Information Processing Systems (NeurIPS)",
    ),
    NormRule(
        id="parenAcronym",
        name="Parenthesised acronym",
        description="Remove a parenthesised acronym (≥2 capitals); only matters when "
        "“Parentheses” is disabled.",
        pattern=r"\(\s*(?=[^\s()]*[A-Z][^\s()]*[A-Z])[^\s()]+\s*\)",
        example="Neural Information Processing Systems (NeurIPS) (Spotlight)",
    ),
    NormRule(
        id="trailingAcronym",
        name="Trailing conference acronym",
        description="Drop a trailing “, ACRONYM YEAR” tail that just repeats the conference "
        "name (the acronym must have ≥2 capitals, so journal names like “…, Nature 2019” are "
        "kept).",
        pattern=r",\s*(?=[^\s,]*[A-Z][^\s,]*[A-Z])[^\s,]+\s+(?:19|20)\d{2}\s*\Z",
        replacement="",
        example="IEEE Conference on Decision and Control, CDC 2025",
    ),
    NormRule(
        id="ordinals",
        name="Ordinals",
        description="Remove ordinal numbers such as 45th, 1st, 17e, 22èmes.",
        pattern=r"\b\d+(?:st|nd|rd|th|e|er|eme|ème)s?\b",
        ignore_case=True,
        example="45th Annual Meeting",
    ),
    NormRule(
        id="years",
        name="Years",
        description="Remove standalone 4-digit years (1900–2099).",
        pattern=r"\b(19|20)\d{2}\b",
        example="Nature Methods 2019",
    ),
    NormRule(
        id="volumeIssue",
        name="Volume (issue)",
        description="Remove “volume(issue)” numbers like 12(3).",
        pattern=r"\b\d+\s*\(\d+\)",
        example="J. Mach. Learn. 12(3)",
    ),
    NormRule(
        id="pageRanges",
        name="Page ranges",
        description="Remove page ranges like pp. 10–25.",
        pattern=r"\bpp?\.?\s*\d+\s*[-–]\s*\d+",
        ignore_case=True,
        example="Proc. of X, pp. 10-25",
    ),
    NormRule(
        id="separators",
        name="Separators",
        description="Replace , ; : with spaces.",
        pattern=r"[,;:]",
        example="Nature methods; journal",
    ),
    NormRule(
        id="bareNumbers",
        name="Bare numbers",
        description="Remove any leftover standalone numbers and numeric ranges.",
        pattern=r"\b\d[\d–-]*\b",
        example="ICML 139",
    ),
    NormRule(
        id="strayDashes",
        name="Stray dashes",
        description="Collapse dashes left dangling between words.",
        pattern=r"\s+[-–]\s+",
        example="ECCV - Workshops",
    ),
)

DEFAULT_RULE_IDS = tuple(r.id for r in DEFAULT_NORM_RULES)

_MULTISPACE = re.compile(r"\s{2,}")


def default_rules() -> list[NormRule]:
    return [r.model_copy(deep=True) for r in DEFAULT_NORM_RULES]


def apply_rules(segment: str | None, rules: Iterable[NormRule], source: str | None = None) -> str:
    """Clean a venue text with normalization rules (those applying to ``source``)."""
    if not segment:
        return ""
    v = str(segment).strip()
    for rule in rules:
        if rule.applies_to(source):
            v = rule.apply(v)
    return _MULTISPACE.sub(" ", v).strip()


def clean_venue(segment: str | None, *, strip_parens: bool = True) -> str:
    """Clean a venue text with the default rules.

    ``"Nature methods, 2019"`` → ``"Nature methods"``; word qualifiers are kept so
    ``"ECCV 2018 workshops"`` stays ``"ECCV workshops"``.
    """
    rules = [r for r in DEFAULT_NORM_RULES if strip_parens or r.id != "parentheses"]
    return apply_rules(segment, rules)
