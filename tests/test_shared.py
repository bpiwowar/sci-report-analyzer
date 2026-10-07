"""Shared settings: the folder format, the three-way merge, applying a group's settings."""

import json

import pytest
from helpers import add_source, make_person, pub, set_venue
from sqlalchemy import select

from sci_report_analyzer import authorship, shared, venue_match, venues
from sci_report_analyzer.db.models import Venue, VenueKey
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking.normalize import NormRule
from sci_report_analyzer.ranking.service import load_settings, save_settings, service


@pytest.fixture
def identity():
    authorship.set_identity("jdoe")
    yield "jdoe"
    authorship.forget_identity()


def _decisions() -> tuple[int, int]:
    """A few local decisions: a venue with levels by period, a workshop of it, a custom
    cleaning rule and a threshold."""
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "P1", 2020, "Conference on Shared Things"),
            pub("b", "P2", 2020, "Workshop on Shared Things"),
        ],
    )
    conf = set_venue(
        "Conference on Shared Things",
        kind="intl_conference",
        short_name="CST",
        levels=[{"type": "conference", "rank": "B", "to": 2017, "note": "as CORE then"}],
    )
    ws = set_venue("Workshop on Shared Things", kind="intl_workshop")
    venues.save_hosts(ws, [{"venue_id": conf, "from": 2015, "to": None}])
    s = load_settings()
    s.min_score = 0.85
    s.norm_rules.append(
        NormRule(id="custom1-jdoe", name="Drop Proc.", pattern=r"\bproc\.", ignore_case=True)
    )
    save_settings(s)
    return conf, ws


def test_folder_roundtrip_is_canonical(identity):
    _decisions()
    items = shared.local_items()
    files = shared.files_from_items(items)
    assert shared.items_from_files(files) == items
    assert shared.check(files) == []
    assert set(files) >= {"settings.json", "norm_rules.json", "venues/cst.json"}
    cst = json.loads(files["venues/cst.json"])
    assert cst["levels"][0]["note"] == "as CORE then" and cst["levels"][0]["by"] == "jdoe"
    assert cst["decisions"]["kind"]["by"] == "jdoe"
    ws = next(json.loads(t) for p, t in files.items() if "workshop" in p)
    assert ws["hosts"] == [{"from": 2015, "venue": "cst"}]
    assert json.loads(files["settings.json"])["settings"] == {"min_score": 0.85}
    assert [r["id"] for r in json.loads(files["norm_rules.json"])["rules"]] == ["custom1-jdoe"]
    # (written again: the same bytes)
    assert shared.files_from_items(shared.local_items()) == files


def test_check_reports_problems():
    files = {
        "settings.json": shared.dumps({"format": shared.FORMAT, "version": 1}),
        "venues/x.json": json.dumps({"id": "x", "name": "X", "hosts": [{"venue": "nope"}]}),
    }
    problems = shared.check(files)
    assert any("nope" in p for p in problems)
    assert any("canonical" in p for p in problems)
    bad = {"venues/y.json": json.dumps({"id": "z", "name": "Y"})}
    assert shared.check(bad)
    overlap = {
        "venues/y.json": json.dumps(
            {"id": "y", "name": "Y", "levels": [{"rank": "A"}, {"rank": "B", "from": 2020}]}
        )
    }
    assert "overlap" in shared.check(overlap)[0]


def test_merge3():
    base = {"a": 1, "b": 1, "c": 1, "d": 1, "venue/x/name": "X"}
    local = {"a": 1, "b": 2, "c": 2, "d": 1, "e": 5, "venue/x/name": "X local"}
    remote = {"a": 3, "b": 1, "c": 3, "f": 6, "venue/x/name": "X group"}
    merged, conflicts = shared.merge3(base, local, remote)
    assert merged == {"a": 3, "b": 2, "c": 2, "e": 5, "f": 6, "venue/x/name": "X group"}
    assert [c.key for c in conflicts] == ["c"]
    assert shared.resolved(merged, conflicts, {"c"})["c"] == 3
    # (the same value set on both sides, by different people: no conflict)
    merged, conflicts = shared.merge3(
        {}, {"k": {"value": 1, "by": "me"}}, {"k": {"value": 1, "by": "them"}}
    )
    assert not conflicts and merged["k"]["by"] == "them"


