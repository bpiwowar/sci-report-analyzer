"""The matcher against reference fixtures (records and expected matches)."""

import pytest
from helpers import load_fixture

from sci_report_analyzer.ranking.matcher import Matcher
from sci_report_analyzer.ranking.normalize import (
    DEFAULT_NORM_RULES,
    LANGUAGE_RULES,
    PREFIX_RULES,
    apply_rules,
    normalize,
    tokenize,
    without_ordinal_marks,
)


def clean_venue(segment: str | None) -> str:
    """A venue text cleaned with the default rules."""
    return apply_rules(segment, DEFAULT_NORM_RULES)


GOLDEN = load_fixture("golden_cases.json.gz")
# Cleaned differently on purpose: the reference leaves "èmes" of a plural French ordinal.
DIVERGES = {
    "6èmes Journées internationales d'Analyse statistique des Données Textuelles (JADT 2002)"
}


@pytest.fixture(scope="module")
def matcher():
    return Matcher().load(load_fixture("golden_records.json.gz"))


def _pick(r):
    if r is None:
        return None
    return {
        "name": r.record["name"],
        "source": r.record["source"],
        "type": r.record["type"],
        "score": pytest.approx(r.score),
        "exact": r.exact,
    }


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["venue"][:40])
def test_cleaning(case):
    if case["venue"] in DIVERGES:
        assert clean_venue(case["venue"]).startswith("Zème Journées")
        return
    # The reference keeps spelled ordinals ("Thirty-sixth", "Première") and drops the others:
    # the language rules replace them all ("Zth"), a mark left out here.
    # Parenthesised text is kept (but acronyms): the reference's "cleanNoParens". A
    # proceedings' "Proceedings of the" and a leading "The" are removed (not by the reference).
    assert without_ordinal_marks(clean_venue(case["venue"])) == without_ordinal_marks(
        apply_rules(case["cleanNoParens"], (*PREFIX_RULES, *LANGUAGE_RULES))
    )
    assert normalize(case["venue"]) == case["norm"]
    assert tokenize(case["venue"]) == case["tokens"]


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["venue"][:40])
def test_match(matcher, case):
    if case["venue"] in DIVERGES:
        pytest.skip("cleaned differently from the reference")
    c = case["clean"]
    assert _pick(matcher.match(c)) == case["match"]
    assert _pick(matcher.match(c, None, "conference")) == case["matchConf"]
    assert _pick(matcher.match(c, None, "journal")) == case["matchJour"]
    assert [_pick(r) for r in matcher.candidates(c)] == case["candidates"]


def test_issn(matcher):
    for case in GOLDEN["issnCases"]:
        assert _pick(matcher.match("zzzz", case["issn"])) == case["match"], case["issn"]


def test_rule_examples():
    expected = {r["id"]: r["after"] for r in GOLDEN["rules"]}
    before = {r["id"]: r["before"] for r in GOLDEN["rules"]}
    rules = {r.id: r for r in DEFAULT_NORM_RULES}
    for rid, after in expected.items():
        if rid == "ordinals":  # now in the English and French rules
            continue
        if rid == "parentheses":  # dropped: it removed tracks ("(Demonstrations)")
            continue
        assert rules[rid].apply(before[rid]) == after
        assert rules[rid].example == before[rid]


def test_parenthesised_track_kept():
    """A track in parentheses stays in the cleaned text (the venue's name, its variant), but
    not in the text searched in the rankings."""
    from sci_report_analyzer.ranking.service import for_rankings

    text = "COLING (Demonstrations): International Conference on Computational Linguistics (COLING)"
    cleaned = clean_venue(text)
    assert (
        cleaned == "COLING (Demonstrations) International Conference on Computational Linguistics"
    )
    assert for_rankings(cleaned) == "COLING International Conference on Computational Linguistics"
