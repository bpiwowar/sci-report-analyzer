"""Detection rules: the regexes classifying venues and papers (shared tasks, workshops and
their main conference, conferences vs journals, tracks, joint conferences).

Their defaults are here; the patterns in force are the settings' (``MatchSettings.
detection_rules``, Settings → Detection rules), a rule missing from them or invalid taking
its default. ``{rule:<id>}`` in a pattern stands for another rule's pattern (in a group).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import BaseModel

from ..i18n import N_, Labels


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
    examples: tuple[str, ...] = ()

    def rule(self) -> DetectionRule:
        return DetectionRule(id=self.id, pattern=self.pattern, ignore_case=self.ignore_case)


DETECTION_GROUPS = Labels(
    {
        "shared_task": N_("Shared tasks"),
        "workshop": N_("Workshops"),
        "form": N_("Conference or journal"),
        "track": N_("Tracks"),
        "joint": N_("Joint conferences"),
    }
)

DEFAULT_DETECTION_RULES: tuple[DetectionDefault, ...] = (
    # Shared tasks and evaluation campaigns (SemEval, TREC, CLEF labs, NTCIR...): their
    # working notes. A venue mixing them with research papers (WMT, BioNLP's "Workshop and
    # Shared Task") is not one: its papers' titles tell.
    DetectionDefault(
        id="shared_task_venue",
        group="shared_task",
        name=N_("Shared task venue"),
        description=N_(
            "A venue text naming an evaluation campaign or its working notes: its papers are "
            "shared task papers (unranked), unless in a journal."
        ),
        pattern=r"\bworking notes\b|\bsemantic evaluations?\b|\bText REtrieval Conference\b"
        r"|\bevaluation campaigns?\b|\bbenchmarking initiative\b"
        r"|\bForum for Information Retrieval Evaluation\b"
        r"|(?-i:\b(?:SemEval|TREC|NTCIR|MediaEval|ImageCLEF|LifeCLEF)\b)",
        examples=("Working Notes of CLEF 2025", "Proceedings of SemEval-2017"),
    ),
    DetectionDefault(
        id="campaigns",
        group="shared_task",
        name=N_("Evaluation campaigns"),
        description=N_(
            "The names of the evaluation campaigns, used by the shared task paper rule as "
            "{rule:campaigns} (case-sensitive there)."
        ),
        pattern=r"SemEval|TREC|NTCIR|MediaEval|WMT|IWSLT|CLEF|ImageCLEF|LifeCLEF|BioASQ"
        r"|CheckThat!?|eRisk|Touché|FIRE|DEFT|GermEval|IberLEF|EvaLatin|MIREX",
        ignore_case=False,
        examples=("SemEval",),
    ),
    # A participant's or an organiser's paper: "X at SemEval-2017 Task 12: ...", "Findings
    # of the WMT 2018 ... Shared Task", "Overview of the CLEF eHealth Evaluation Lab 2016",
    # "... Notebook for the ImageCLEF Lab at CLEF 2025".
    DetectionDefault(
        id="shared_task_title",
        group="shared_task",
        name=N_("Shared task paper"),
        description=N_(
            "A paper title saying it is a participant's or an organiser's paper of a shared "
            "task: the paper is a shared task paper, whatever its venue (unless a journal)."
        ),
        pattern=r"\bshared[- ]tasks?\b|\bnotebook for the\b"
        r"|\b(?:evaluation|benchmarking) (?:lab|campaign)s?\b"
        r"|(?-i:\b{rule:campaigns}(?:[- ]?(?:19|20)\d{2}\b|\s+Task\s*\d))"
        r"|(?:\bat|@)\s+(?:the\s+)?(?-i:{rule:campaigns}\b)"
        r"|^\s*(?:an\s+)?overview\s+of\b.*\b(?:lab|track|task|challenge)\b",
        examples=(
            "Team Foo at SemEval-2017 Task 12: Clinical Events",
            "Overview of the CLEF eHealth Evaluation Lab 2016",
        ),
    ),
    # "Workshop on ...", "Trustworthy AI @ ACM Multimedia", "... co-located with ...". Its
    # rank is that of its main conference.
    DetectionDefault(
        id="workshop",
        group="workshop",
        name=N_("Workshop"),
        description=N_(
            "A venue text naming a workshop (ranked as its main conference): “Workshop on…”, "
            "“X @ SIGIR”, “co-located with…”."
        ),
        pattern=r"\bworkshops?\b|\bateliers?\b|(?-i:(?<=\w)\s*@\s*(?=[A-Z]))|\bco-located\b"
        r"|\bin conjunction with\b",
        examples=("Trustworthy AI @ ACM Multimedia", "Proc. of X, co-located with ECIR"),
    ),
    DetectionDefault(
        id="workshop_host",
        group="workshop",
        name=N_("Workshop's main conference"),
        description=N_(
            "The main conference a workshop text names: the text of the first group that "
            "matches (suggested as the workshop's host)."
        ),
        pattern=r"(?-i:(?<=\w)\s*@\s*)(?P<a>[^,:;()]+)|(?:co-located|in conjunction) with "
        r"(?:the )?(?P<b>[^,:;()]+)",
        examples=("Trustworthy AI @ ACM Multimedia", "Proc. of X, co-located with ECIR"),
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
        r"colloquium|conférence|colloque|journées|atelier|rencontres|forum|summit)\b",
        examples=("Symposium on Foo",),
    ),
    DetectionDefault(
        id="journal",
        group="form",
        name=N_("Journal"),
        description=N_("A venue text naming a journal (after the conference rule)."),
        pattern=r"\b(journal|transactions|trans\.|revue|letters|review|magazine|annals|"
        r"bulletin|quarterly|j\.)\b",
        examples=("Annals of Foo",),
    ),
    # Tracks, in this order (Findings: see below).
    DetectionDefault(
        id="track_tutorial",
        group="track",
        name=N_("Tutorial track"),
        description=N_("A venue text naming a tutorial track."),
        pattern=r"\btutorials?\b",
        examples=("ECIR 2024 Tutorials",),
    ),
    DetectionDefault(
        id="track_demo",
        group="track",
        name=N_("Demo track"),
        description=N_("A venue text naming a demo track."),
        pattern=r"\b(?:demos?|demonstrations?)\b",
        examples=("ACL 2023 (System Demonstrations)",),
    ),
    DetectionDefault(
        id="track_short",
        group="track",
        name=N_("Short paper track"),
        description=N_("A venue text naming a short paper track."),
        pattern=r"\bshort papers?\b",
        examples=("ACL 2022 (Volume 2: Short Papers)",),
    ),
    DetectionDefault(
        id="track_findings",
        group="track",
        name=N_("Findings"),
        description=N_(
            "A venue text naming a Findings volume (ACL's, EMNLP's…): ranked apart from the "
            "main conference."
        ),
        pattern=r"\bfindings\b",
        examples=("Findings of the Association for Computational Linguistics: ACL 2023",),
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
        try:
            out[r.id] = re.compile(
                _expand(r.pattern, patterns, frozenset({r.id})), re.I if r.ignore_case else 0
            )
        except re.error:
            out[r.id] = None
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


class Rule:
    """A rule's regex as set, looked up at each use: ``WORKSHOP_RE.search(text)``."""

    def __init__(self, rule_id: str) -> None:
        self.id = rule_id

    def search(self, text: str, *args) -> re.Match[str] | None:
        return regex(self.id).search(text, *args)

    @property
    def pattern(self) -> str:
        return regex(self.id).pattern
