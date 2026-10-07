"""A venue's levels by year range, and who made its manual decisions."""

import asyncio

import pytest
from helpers import add_source, make_person, pub, set_venue
from sqlalchemy import select

from sci_report_analyzer import authorship, pubview, venues
from sci_report_analyzer.db.models import Venue, VenueKey
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking import levels
from sci_report_analyzer.ranking.service import load_settings, save_settings


def stats(pid):
    return {s.title: s for s in asyncio.run(pubview.load_stats(pid))}


@pytest.fixture
def identity():
    authorship.set_identity("jdoe")
    yield "jdoe"
    authorship.forget_identity()


def test_normalized_sorts_and_refuses_overlaps():
    out = levels.normalized(
        [{"rank": "A", "from": "2018"}, {"rank": "B", "to": 2017}, {"rank": ""}]
    )
    assert out == [
        {"type": "conference", "rank": "B", "to": 2017},
        {"type": "conference", "rank": "A", "from": 2018},
    ]
    assert levels.covers_all(out)
    with pytest.raises(ValueError):
        levels.normalized([{"rank": "B", "to": 2018}, {"rank": "A", "from": 2018}])
    with pytest.raises(ValueError):
        levels.normalized([{"rank": "B", "from": 2019, "to": 2018}])
    assert levels.label(out) == "B (–2017) · A (2018–)"


def test_a_paper_takes_the_level_of_its_year():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2016, "Workshop on Odd Things"),
            pub("b", "New paper", 2020, "Workshop on Odd Things"),
        ],
    )
    set_venue(
        "Workshop on Odd Things",
        kind="intl_conference",
        levels=[{"type": "conference", "rank": "B", "to": 2017}, {"rank": "A", "from": 2018}],
    )
    st = stats(pid)
    assert st["Old paper"].badge.coreRank == "B" and st["Old paper"].badge.manual
    assert st["New paper"].badge.coreRank == "A"


def test_years_no_range_covers_keep_the_ranking():
    """A* by hand until 2020; later papers take CORE's rank of their year."""
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2019, "Symposium on Timely Rankings"),
            pub("b", "New paper", 2024, "Symposium on Timely Rankings"),
        ],
    )
    set_venue("Symposium on Timely Rankings", levels=[{"rank": "A*", "to": 2020}])
    st = stats(pid)
    assert st["Old paper"].badge.coreRank == "A*" and st["Old paper"].badge.manual
    assert st["New paper"].badge.coreRank == "A" and not st["New paper"].badge.manual


def test_decisions_are_stamped_with_their_author(identity):
    vid = set_venue("Some Venue On Stamps", kind="intl_conference", url="https://x.org")
    venues.update_venue(vid, levels=[{"type": "conference", "rank": "B"}])
    with session_scope() as s:
        v = s.get(Venue, vid)
        assert v.decisions["kind"]["by"] == "jdoe" and v.decisions["url"]["on"]
        assert v.levels[0]["by"] == "jdoe"
        key = s.scalar(select(VenueKey).where(VenueKey.venue_id == vid, VenueKey.manual))
    authorship.set_identity("other")
    venues.update_venue(vid, url="https://x.org", kind="natl_conference")
    with session_scope() as s:
        v = s.get(Venue, vid)
        assert v.decisions["url"]["by"] == "jdoe"  # (saved unchanged)
        assert v.decisions["kind"]["by"] == "other"
        assert v.levels[0]["by"] == "jdoe"
    venues.update_venue(vid, kind=None)
    with session_scope() as s:
        assert "kind" not in s.get(Venue, vid).decisions  # (automatic again)
    assert key is None or key.decided_by in ("jdoe", None)


def test_kept_authors_while_importing(identity):
    with authorship.keeping():
        vid = set_venue("Venue From A Colleague", kind="intl_conference")
    with session_scope() as s:
        assert not (s.get(Venue, vid).decisions or {}).get("kind")


def test_rules_stamped_when_changed(identity):
    s = load_settings()
    s.norm_rules[0] = s.norm_rules[0].model_copy(update={"pattern": "foo"})
    save_settings(s)
    saved = load_settings()
    assert saved.norm_rules[0].by == "jdoe" and saved.norm_rules[1].by is None
    authorship.set_identity("other")
    save_settings(saved)  # (unchanged: its author kept)
    assert load_settings().norm_rules[0].by == "jdoe"
    reset = load_settings()
    reset.norm_rules = type(reset)().norm_rules  # (back to the defaults)
    save_settings(reset)
    assert load_settings().norm_rules[0].by is None


