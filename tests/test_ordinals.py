"""Spelled ordinals: the words, and the cleaning rules removing them."""

import pytest

from sci_report_analyzer.ranking.normalize import LANGUAGE_RULES, apply_rules, normalize
from sci_report_analyzer.ranking.ordinals import english_ordinal, french_ordinal


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (1, ["premier", "première"]),
        (2, ["deuxième", "second", "seconde"]),
        (5, ["cinquième"]),
        (9, ["neuvième"]),
        (21, ["vingt et unième"]),
        (71, ["soixante et onzième"]),
        (80, ["quatre-vingtième"]),
        (81, ["quatre-vingt-unième"]),
        (99, ["quatre-vingt-dix-neuvième"]),
        (200, ["deux centième"]),
        (280, ["deux cent quatre-vingtième"]),
        (1000, ["millième"]),
    ],
)
def test_french(n, words) -> None:
    assert french_ordinal(n) == words


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (3, ["third"]),
        (12, ["twelfth"]),
        (20, ["twentieth"]),
        (35, ["thirty-fifth"]),
        (100, ["one hundredth", "hundredth"]),
        (101, ["one hundred first", "one hundred and first"]),
        (1000, ["one thousandth", "thousandth"]),
    ],
)
def test_english(n, words) -> None:
    assert english_ordinal(n) == words


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        (
            "Thirty-fifth Conference on Neural Information Processing Systems",
            "Zth Conference on Neural Information Processing Systems",
        ),
        (
            "The Fourth Arabic Natural Language Processing Workshop",
            "The Zth Arabic Natural Language Processing Workshop",
        ),
        ("Vingt-et-unième congrès", "Zème congrès"),
        ("Vingt et unièmes journées", "Zème journées"),
        ("DEUXIÈME atelier", "Zème atelier"),
        ("Premières rencontres", "Zème rencontres"),
        ("Quatre-vingtieme colloque", "Zème colloque"),
        ("45th Annual Meeting", "Zth Annual Meeting"),
        ("ACL 2022 - 60th Annual Meeting", "ACL 2022 - Zth Annual Meeting"),
        ("17e conférence", "Zème conférence"),
        (
            "1er atelier, 2ème colloque, 22èmes journées, 3ième",
            "Zème atelier, Zème colloque, Zème journées, Zème",
        ),
        # Not ordinals:
        ("North American Chapter", "North American Chapter"),
        ("Health Text Mining", "Health Text Mining"),
        ("Systèmes d'information", "Systèmes d'information"),
        ("Fourier analysis", "Fourier analysis"),
    ],
)
def test_rules(text, cleaned) -> None:
    assert apply_rules(text, LANGUAGE_RULES) == cleaned


def test_ordinal_marks_left_out_of_keys() -> None:
    """ "Zth" / "Zème" are for reading: "Fourteenth X" and "X" have the same key."""
    for text, plain in (
        ("Fourteenth ACM Conference on Widgets", "ACM Conference on Widgets"),
        ("Quatorzième conférence sur les gadgets", "conférence sur les gadgets"),
    ):
        assert normalize(apply_rules(text, LANGUAGE_RULES)) == normalize(plain)
