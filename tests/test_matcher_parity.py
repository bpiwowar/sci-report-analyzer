"""The matcher against reference fixtures (records and expected matches)."""

import gzip
import json
from pathlib import Path

import pytest

from sci_report_analyzer.ranking.matcher import Matcher
from sci_report_analyzer.ranking.normalize import (
    DEFAULT_NORM_RULES,
    LANGUAGE_RULES,
    apply_rules,
    clean_venue,
    normalize,
    tokenize,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    with gzip.open(FIXTURES / name, "rt") as f:
        return json.load(f)


GOLDEN = _load("golden_cases.json.gz")
# Cleaned differently on purpose: the reference leaves "èmes" of a plural French ordinal.
DIVERGES = {
    "6èmes Journées internationales d'Analyse statistique des Données Textuelles (JADT 2002)"
}


@pytest.fixture(scope="module")
def matcher():
    return Matcher().load(_load("golden_records.json.gz"))


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
        assert clean_venue(case["venue"]).startswith("Journées")
        return
    # The reference keeps spelled ordinals ("Thirty-sixth", "Première"): the language rules
    # drop them.
    assert clean_venue(case["venue"]) == apply_rules(case["clean"], LANGUAGE_RULES)
    assert clean_venue(case["venue"], strip_parens=False) == apply_rules(
        case["cleanNoParens"], LANGUAGE_RULES
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
        assert rules[rid].apply(before[rid]) == after
        assert rules[rid].example == before[rid]