def test_apply_the_group_settings(identity):
    conf, _ws = _decisions()
    old = shared.local_items()
    new = dict(old)
    # The group: CST is A from 2018, a new venue with a variant, a threshold back to its
    # default, the custom rule removed.
    new["venue/cst/levels"] = [
        *old["venue/cst/levels"],
        {"type": "conference", "rank": "A", "from": 2018, "by": "alice", "on": "2026-10-01"},
    ]
    new["venue/new-venue/name"] = "Brand New Venue"
    new["venue/new-venue/kind"] = {"value": "natl_conference", "by": "alice", "on": "2026-10-01"}
    new["venue/new-venue/variant/hal|Brand New Venue 2024"] = {"by": "alice"}
    del new["settings/min_score"]
    del new["norm_rule/custom1-jdoe"]
    counts = shared.apply(new, old)
    assert counts["venues"] == 2 and counts["settings"] == 1
    assert shared.changes(shared.local_items(), new) == []
    with session_scope() as s:
        v = s.get(Venue, conf)
        assert [lv["rank"] for lv in v.levels] == ["B", "A"]
        assert v.levels[1]["by"] == "alice"  # (kept: not the local user's)
        nv = s.scalar(select(Venue).where(Venue.share_id == "new-venue"))
        assert nv.kind == "natl_conference" and nv.kind_manual
        assert nv.decisions["kind"]["by"] == "alice"
        vk = s.get(VenueKey, service.key("Brand New Venue 2024", "hal"))
        assert vk.venue_id == nv.id and vk.manual and vk.decided_by == "alice"
    assert load_settings().min_score == 0.8
    assert "custom1-jdoe" not in {r.id for r in load_settings().norm_rules}
    # The group removes the venue's decisions: automatic again.
    newer = {k: v for k, v in new.items() if not k.startswith("venue/new-venue/")}
    shared.apply(newer, shared.local_items())
    with session_scope() as s:
        nv = s.scalar(select(Venue).where(Venue.share_id == "new-venue"))
        assert not nv.kind_manual
        vk = s.get(VenueKey, service.key("Brand New Venue 2024", "hal"))
        assert not vk.manual


def test_group_venues_linked_to_local_ones_by_name():
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "P1", 2020, "Symposium on Linkable Venues")])
    venue_match.refresh()
    with session_scope() as s:
        local = s.scalar(select(Venue).where(Venue.name.like("%Linkable%")))
        assert local.share_id is None
        name = local.name
    remote = {"venue/slv/name": name, "venue/slv/kind": {"value": "intl_conference"}}
    assert shared.link_venues(remote) == 1
    base, local_items = {}, shared.local_items()
    merged, conflicts = shared.merge3(base, local_items, remote)
    assert not conflicts
    shared.apply(merged, local_items)
    with session_scope() as s:
        v = s.scalar(select(Venue).where(Venue.share_id == "slv"))
        assert v.name == name and v.kind_manual


def test_chosen_changes_only():
    base = {"settings/min_score": 0.9}
    local = {
        "settings/min_score": 0.7,
        "venue/a/name": "A",
        "venue/a/kind": {"value": "intl_conference"},
        "venue/b/name": "B",
        "venue/b/hosts": {"value": [{"venue": "a"}]},
        "venue/c/name": "C",
        "venue/c/url": {"value": "https://c.org"},
    }
    out = shared.chosen(base, local, {"venue/b/hosts"})
    assert out == {
        "settings/min_score": 0.9,
        "venue/b/name": "B",
        "venue/b/hosts": {"value": [{"venue": "a"}]},
        "venue/a/name": "A",
    }
    assert shared.describe("venue/b/hosts", out) == "B · main conferences"


def test_check_command_fixes_the_formatting(tmp_path, capsys):
    from sci_report_analyzer import shared_cli

    (tmp_path / "venues").mkdir()
    (tmp_path / "settings.json").write_text(
        '{"format": "sci-report-analyzer-shared", "version": 1}'
    )
    (tmp_path / "venues/x.json").write_text('{"name": "X", "id": "x", "kind": "intl_conference"}')
    assert shared_cli.main(["check", str(tmp_path)]) == 1
    assert "canonical" in capsys.readouterr().err
    assert shared_cli.main(["check", str(tmp_path), "--fix"]) == 0
    assert (tmp_path / "venues/x.json").read_text() == shared.dumps(
        {"id": "x", "kind": "intl_conference", "name": "X"}
    )
