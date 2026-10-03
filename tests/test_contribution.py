"""The person's role in a paper, from their position: ordered rules, the first wins."""

from types import SimpleNamespace

import pytest

from sci_report_analyzer import contribution
from sci_report_analyzer.contribution import Config, Role, Rule, default_config


def _role(pos, n, marks=(), cfg=None) -> str | None:
    s = SimpleNamespace(author_pos=pos, num_authors=n, author_marks=list(marks))
    return contribution.role(s, cfg or default_config())


def test_default_rules() -> None:
    assert _role(None, 5) is None
    assert _role(1, 1) == "sole"
    assert _role(1, 5) == "first"
    assert _role(1, None) == "first"
    assert _role(5, 5) == "last"
    assert _role(2, 2) == "last"
    assert _role(2, 3) == "supervisor"  # (one of the last three)
    assert [_role(p, 8) for p in range(1, 9)] == [
        "first",
        "contributor",
        "contributor",
        "involved",  # (the catch-all)
        "involved",
        "supervisor",
        "supervisor",
        "last",
    ]
    # Large teams: the middle (25–75% of the list) is involved.
    assert [_role(p, 13) for p in (2, 3, 4, 10, 11, 12, 13)] == [
        "contributor",
        "contributor",
        "involved",
        "involved",
        "supervisor",
        "supervisor",
        "last",
    ]
    # Unknown number of authors: only what does not depend on it.
    assert _role(2, None) == "contributor"
    assert _role(5, None) == "involved"


def test_phd_student_marks_the_supervisor_side() -> None:
    assert _role(2, 6, ["student", "owner"]) == "supervisor"
    assert _role(1, 4, ["owner", "student"]) == "first"
    assert _role(7, 13, ["student"]) == "involved"  # (the large-team rule comes first)


def test_conditions() -> None:
    def ok(cond, p, n, phd=False) -> bool:
        return contribution.matches(cond, p, n, phd)

    assert ok("", 3, 5)
    assert ok("p>=-3", 3, 5) and not ok("p>=-3", 2, 5)
    assert ok("P = -1", 5, 5)
    assert ok("25% <= p", 2, 5) and not ok("p>25%", 2, 5)
    assert ok("n<3 or (phd and not p=1)", 2, 6, phd=True)
    assert not ok("NOT phd", 2, 6, phd=True)
    # An unknown number of authors: unknown, hence no match (even negated).
    assert not ok("n>=12", 2, None) and not ok("not n>=12", 2, None)
    assert ok("p=2 or n>=12", 2, None)
    for bad, error in (
        ("p=", "Incomplete"),
        ("(p=1", "Incomplete"),
        ("x=1", "Unexpected"),
        ("p=1 p=2", "Unexpected"),
        ("n=5%", "percentage"),
    ):
        assert error in contribution.check_condition(bad), bad


def test_custom_roles_and_catch_all() -> None:
    assert contribution.load_config() == default_config()
    cfg = Config(
        roles=[Role("lead", "Lead", "#000000"), Role("rest", "Rest", "#ffffff")],
        rules=[Rule("lead", "p=1 or p=-1")],
        fallback="rest",
    )
    assert _role(1, 1, cfg=cfg) == "lead"
    assert _role(3, 5, cfg=cfg) == "rest"
    assert (
        _role(0, 5, cfg=Config(cfg.roles, [Rule("lead", "p<=2")], "rest")) == "rest"
    )  # (any order)
    contribution.save_config(cfg)
    assert contribution.load_config() == cfg
    for broken, error in (
        (Config(cfg.roles, [Rule("lead", "p=")], "rest"), "Rule 1: Incomplete"),
        (Config(cfg.roles, [Rule("other", "p=1")], "rest"), "unknown role"),
        (Config(cfg.roles, [], "other"), "catch-all"),
        (Config([Role("a", "A", "#000"), Role("b", "A", "#000")], [], "a"), "same name"),
    ):
        with pytest.raises(ValueError, match=error):
            contribution.save_config(broken)


def test_selection() -> None:
    from sci_report_analyzer.pubview import Sel, match_sel

    s = SimpleNamespace(contribution="last", year=2024)
    assert match_sel(s, Sel("contribution", "last author", key="last"))
    assert match_sel(s, Sel("contribution", "last author (2020–2024)", 2020, 2024, key="last"))
    assert not match_sel(s, Sel("contribution", "last author (2010–2014)", 2010, 2014, key="last"))
    assert not match_sel(s, Sel("contribution", "first author", key="first"))
