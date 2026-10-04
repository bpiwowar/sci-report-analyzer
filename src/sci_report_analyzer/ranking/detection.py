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
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from ..i18n import N_, Labels
from .inforce import InForce, leftmost
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
        "ranking": N_("Matching the rankings"),
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
        pattern=r"(?-i:(?<=[\w)\]])\s*@\s*(?=[A-Z]))",
        part_of="workshop",
        examples=("Trustworthy AI @ ACM Multimedia", "Foo Workshop (FooW) @ ECIR"),
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
        pattern=r"(?-i:(?<=[\w)\]])\s*@\s*)(?P<a>[^,:;()]+)",
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
        r"colloquium|forum|summit)(?!\w)",
        language="en",
        examples=("Symposium on Foo", "Proc. Foo"),
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
        r"bulletin|quarterly|j\.)(?!\w)",
        language="en",
        examples=("Annals of Foo", "J. Foo"),
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
    # A fuzzy match of a society or an event to a journal of the same distinctive words is
    # not one.
    DetectionDefault(
        id="not_journal",
        group="ranking",
        name=N_("Society or event"),
        description=N_(
            "A venue text naming a society or an event, not a journal: an approximate match to "
            "a journal of the same words is dropped (“Society for Neuroscience” is not the "
            "“Journal of Neuroscience”)."
        ),
        pattern=r"\b(society|association|meeting|congress|forum|summit)\b",
        language="en",
        examples=("Society for Foo",),
    ),
    DetectionDefault(
        id="not_journal_fr",
        group="ranking",
        name=N_("Society or event"),
        description=N_("A venue text naming a society or an event, not a journal."),
        pattern=r"\bsoci[ée]t[ée]s?\b",
        language="fr",
        part_of="not_journal",
        examples=("Société des gadgets",),
    ),
    DetectionDefault(
        id="generic_words",
        group="ranking",
        name=N_("Generic words"),
        description=N_(
            "Words any conference name can have, left out when comparing a venue text with the "
            "name of a ranking's record found by its acronym (they say nothing of which "
            "conference it is)."
        ),
        pattern=r"\b(international|national|annual|conferences?|symposium|workshop|proceedings|"
        r"meeting|joint|acm|ieee|ifip)\b",
        language="en",
        examples=("Annual International Conference on Foo",),
    ),
)
# Former defaults: a rule saved with one takes the current default.
FORMER_DEFAULTS: dict[str, tuple[str, ...]] = {
    "workshop_at": (r"(?-i:(?<=\w)\s*@\s*(?=[A-Z]))",),
    "workshop_host_at": (r"(?-i:(?<=\w)\s*@\s*)(?P<a>[^,:;()]+)",),
    "conference": (
        r"\b(conf(erence)?|symposium|workshops?|proceedings|proc\.|meeting|congress|"
        r"colloquium|forum|summit)\b",
    ),
    "journal": (
        r"\b(journal|transactions|trans\.|letters|review|magazine|annals|"
        r"bulletin|quarterly|j\.)\b",
    ),
}
DEFAULTS = {d.id: d for d in DEFAULT_DETECTION_RULES}
_REF = re.compile(r"\{rule:([\w-]+)\}")


def default_detection_rules() -> list[DetectionRule]:
    return [d.rule() for d in DEFAULT_DETECTION_RULES]


def completed(rules: Iterable[DetectionRule]) -> list[DetectionRule]:
    """The rules as set, in the defaults' order, a missing one (or one of a former default)
    taking its default (unknown ones are dropped)."""
    by_id = {r.id: r for r in rules}
    return [
        r if (r := by_id.get(d.id)) and r.pattern not in FORMER_DEFAULTS.get(d.id, ()) else d.rule()
        for d in DEFAULT_DETECTION_RULES
    ]


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
_defaults: dict[str, re.Pattern[str]] | None = None


def _default_regexes() -> dict[str, re.Pattern[str]]:
    global _defaults
    if _defaults is None:
        _defaults = {k: v for k, v in compile_rules(()).items() if v is not None}
    return _defaults


def _with_defaults(rules: Iterable[DetectionRule]) -> dict[str, re.Pattern[str]]:
    defaults = _default_regexes()
    return {k: v or defaults[k] for k, v in compile_rules(rules).items()}


_IN_FORCE: InForce[dict[str, re.Pattern[str]]] = InForce(_with_defaults)
use = _IN_FORCE.use
reset = _IN_FORCE.reset
using = _IN_FORCE.using


def regex(rule_id: str) -> re.Pattern[str]:
    """The regex of a rule, as set."""
    return _IN_FORCE.get()[rule_id]


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
        return leftmost((regex(p) for p in PARTS[self.id]), text, *args)
