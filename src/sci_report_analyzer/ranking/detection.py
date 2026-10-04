"""Detection rules: the regexes classifying venues (workshops and their main conference,
conferences vs journals, joint conferences); those of the tracks are the tracks' own
(``ranking.tracks``).

Their defaults are here; the patterns in force are the settings' (``MatchSettings.
detection_rules``, Settings → Detection rules), a rule missing from them or invalid taking
its default. ``{rule:<id>}`` in a pattern stands for another rule's pattern (in a group).

A rule is general or of a language (its words: "workshop", "atelier"); one of a language
can be part of another (``part_of``): a text matches the latter if it matches either.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import BaseModel

from ..i18n import N_, Labels
from .normalize import safe_compile


class DetectionRule(BaseModel):
    """A detection rule as set: its pattern (Python syntax) and case sensitivity."""

    id: str
    pattern: str
    ignore_case: bool = True


@dataclass(frozen=True)
class DetectionDefault:
    """A detection rule's built-in definition: what it decides, and its default."""

    id: str
    group: str
    name: str
    description: str
    pattern: str
    ignore_case: bool = True
    # The language whose words it matches (Settings → Detection rules, by language); none:
    # a general rule.
    language: str | None = None
    # The rule it is part of (searched with it: ``Rule(part_of)``); none: its own.
    part_of: str | None = None
    examples: tuple[str, ...] = ()

    @property
    def decides(self) -> str:
        """The rule whose decision it makes (its own, or the one it is part of)."""
        return self.part_of or self.id

    def rule(self) -> DetectionRule:
        return DetectionRule(id=self.id, pattern=self.pattern, ignore_case=self.ignore_case)


DETECTION_GROUPS = Labels(
    {
        "workshop": N_("Workshops"),
        "form": N_("Conference or journal"),
        "joint": N_("Joint conferences"),
    }
)

DEFAULT_DETECTION_RULES: tuple[DetectionDefault, ...] = (
    # "Workshop on ...", "Trustworthy AI @ ACM Multimedia", "... co-located with ...". Its
    # rank is that of its main conference.
    DetectionDefault(
        id="workshop",
        group="workshop",
        name=N_("Workshop"),
        description=N_(
            "A venue text naming a workshop (ranked as its main conference): “Workshop on…”, "
            "“co-located with…”."
        ),
        pattern=r"\bworkshops?\b|\bco-located\b|\bin conjunction with\b",
        language="en",
        examples=("Workshop on Foo", "Proc. of X, co-located with ECIR"),
    ),
    DetectionDefault(
        id="workshop_at",
        group="workshop",
        name=N_("Workshop @ conference"),
        description=N_("A venue text naming a workshop by its main conference: “X @ SIGIR”."),
        pattern=r"(?-i:(?<=\w)\s*@\s*(?=[A-Z]))",
        part_of="workshop",
        examples=("Trustworthy AI @ ACM Multimedia",),
    ),
    DetectionDefault(
        id="workshop_fr",
        group="workshop",
        name=N_("Workshop"),
        description=N_("A venue text naming a workshop (ranked as its main conference)."),
        pattern=r"\bateliers?\b",
        language="fr",
        part_of="workshop",
        examples=("Atelier sur les gadgets",),
    ),
    DetectionDefault(
        id="workshop_host",
        group="workshop",
        name=N_("Workshop's main conference"),
        description=N_(
            "The main conference a workshop text names: the text of the first group that "
            "matches (suggested as the workshop's host)."
        ),
        pattern=r"(?:co-located|in conjunction) with (?:the )?(?P<b>[^,:;()]+)",
        language="en",
        examples=("Proc. of X, co-located with ECIR",),
    ),
    DetectionDefault(
        id="workshop_host_at",
        group="workshop",
        name=N_("Workshop's main conference @"),
        description=N_("The main conference named after an “@”: “X @ SIGIR”."),
        pattern=r"(?-i:(?<=\w)\s*@\s*)(?P<a>[^,:;()]+)",
        part_of="workshop_host",
        examples=("Trustworthy AI @ ACM Multimedia",),
    ),
    # Used only when neither a ranking nor the sources tell.
    DetectionDefault(
        id="conference",
        group="form",
        name=N_("Conference"),
        description=N_(
            "A venue text naming a conference, when neither the rankings nor the sources say "
            "whether it is a conference or a journal."
        ),
        pattern=r"\b(conf(erence)?|symposium|workshops?|proceedings|proc\.|meeting|congress|"
        r"colloquium|forum|summit)\b",
        language="en",
        examples=("Symposium on Foo",),
    ),
    DetectionDefault(
        id="conference_fr",
        group="form",
        name=N_("Conference"),
        description=N_(
            "A venue text naming a conference, when neither the rankings nor the sources say "
            "whether it is a conference or a journal."
        ),
        pattern=r"\b(conférence|colloque|journées|ateliers?|rencontres|congrès)\b",
        language="fr",
        part_of="conference",
        examples=("Actes du colloque Foo",),
    ),
    DetectionDefault(
        id="journal",
        group="form",
        name=N_("Journal"),
        description=N_("A venue text naming a journal (after the conference rule)."),
        pattern=r"\b(journal|transactions|trans\.|letters|review|magazine|annals|"
        r"bulletin|quarterly|j\.)\b",
        language="en",
        examples=("Annals of Foo",),
    ),
    DetectionDefault(
        id="journal_fr",
        group="form",
        name=N_("Journal"),
        description=N_("A venue text naming a journal (after the conference rule)."),
        pattern=r"\brevues?\b",
        language="fr",
        part_of="journal",
        examples=("Revue des gadgets",),
    ),
    # Two conferences joined by "and" ("… Conference on X and the International Joint
    # Conference on Y"); IJCAI, a single "International Joint Conference", is not one.
    DetectionDefault(
        id="joint",
        group="joint",
        name=N_("Joint conference"),
        description=N_(
            "A venue text joining two conferences (its parts are then looked for among the venues)."
        ),
        pattern=r"\b(?:conference|symposium|meeting|workshop)\b.*\band\b(?:\s+the)?\b.*"
        r"\b(?:conference|symposium|meeting|workshop)\b",
        language="en",
        examples=("Conference on Foo and the International Conference on Bar",),
    ),
)
DEFAULTS = {d.id: d for d in DEFAULT_DETECTION_RULES}
_REF = re.compile(r"\{rule:([\w-]+)\}")