def test_former_names_by_year():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2016, "Neural Things Conference"),
            pub("b", "New paper", 2020, "Neural Things Conference"),
        ],
    )
    set_venue(
        "Neural Things Conference",
        name="Conference on Neural Things",
        short_name="NeuThings",
        names=[{"name": "Neural Things Processing", "short_name": "NTP", "to": 2017}],
    )
    st = stats(pid)
    assert st["Old paper"].venue == "Neural Things Processing"
    assert st["Old paper"].venue_short == "NTP"
    assert st["New paper"].venue == "Conference on Neural Things"
    assert st["New paper"].venue_short == "NeuThings"


def test_a_dated_venue_rule_wins_in_its_years():
    """A workshop that became a conference under the same name."""
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2013, "Theory of Things Meeting"),
            pub("b", "New paper", 2016, "Theory of Things Meeting"),
            pub("c", "Other", 2013, "Workshop on Elsewhere"),
        ],
    )
    conf = set_venue("Theory of Things Meeting", kind="intl_conference")
    ws = set_venue("Workshop on Elsewhere", kind="intl_workshop")
    rule = VenuePattern(pattern="^Theory of Things", to_year=2014)
    venues.update_venue(ws, patterns=[rule.model_dump()])
    from sci_report_analyzer import venue_match

    venue_match.refresh(reassign=True)
    st = stats(pid)
    assert st["Old paper"].venue_id == ws
    assert st["New paper"].venue_id == conf


def test_not_ranked_by_hand_is_checked():
    """ "Not ranked" by hand: the kind's category, but checked (unlike a venue no one did)."""
    from sci_report_analyzer.ranking.badge import CHECKED, TO_CHECK

    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2014, "Symposium on Unlisted Things"),
            pub("b", "New paper", 2020, "Symposium on Unlisted Things"),
            pub("c", "Other paper", 2020, "Colloquium of Nobody Looked"),
        ],
    )
    for raw in ("Symposium on Unlisted Things", "Colloquium of Nobody Looked"):
        set_venue(raw, kind="intl_conference")
    set_venue(
        "Symposium on Unlisted Things", levels=[{"rank": levels.NONE, "from": 2015, "note": "x"}]
    )
    st = stats(pid)
    new = st["New paper"]
    assert new.badge.unranked and new.badge.manual and not new.badge.coreRank
    assert new.category.base_key == "k_intl_conference"
    assert new.review == CHECKED
    assert st["Old paper"].review == TO_CHECK  # (no range covers 2014)
    assert st["Other paper"].review == TO_CHECK
    assert levels.label([{"rank": levels.NONE}]) == "not ranked"
    rows = {r.name: r for r in asyncio.run(venues.venue_rows())}
    assert rows["Symposium on Unlisted Things"].review == CHECKED
    assert rows["Colloquium of Nobody Looked"].review == TO_CHECK


def test_not_ranked_takes_the_kind_default_level_and_stays_checked():
    from sci_report_analyzer.ranking.badge import CHECKED, TO_CHECK

    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Checked paper", 2020, "Rencontres de truc"),
            pub("b", "Unchecked paper", 2020, "Journées de machin"),
        ],
    )
    for raw in ("Rencontres de truc", "Journées de machin"):
        set_venue(raw, kind="natl_conference")
    set_venue("Rencontres de truc", levels=[{"rank": levels.NONE}])
    s = load_settings()
    s.kind_levels = {"natl_conference": "C"}
    save_settings(s)
    st = stats(pid)
    assert st["Checked paper"].badge.coreRank == "C"
    assert st["Checked paper"].review == CHECKED
    assert st["Unchecked paper"].badge.coreRank == "C"
    assert st["Unchecked paper"].review == TO_CHECK


def test_a_workshop_not_ranked_takes_its_main_conference():
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Workshop paper", 2020, "Workshop on Small Bits")])
    host = set_venue("Conference on Big Things", kind="intl_conference", levels=[{"rank": "A"}])
    set_venue(
        "Workshop on Small Bits",
        kind="intl_workshop",
        levels=[{"rank": levels.NONE}],
        hosts=[{"venue_id": host}],
    )
    assert stats(pid)["Workshop paper"].badge.coreRank == "A"