def default_detection_rules() -> list[DetectionRule]:
    return [d.rule() for d in DEFAULT_DETECTION_RULES]


def completed(rules: Iterable[DetectionRule]) -> list[DetectionRule]:
    """The rules as set, in the defaults' order, a missing one taking its default (unknown
    ones are dropped)."""
    by_id = {r.id: r for r in rules}
    return [by_id.get(d.id) or d.rule() for d in DEFAULT_DETECTION_RULES]


def _expand(pattern: str, patterns: dict[str, str], seen: frozenset[str] = frozenset()) -> str:
    def sub(m: re.Match[str]) -> str:
        ref = m.group(1)
        if ref not in patterns or ref in seen:
            return m.group(0)
        return f"(?:{_expand(patterns[ref], patterns, seen | {ref})})"

    return _REF.sub(sub, pattern)


def compile_rules(rules: Iterable[DetectionRule]) -> dict[str, re.Pattern[str] | None]:
    """Each rule's regex (its references expanded); none when invalid."""
    rules = completed(rules)
    patterns = {r.id: r.pattern for r in rules}
    out: dict[str, re.Pattern[str] | None] = {}
    for r in rules:
        out[r.id] = safe_compile(_expand(r.pattern, patterns, frozenset({r.id})), r.ignore_case)
    return out


# The rules in force: those of the settings (``use``), compiled on first use.
_source: Callable[[], Iterable[DetectionRule]] | None = None
_compiled: dict[str, re.Pattern[str]] | None = None
_defaults: dict[str, re.Pattern[str]] | None = None


def _default_regexes() -> dict[str, re.Pattern[str]]:
    global _defaults
    if _defaults is None:
        _defaults = {k: v for k, v in compile_rules(()).items() if v is not None}
    return _defaults


def _with_defaults(rules: Iterable[DetectionRule]) -> dict[str, re.Pattern[str]]:
    defaults = _default_regexes()
    return {k: v or defaults[k] for k, v in compile_rules(rules).items()}


def use(source: Callable[[], Iterable[DetectionRule]] | None) -> None:
    """Take the rules in force from ``source`` (the settings), from their next use."""
    global _source
    _source = source
    reset()


def reset() -> None:
    """Forget the compiled rules (the settings changed)."""
    global _compiled
    _compiled = None


@contextmanager
def using(rules: Iterable[DetectionRule]) -> Iterator[None]:
    """The given rules in force meanwhile (a preview of edited rules)."""
    global _compiled
    before = _compiled
    _compiled = _with_defaults(rules)
    try:
        yield
    finally:
        _compiled = before


def regex(rule_id: str) -> re.Pattern[str]:
    """The regex of a rule, as set."""
    global _compiled
    if _compiled is None:
        _compiled = _with_defaults(_source() if _source else ())
    return _compiled[rule_id]


# Each rule's own and those part of it ({"workshop": ("workshop", "workshop_at", …)}).
PARTS = {
    d.id: tuple(p.id for p in DEFAULT_DETECTION_RULES if p.decides == d.id)
    for d in DEFAULT_DETECTION_RULES
    if d.part_of is None
}


class Rule:
    """A rule's regexes as set (its own and its parts'), looked up at each use:
    ``WORKSHOP_RE.search(text)``."""

    def __init__(self, rule_id: str) -> None:
        self.id = rule_id

    def search(self, text: str, *args) -> re.Match[str] | None:
        """The leftmost match of its regexes (the first one's, on a tie)."""
        found = (regex(p).search(text, *args) for p in PARTS[self.id])
        return min((m for m in found if m), key=lambda m: m.start(), default=None)
