import asyncio

from helpers import add_source, make_person, pub
from sqlalchemy import select

from sci_report_analyzer import pubview, sync, venues
from sci_report_analyzer.db.models import Publication, Venue, VenueKey
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking import tracks
from sci_report_analyzer.ranking.kinds import KindEvidence, detect_kind
from sci_report_analyzer.ranking.service import (
    VenuePattern,
    load_settings,
    save_settings,
    service,
)
from sci_report_analyzer.sources.base import FetchResult


def stats(pid):
    return {s.title: s for s in asyncio.run(pubview.load_stats(pid))}


def test_publications_are_linked_to_venues():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [
            pub("a", "Paper A", 2020, "Conférence en Recherche d'Information 2020"),
            pub("b", "Paper B", 2021, "Conférence en Recherche d'Information 2021"),
        ],
    )
    st = stats(pid)
    assert st["Paper A"].venue_id == st["Paper B"].venue_id  # same processed text
    assert st["Paper A"].kind == "natl_conference"


def test_manual_link_survives_resync():
    pid = make_person()
    link = add_source(
        pid,
        "hal",
        "idhal:x",
        [pub("a", "Paper A", 2020, "Venue One"), pub("b", "Paper B", 2020, "Venue Two")],
    )
    st = stats(pid)
    venues.link_publication(st["Paper A"].id, st["Paper B"].venue_id)
    sync.finish_link(
        link,
        FetchResult(
            publications=[
                pub("a", "Paper A", 2020, "Venue One"),
                pub("b", "Paper B", 2020, "Venue Two"),
            ]
        ),
    )
    st = stats(pid)
    assert st["Paper A"].venue_id == st["Paper B"].venue_id and st["Paper A"].venue_manual


def test_kind_default_level_unless_overridden():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [
            pub("a", "Paper A", 2020, "Journées francophones de truc", doc_type="COMM"),
            pub("b", "Paper B", 2020, "Colloque de machin", doc_type="COMM"),
        ],
    )
    s = load_settings()
    s.kind_levels = {"natl_conference": "C"}
    save_settings(s)
    st = stats(pid)
    assert st["Paper A"].badge.coreRank == "C"
    assert st["Paper A"].badge.extra["kind_default"] == "natl_conference"
    venues.set_level("Colloque de machin", "conference", "B")  # venue override wins
    assert stats(pid)["Paper B"].badge.coreRank == "B"


def test_merge_venues_moves_variants_and_links():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [pub("a", "Paper A", 2020, "Some Venue"), pub("b", "Paper B", 2020, "Some Venue Alt")],
    )
    st = stats(pid)
    a, b = st["Paper A"].venue_id, st["Paper B"].venue_id
    venues.set_kind("Some Venue Alt", "natl_journal")
    venues.merge_venues(a, [b])
    with session_scope() as s:
        assert {k.key for k in s.get(Venue, a).keys} == {"some venue", "some venue alt"}
        assert s.get(Venue, a).kind == "natl_journal"  # manual decision carried over
        assert s.get(VenueKey, "some venue alt").manual
    assert stats(pid)["Paper B"].venue_id == a


def test_detect_kind():
    kw = {
        "national_keywords": load_settings().national_keywords,
        "international_keywords": load_settings().international_keywords,
    }
    assert detect_kind(None, KindEvidence("CoRR", archival=True), **kw) == "preprint"
    assert detect_kind(None, KindEvidence("Revue d'Intelligence Artificielle"), **kw) == (
        "natl_journal"
    )
    assert detect_kind(None, KindEvidence("International Workshop on Foo"), **kw) == (
        "intl_workshop"
    )
    assert detect_kind(None, KindEvidence("Atelier Foo, colloque Bar"), **kw) == "natl_workshop"
    for doc_type in ("Editorship", "DOUV", "proceedings"):  # DBLP, HAL, Crossref
        ev = KindEvidence("Symposium on Timely Rankings", doc_type=doc_type)
        assert detect_kind(None, ev, **kw) == "proceedings"
    assert detect_kind(None, KindEvidence("Some Thing", doc_type="Book"), **kw) == "book"
    assert detect_kind(None, KindEvidence(None, doc_type="THESE"), **kw) == "thesis"
    # A habilitation (HAL's "HDR") is a thesis, even when another source says it is a book.
    for doc_type in ("HDR", "Book HDR", "Book THESE", "Book dissertation-thesis"):
        assert detect_kind(None, KindEvidence(None, doc_type=doc_type), **kw) == "thesis"
    # Software and datasets, even in an archive (Zenodo); not when a record is a paper.
    for doc_type, kind in (
        ("software", "software"),
        ("SOFTWARE", "software"),
        ("dataset", "dataset"),
        ("Data", "dataset"),
        ("data-set", "dataset"),
    ):
        ev = KindEvidence("Zenodo", doc_type=doc_type, archival=True)
        assert detect_kind(None, ev, **kw) == kind
    ev = KindEvidence("Some Conference", doc_type="dataset Inproceedings")
    assert detect_kind(None, ev, **kw) == "intl_conference"


def test_no_shared_task_detected():
    # A shared task (or evaluation campaign) is set by hand: its venue is detected as any.
    kw = {
        "national_keywords": load_settings().national_keywords,
        "international_keywords": load_settings().international_keywords,
    }
    for venue, kind in (
        ("International Workshop on Semantic Evaluation (SemEval-2017)", "intl_workshop"),
        ("Working Notes of the Widget Evaluation Forum", "intl_conference"),
    ):
        assert detect_kind(None, KindEvidence(venue), **kw) == kind, venue


def test_venue_rows():
    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Journées de Foo")])
    stats(pid)
    rows = asyncio.run(venues.venue_rows())
    (row,) = rows
    assert row.kind == "natl_conference" and row.publications == 1 and len(row.variants) == 1
    with session_scope() as s:
        assert s.scalar(select(Publication.venue_id)) == row.id


def test_rename_keeps_the_old_name_as_variant():
    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Venue One")])
    vid = stats(pid)["Paper A"].venue_id
    with session_scope() as s:
        s.get(Venue, vid).name = "Some Old Name"  # e.g. derived from an older venue text
    venues.update_venue(vid, name="Venue number one")
    with session_scope() as s:
        vk = s.get(VenueKey, venues.service.key("Some Old Name"))
        assert vk is not None and vk.venue_id == vid and vk.manual
        assert s.get(Venue, vid).name == "Venue number one"


def test_rule_sets_the_track():
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x",
        [pub("a", "Paper A", 2020, "ACL Findings 2020"), pub("b", "Paper B", 2020, "ACL 2021")],
    )
    st = stats(pid)
    acl = st["Paper B"].venue_id
    rule = VenuePattern(pattern=r"^ACL Findings", track="findings", sources=["dblp"])
    assert [e.raw for e in venues.pattern_effects(acl, rule)] == ["ACL Findings 2020"]
    venues.save_patterns(acl, [rule])
    st = stats(pid)
    assert st["Paper A"].venue_id == acl and st["Paper A"].members[0].via == "pattern"
    assert st["Paper A"].track == "findings" and st["Paper B"].track is None
    (use,) = venues.pattern_uses(acl)
    assert [e[1] for e in use.examples] == ["ACL Findings 2020"]


def test_manual_variant_beats_rules_and_conflicts_are_listed():
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [
            pub("a", "Paper A", 2020, "Workshop on Things 2020"),
            pub("b", "Paper B", 2020, "Venue One"),
            pub("c", "Paper C", 2020, "Venue Two"),
        ],
    )
    st = stats(pid)
    one, two = st["Paper B"].venue_id, st["Paper C"].venue_id
    venues.save_patterns(one, [VenuePattern(pattern="Things")])
    venues.save_patterns(two, [VenuePattern(pattern="Workshop")])
    a = stats(pid)["Paper A"]
    assert a.venue_id == one and a.members[0].conflicts == (two,)
    venues.add_variant(two, "Workshop on Things 2020")
    a = stats(pid)["Paper A"]
    assert a.venue_id == two and a.members[0].via == "variant"


def test_rules_change_rekeys_variants():
    from sci_report_analyzer.ranking.normalize import NormRule

    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [pub("a", "Paper A", 2020, "Venue One"), pub("b", "Paper B", 2020, "Venue One Bis")],
    )
    st = stats(pid)
    assert st["Paper A"].venue_id != st["Paper B"].venue_id
    venues.add_variant(st["Paper A"].venue_id, "Special Name")
    s = load_settings()
    s.norm_rules.append(NormRule(id="bis", name="bis", pattern=r"\s+Bis$", replacement=""))
    save_settings(s)
    st = stats(pid)
    assert st["Paper A"].venue_id == st["Paper B"].venue_id
    with session_scope() as ss:
        vk = ss.get(VenueKey, "special name")
        assert vk.manual and vk.venue_id == st["Paper A"].venue_id


def test_issn_identifies_the_venue():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [
            pub("a", "Paper A", 2020, "Some Journal", issn="1234-5678"),
            pub("b", "Paper B", 2020, "Other Journal"),
        ],
    )
    target = stats(pid)["Paper B"].venue_id
    venues.save_issns(target, ["12345678"])
    a = stats(pid)["Paper A"]
    assert a.venue_id == target and a.members[0].via == "identifier"


def test_validated_source_settles_different_venues():
    from sci_report_analyzer import annotations

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Venue One", doi="10.1/x")])
    add_source(pid, "dblp", "x", [pub("b", "Paper A", 2020, "Venue Two", doi="10.1/x")])
    (a,) = stats(pid).values()
    assert a.disagree and any("different venues" in p for p in a.problems)
    annotations.set_venue_source(a.id, "hal")
    (a,) = stats(pid).values()
    assert a.venue == "Venue One" and not any("different venues" in p for p in a.problems)
    annotations.set_venue_source(a.id, "dblp")
    assert next(iter(stats(pid).values())).venue == "Venue Two"


def test_clear_manual_decisions_by_level():
    from sci_report_analyzer import annotations, venue_match

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Venue One")])
    a = stats(pid)["Paper A"]
    venues.update_venue(a.venue_id, level_type="conference", level_rank="A")
    venues.add_variant(a.venue_id, "Other Text")
    annotations.set_overrides(a.id, year_override=2019)
    assert venue_match.manual_counts() == {"venues": 1, "variants": 1, "papers": 1}
    venue_match.clear_manual(venues=False, papers=True)
    assert venue_match.manual_counts() == {"venues": 1, "variants": 1, "papers": 0}
    assert stats(pid)["Paper A"].year == 2020
    venue_match.clear_manual(venues=True, papers=False)
    assert venue_match.manual_counts() == {"venues": 0, "variants": 0, "papers": 0}
    a = stats(pid)["Paper A"]
    assert a.venue_id is not None and not (a.badge and a.badge.manual)


def _set_every_manual_field(pid):
    from sci_report_analyzer import annotations

    add_source(
        pid,
        "hal",
        "idhal:x",
        [pub("a", "Paper A", 2020, "Venue One"), pub("b", "Paper B", 2020, "Venue Two")],
    )
    a, b = stats(pid)["Paper A"], stats(pid)["Paper B"]
    venues.update_venue(
        a.venue_id,
        kind="intl_workshop",
        level_type="conference",
        level_rank="A",
        record_key="core:x",
        match_text="Venue",
        short_name="VO",
        url="https://venue.example.org",
        patterns=[{"pattern": "venue one"}],
    )
    venues.save_issns(a.venue_id, ["12345678"])
    venues.save_hosts(a.venue_id, [{"venue_id": b.venue_id}])
    venues.update_venue(a.venue_id, short_name="")  # "no acronym", by hand
    annotations.set_overrides(a.id, year_override=2019, note="n")
    annotations.set_rank_override(a.id, {"record_key": "core:x"}, "why")
    annotations.set_kind_override(a.id, "natl_journal")
    annotations.set_doi(a.id, "10.1234/abc")
    return a


def test_clear_manual_clears_every_field():
    from sci_report_analyzer import venue_match

    a = _set_every_manual_field(make_person())
    venue_match.clear_manual(venues=True, papers=True)
    assert venue_match.manual_counts() == {"venues": 0, "variants": 0, "papers": 0}
    with session_scope() as s:
        v, p = s.get(Venue, a.venue_id), s.get(Publication, a.id)
        assert v is None or not (v.has_manual or v.short_manual or v.hosts or v.url)
        assert not p.has_overrides and p.doi_manual is None and p.rank_note is None


def test_clear_manual_of_one_venue():
    a = _set_every_manual_field(make_person())
    venues.clear_manual(a.venue_id)
    with session_scope() as s:
        v = s.get(Venue, a.venue_id)
        assert not v.has_manual and not v.short_manual
        assert all(getattr(v, f) in (None, False) for f in v.MANUAL_FIELDS if f != "kind")


def test_merge_conflicting_venues_keeps_rules_and_tracks():
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(
        pid,
        "hal",
        "idhal:x",
        [
            pub("a", "Paper A", 2020, "ACL Findings 2021"),
            pub("b", "Paper B", 2020, "ACL"),
            pub("c", "Paper C", 2020, "ACL Workshop"),
        ],
    )
    st = stats(pid)
    acl, ws = st["Paper B"].venue_id, st["Paper C"].venue_id
    venues.save_patterns(acl, [VenuePattern(pattern="^ACL Findings", track="findings")])
    venues.save_patterns(ws, [VenuePattern(pattern="^ACL")])
    ((_, _, ids),) = [c for c in venues.conflicts() if c[1] == "ACL Findings 2021"]
    assert set(ids) == {acl, ws}
    venues.merge_venues(acl, [v for v in ids if v != acl])
    assert venues.conflicts() == []
    st = stats(pid)
    assert st["Paper A"].venue_id == st["Paper C"].venue_id == acl
    assert st["Paper A"].track == "findings"
    assert len(venues.venue_patterns(acl)) == 2


def test_explain_shows_rules_and_match():
    from sci_report_analyzer import venue_match
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Venue One, 2021")])
    a = stats(pid)["Paper A"]
    e = venue_match.explain("hal", "Venue One, 2021")
    assert [n for n, _ in e.steps] == ["Years", "Separators"]
    assert (e.key, e.via, e.venue_name) == ("venue one", "auto", "Venue One")
    venues.save_patterns(a.venue_id, [VenuePattern(pattern="^Venue One,")])
    e = venue_match.explain("hal", "Venue One, 2021")
    assert (e.via, e.detail) == ("pattern", "^Venue One,")  # a rule beats an automatic variant


def test_a_track_wins_and_the_validated_source_decides():
    from sci_report_analyzer import annotations, venues
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "ACL", doi="10.1/x")])
    add_source(pid, "dblp", "x", [pub("b", "Paper A", 2020, "ACL Findings", doi="10.1/x")])
    (a,) = stats(pid).values()
    acl = next(m.venue_id for m in a.members if m.source == "hal")
    venues.save_patterns(acl, [VenuePattern(pattern="^ACL Findings$", track="findings")])
    (a,) = stats(pid).values()
    assert len({m.venue_id for m in a.members}) == 1  # one venue: the Findings win
    assert a.track == "findings" and not a.disagree
    annotations.set_venue_source(a.id, "dblp")
    (a,) = stats(pid).values()
    assert a.track == "findings" and not any("different venues" in p for p in a.problems)
    annotations.set_venue_source(a.id, "hal")  # (the main track, validated by hand)
    assert next(iter(stats(pid).values())).track is None


def test_data_dir_copy_and_location(tmp_path) -> None:
    import sqlite3

    import pytest

    from sci_report_analyzer import config, datadir
    from sci_report_analyzer.venues import ensure_venue

    with session_scope() as s:
        ensure_venue(s, "Conference on Copies")
    dest = tmp_path / "elsewhere"
    datadir.use_data_dir(dest, copy=True)
    assert config.saved_data_dir() == dest.resolve()
    con = sqlite3.connect(dest / "sci-report-analyzer.sqlite")
    assert con.execute("select name from venue").fetchall() == [("Conference on Copies",)]
    con.close()
    with pytest.raises(FileExistsError):  # never overwrites a database
        datadir.use_data_dir(dest, copy=True)
    config.save_data_dir(None)
    assert config.saved_data_dir() is None


def _stats(pid):
    return {s.title: s for s in asyncio.run(pubview.load_stats(pid))}


def _venue_of(title):
    with session_scope() as s:
        return s.scalar(select(Publication.venue_id).where(Publication.title == title))


def test_workshop_ranked_as_its_main_conference():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Main paper", 2019, "Symposium on Timely Rankings (STR)"),
            pub("b", "Workshop paper", 2019, "Timely Workshop @ STR 2019"),
        ],
    )
    st = _stats(pid)
    ws = st["Workshop paper"]
    assert ws.kind == "intl_workshop" and ws.badge is None  # no main conference yet
    assert ws.category.key == "k_intl_workshop"
    host = _venue_of("Main paper")
    wid = _venue_of("Workshop paper")
    assert venues.host_suggestion(wid) == (host, "Symposium on Timely Rankings")

    venues.save_hosts(wid, [{"venue_id": host, "from": 2015, "to": None}])
    ws = _stats(pid)["Workshop paper"]
    # The main conference's rank in CORE2018 (in force in 2019), in its own category.
    assert (ws.badge.coreRank, ws.badge.coreEdition) == ("B", "CORE2018")
    assert ws.category.key == "workshop:b" and ws.category.label == "Workshop CORE B"
    assert ws.category.workshop and ws.host_id == host

    venues.save_hosts(wid, [{"venue_id": host, "from": 2020, "to": None}])
    ws = _stats(pid)["Workshop paper"]
    assert ws.badge is None and ws.host_id is None  # not its main conference in 2019


def test_workshop_takes_host_edition_like_its_papers():
    # A workshop of 2019 has its main conference's rank of 2019 (CORE2018: B, A now), as the
    # conference's own papers of that year; the latest one when the settings say so.
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Main paper", 2019, "Symposium on Timely Rankings (STR)"),
            pub("b", "Workshop paper", 2019, "Timely Workshop @ STR 2019"),
        ],
    )
    _stats(pid)
    venues.save_hosts(_venue_of("Workshop paper"), [{"venue_id": _venue_of("Main paper")}])
    st = _stats(pid)
    main, ws = st["Main paper"].badge, st["Workshop paper"].badge
    assert (ws.coreRank, ws.coreEdition) == (main.coreRank, main.coreEdition) == ("B", "CORE2018")
    assert ws.extra["coreLatest"] == ["ICORE2026", "A"]
    settings = load_settings()
    settings.core_edition = "latest"
    save_settings(settings)
    st = _stats(pid)
    assert st["Workshop paper"].badge.coreRank == st["Main paper"].badge.coreRank == "A"
    assert st["Workshop paper"].category.key == "workshop:a"


def test_split_workshop_variant_and_merge_host():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [pub("a", "Main paper", 2024, "Symposium on Timely Rankings (STR)")],
    )
    _stats(pid)
    host = _venue_of("Main paper")
    venues.add_variant(host, "Workshop on Foo co-located with STR")
    ((key, _),) = venues.workshop_variants(host)
    wid = venues.split_as_workshop(key)
    with session_scope() as s:
        w = s.get(Venue, wid)
        assert w.kind == "intl_workshop" and w.hosts[0]["venue_id"] == host
        assert s.get(VenueKey, key).venue_id == wid
        other = Venue(name="STR duplicate")
        s.add(other)
        s.flush()
        other_id = other.id
        w.hosts = [{"venue_id": other_id, "from": None, "to": None}]
    venues.merge_venues(host, [other_id])
    with session_scope() as s:
        assert s.get(Venue, wid).hosts == [{"venue_id": host, "from": None, "to": None}]
    assert venues.workshop_variants(host) == []


def test_findings_wins_over_the_main_track():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [pub("a", "A paper", 2025, "Findings of the Symposium on Timely Rankings (STR)")],
    )
    add_source(pid, "hal", "h/1", [pub("h", "A paper", 2025, "Symposium on Timely Rankings")])
    s = _stats(pid)["A paper"]
    assert s.track == "findings" and not s.disagree
    assert not any("different venues" in p for p in s.problems)


def test_disabled_source_is_left_out():
    from sci_report_analyzer import source_settings, sync

    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Only on DBLP", 2020, "Neural Computation")])
    add_source(pid, "hal", "h/1", [pub("b", "Only on HAL", 2021, "Neural Computation")])
    assert {s.title for s in _stats(pid).values()} == {"Only on DBLP", "Only on HAL"}
    source_settings.set_disabled({"hal"})
    assert set(_stats(pid)) == {"Only on DBLP"}
    assert "hal" not in sync.default_search_sources()
    source_settings.set_disabled(set())
    assert set(_stats(pid)) == {"Only on DBLP", "Only on HAL"}


def test_search_a_venue_and_merge_the_sources_venues_or_not():
    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Venue One", doi="10.1/x")])
    add_source(pid, "dblp", "x", [pub("b", "Paper A", 2020, "Venue Two", doi="10.1/x")])
    add_source(pid, "openalex", "y", [pub("c", "Paper C", 2021, "Venue Two")])
    stats(pid)
    names = {n: i for i, n in venues.venue_names().items()}
    one, two = names["Venue One"], names["Venue Two"]
    # Known venues and ranking records are both found.
    assert [h.venue_id for h in venues.find_venues("venue")] == [one, two]
    hit = next(h for h in venues.find_venues("Timely Rankings") if h.record_key)
    assert hit.venue_id is None and hit.badge.coreRank == "A"
    assert (hit.short, hit.kind) == ("STR", "Conference")

    # Without merging: only the paper is linked (by hand) to the new venue.
    target = venues.venue_for_record(hit.record_key)
    assert venues.venue_for_record(hit.record_key) == target  # created once
    a = stats(pid)["Paper A"]
    venues.use_venue(a.id, target, [], [one, two])
    s = stats(pid)
    assert s["Paper A"].venue_id == target and s["Paper A"].venue_manual
    assert s["Paper A"].badge.coreRank == "B"  # in 2020: its CORE2018 rank
    assert s["Paper C"].venue_id == two
    assert venues.find_venues("Timely Rankings")[0].venue_id == target  # pinned: the venue

    # Merging both sources' venues: the link is automatic, the other papers follow.
    venues.use_venue(a.id, target, [one, two], [one, two])
    s = stats(pid)
    assert s["Paper A"].venue_id == target and not s["Paper A"].venue_manual
    assert s["Paper C"].venue_id == target and not s["Paper A"].disagree
    assert set(venues.venue_names()) >= {target} and one not in venues.venue_names()


def test_edited_proceedings_are_ranked_apart_from_papers():
    from sci_report_analyzer import annotations

    pid = make_person()
    venue = "Symposium on Timely Rankings"
    add_source(
        pid,
        "dblp",
        "x",
        [
            pub("a", "Proceedings of STR 2024", 2024, venue, doc_type="Editorship"),
            pub("b", "A paper at STR", 2024, venue, doc_type="Inproceedings"),
        ],
    )
    s = stats(pid)
    vol, paper = s["Proceedings of STR 2024"], s["A paper at STR"]
    # Chairing an A conference: its rank, in a category of its own (not an A paper).
    assert (vol.kind, vol.kind_source, vol.badge.coreRank) == ("proceedings", "detected", "A")
    assert vol.category.key == "edited:a" and vol.category.label == "Proc. (ed.) CORE A"
    assert "no venue" not in vol.problems
    assert paper.badge.coreRank == "A" and paper.kind == "intl_conference"
    assert paper.category.key == "a"
    # The venue is a conference: its edited volume says nothing of its kind.
    (row,) = [r for r in asyncio.run(venues.venue_rows()) if r.id == paper.venue_id]
    assert row.kind == "intl_conference"
    # Set by hand, either way.
    annotations.set_kind_override(paper.id, "proceedings")
    annotations.set_kind_override(vol.id, "intl_conference")
    s = stats(pid)
    assert s["A paper at STR"].category.key == "edited:a"
    assert s["A paper at STR"].kind_source == "forced"
    assert s["Proceedings of STR 2024"].category.key == "a"


def test_edited_volume_without_venue_is_named_by_its_title():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x",
        [pub("a", "Symposium on Timely Rankings", 2024, None, doc_type="Editorship")],
    )
    vol = stats(pid)["Symposium on Timely Rankings"]
    assert vol.kind == "proceedings" and vol.badge.coreRank == "A"


def test_zenodo_software_and_datasets():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x",
        [
            pub("a", "A corpus", 2025, "Zenodo", doc_type="Data"),
            pub("b", "A tool", 2025, "Zenodo", doc_type="Data"),
        ],
    )
    # The DOI record (an archive) tells software from data.
    add_source(
        pid,
        "doi",
        "d",
        [pub("t", "A tool", 2025, "Zenodo", doc_type="software", archival=True)],
    )
    s = stats(pid)
    corpus, tool = s["A corpus"], s["A tool"]
    assert (corpus.kind, corpus.badge, corpus.category.key) == ("dataset", None, "k_dataset")
    assert (tool.kind, tool.category.key) == ("software", "k_software")
    assert "no venue" not in tool.problems


def test_summary_by_category_with_venues_and_years():
    from sci_report_analyzer.pubview import summary_lines

    pid = make_person()
    venue = "Symposium on Timely Rankings"
    add_source(
        pid,
        "dblp",
        "x",
        [
            pub("a", "Paper A", 2024, venue),
            pub("b", "Paper B", 2024, venue),
            pub("c", "Paper C", 2023, venue),
            pub("d", "Paper D", 2019, venue),  # CORE2018: B
            pub("e", "Paper E", 2024, None),
        ],
    )
    rows = list(stats(pid).values())
    lines = summary_lines(rows)
    assert lines[0] == f"3 CORE A (2x {venue} 2024, {venue} 2023)"
    assert lines[1] == f"1 CORE B ({venue} 2019)"
    assert lines[-1].endswith("(no venue 2024)")
    assert summary_lines(rows, short=True)[0] == "3 CORE A (2x STR 2024, STR 2023)"
    by_kind = summary_lines(rows, short=True, by_kind=True)
    assert by_kind[0] == "4 Intl. conf.: 3 CORE A (2x STR 2024, STR 2023); 1 CORE B (STR 2019)"
    assert by_kind[-1].endswith(" (no venue 2024)") and "unranked" not in by_kind[-1]
    no_years = summary_lines(rows, short=True, by_kind=True, years=False)
    assert no_years[0] == "4 Intl. conf.: 3 CORE A (3x STR); 1 CORE B (STR)"
    # A category without details is still counted in its kind.
    hidden = summary_lines(rows, short=True, by_kind=True, years=False, hidden={"b"})
    assert hidden[0] == "4 Intl. conf.: 3 CORE A (3x STR)"
    assert summary_lines(rows, by_kind=True, hidden={"a", "b"})[0] == "4 Intl. conf."
    fr = summary_lines(rows, short=True, by_kind=True, years=False, lang="fr")
    assert fr[0] == "4 Conf. int.: 3 CORE A (3x STR); 1 CORE B (STR)"
    assert fr[-1] == "1 Autre (sans canal)"
    # Per category: just the count, the venues without years.
    mixed = summary_lines(rows, short=True, by_kind=True, details={"a": "list", "b": "count"})
    assert mixed[0] == "4 Intl. conf.: 3 CORE A (3x STR); 1 CORE B"
    assert summary_lines(rows, details={"a": "off", "b": "count"})[0] == "1 CORE B"
    md = summary_lines(rows, short=True, by_kind=True, markdown=True)
    assert md[0] == "- 4 Intl. conf.: 3 CORE A (2x STR 2024, STR 2023); 1 CORE B (STR 2019)"
    assert md[-1] == "- 1 Other (no venue 2024)"
    assert summary_lines(rows, short=True, markdown=True)[0] == "- 3 CORE A (2x STR 2024, STR 2023)"
    # The summary's language, whatever the app's.
    from sci_report_analyzer import i18n

    with i18n.using("fr"):
        rows = list(stats(pid).values())
        assert summary_lines(rows, short=True, by_kind=True, years=False) == no_years
        assert summary_lines(rows, short=True, by_kind=True, years=False, lang="fr") == fr


def test_a_workshop_does_not_take_its_main_conference_acronym():
    from sci_report_analyzer.ranking.service import service

    str_badge = service.badge_for_record(
        next(b.recordKey for b in service.search("Timely Rankings") if "STR" in b.extra["aliases"])
    )
    # Its ranking record (the main conference's) and the text after "@" name the host.
    assert venues.auto_short_name(str_badge, ["Workshop on Foo @ STR 2024 (STR)"]) == "STR"
    assert (
        venues.auto_short_name(str_badge, ["Workshop on Foo @ STR 2024 (STR)"], workshop=True)
        is None
    )
    assert (
        venues.auto_short_name(None, ["Foo Workshop (FooW) @ STR (STR)"], workshop=True) == "FooW"
    )


def _joint_setup():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "h",
        [
            pub("a", "Alpha paper", 2023, "Conférence Alpha (ALPHA)"),
            pub("b", "Beta paper", 2023, "Conférence Beta (BETA)"),
            pub("j", "Joint paper", 2023, "ALPHA-BETA 2023 Conférence commune"),
        ],
    )
    _stats(pid)
    alpha, beta, joint = (_venue_of(t) for t in ("Alpha paper", "Beta paper", "Joint paper"))
    for vid in (alpha, beta):
        venues.update_venue(vid, kind="natl_conference", level_type="conference", level_rank="B")
    return pid, alpha, beta, joint


def _rows():
    return {r.id: r for r in asyncio.run(venues.venue_rows())}


def test_joint_venue_takes_the_level_of_its_conferences():
    pid, alpha, beta, joint = _joint_setup()
    row = _rows()[joint]
    assert [p for p, *_ in row.parts] == [alpha, beta] and not row.joint_differ
    st = _stats(pid)["Joint paper"]
    assert st.category.base_key == "b" and st.badge.extra["joint"]

    # Different levels: the lowest, until one is chosen.
    venues.update_venue(beta, level_rank="A")
    assert _rows()[joint].joint_differ
    st = _stats(pid)["Joint paper"]
    assert st.category.base_key == "b" and st.badge.extra["joint_differ"]
    venues.set_joint_use(joint, beta)
    assert _stats(pid)["Joint paper"].category.base_key == "a"

    # A level set by hand wins.
    venues.update_venue(joint, level_type="conference", level_rank="C")
    assert _stats(pid)["Joint paper"].category.base_key == "c"


def test_joint_venue_parts_by_hand_and_merge():
    _, alpha, beta, joint = _joint_setup()
    venues.set_joint_parts(joint, [])  # not a joint venue
    assert _rows()[joint].parts == []
    venues.set_joint_parts(joint, None)  # back to automatic
    assert [p for p, *_ in _rows()[joint].parts] == [alpha, beta]

    with session_scope() as s:
        dup = Venue(name="Alpha duplicate")
        s.add(dup)
        s.flush()
        dup_id = dup.id
    venues.set_joint_parts(joint, [dup_id, beta])
    venues.set_joint_use(joint, dup_id)
    venues.merge_venues(alpha, [dup_id])
    with session_scope() as s:
        assert s.get(Venue, joint).joint == {"parts": [alpha, beta], "manual": True, "use": alpha}


def test_joint_venue_named_after_its_conferences_in_order():
    """Without a name, a joint venue takes its conferences' names and acronyms, in order;
    renaming a conference renames it; a name typed is kept."""
    _, alpha, beta, _joint = _joint_setup()
    _rows()  # (the conferences' acronyms, inferred)
    vid, created = venues.add_venue("", "natl_conference", parts=[beta, alpha])
    assert created
    with session_scope() as s:
        names = s.get(Venue, beta).name, s.get(Venue, alpha).name
        assert s.get(Venue, vid).name == " / ".join(names)
    assert _rows()[vid].short_name == "BETA-ALPHA"

    venues.set_joint_parts(vid, [alpha, beta])  # (the order changed)
    venues.update_venue(alpha, name="Conférence Alpha renommée")
    with session_scope() as s:
        assert s.get(Venue, vid).name == f"Conférence Alpha renommée / {names[0]}"
    assert _rows()[vid].short_name == "ALPHA-BETA"

    venues.update_venue(vid, name="Rencontres communes")
    venues.set_joint_parts(vid, [beta, alpha])
    with session_scope() as s:
        assert s.get(Venue, vid).name == "Rencontres communes"


def test_joint_venue_named_after_its_conferences_keeps_its_old_name_as_variant():
    _, alpha, beta, joint = _joint_setup()
    _rows()  # (its parts found)
    with session_scope() as s:
        old = s.get(Venue, joint).name
    venues.set_joint_parts(joint, None, auto_name=True)  # (its automatic parts kept)
    with session_scope() as s:
        v = s.get(Venue, joint)
        assert v.parts == [alpha, beta] and v.name != old and " / " in v.name
        assert s.get(VenueKey, service.key(old)).venue_id == joint


def test_theses_have_no_venue():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "x",
        [
            pub("a", "A habilitation", 2022, "Université de Nulle Part", doc_type="HDR"),
            pub("b", "A PhD", 2015, "Université de Nulle Part", doc_type="THESE"),
        ],
    )
    s = stats(pid)
    for title in ("A habilitation", "A PhD"):
        assert s[title].kind == "thesis" and s[title].venue_id is None
        assert s[title].category.key == "k_thesis" and "no venue" not in s[title].problems


def test_books_and_chapters_have_no_venue():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "x",
        [
            pub("a", "A chapter", 2022, "Handbook of Rankings", doc_type="COUV"),
            pub("b", "A book", 2023, "Springer", doc_type="OUV"),
            pub("c", "A paper", 2023, "Conférence en Recherche d'Information", doc_type="COMM"),
        ],
    )
    s = stats(pid)
    for title in ("A chapter", "A book"):
        assert s[title].venue_id is None and s[title].venue_short is None
        assert "no venue" not in s[title].problems
    assert s["A chapter"].venue == "Handbook of Rankings"  # still shown, as its book
    assert s["A paper"].venue_id is not None
    with session_scope() as ss:
        linked = {p.title: p.venue_id for p in ss.scalars(select(Publication))}
    assert linked["A chapter"] is None and linked["A book"] is None
    # Linked to a venue by hand: kept.
    venues.link_publication(s["A chapter"].id, s["A paper"].venue_id)
    assert stats(pid)["A chapter"].venue_id == s["A paper"].venue_id


def test_a_demo_track_wins_over_no_track():
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "ACL", doi="10.1/x")])
    add_source(pid, "dblp", "x", [pub("b", "Paper A", 2020, "ACL Demos", doi="10.1/x")])
    (a,) = stats(pid).values()
    acl = next(m.venue_id for m in a.members if m.source == "hal")
    venues.save_patterns(acl, [VenuePattern(pattern="^ACL Demos$", track="demo")])
    (a,) = stats(pid).values()
    assert len({m.venue_id for m in a.members}) == 1
    # The record naming only the conference does not contradict the demo one.
    assert a.track == "demo" and not a.disagree


def test_author_position_set_by_hand_silences_the_author_warning():
    from sci_report_analyzer import annotations

    pid = make_person()
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2020, "ACL", authors=["Someone Else"])])
    (a,) = stats(pid).values()
    assert any("not found among the authors" in p for p in a.problems)
    annotations.set_overrides(a.id, author_pos_override=2)
    (a,) = stats(pid).values()
    assert not any("author" in p for p in a.problems)
    # 0: the order does not matter (e.g. edited proceedings), still set by hand.
    annotations.set_overrides(a.id, author_pos_override=0)
    (a,) = stats(pid).values()
    assert a.author_pos == 0 and a.author_pos_manual
    assert not any("author" in p for p in a.problems)


def test_main_conference_of_the_workshop_is_no_disagreement():
    from sci_report_analyzer.db.models import Venue

    pid = make_person()
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2024, "Workshop on Things", doi="10.1/w")])
    add_source(
        pid, "doi", "d", [pub("b", "Paper A", 2024, "Symposium on Timely Rankings", doi="10.1/w")]
    )
    (a,) = stats(pid).values()
    ids = {m.source: m.venue_id for m in a.members}
    with session_scope() as s:
        s.get(Venue, ids["hal"]).hosts = [{"venue_id": ids["doi"], "from": None, "to": None}]
    (a,) = stats(pid).values()
    # The workshop (more precise) wins over the DOI record giving its main conference.
    assert a.venue_id == ids["hal"] and a.kind == "intl_workshop"
    assert a.badge.coreRank == "A" and not a.disagree
    assert not any("different venues" in p for p in a.problems)
    assert [m.minor for m in sorted(a.members, key=lambda m: m.source)] == [True, False]


def test_possible_author_says_why():
    from sci_report_analyzer.pubview import PeopleIndex

    people = PeopleIndex({"jane doe"}, ["Jane Doe"], set(), [])
    marks, notes = people.marks(["Bob", "J. Doe"], None)
    assert marks == [None, "owner?"]
    assert notes[1][0] == "Is the author “J. Doe” Jane Doe? (similar name)"
    marks, notes = people.marks(["Bob", "Al", "Z. Who"], 3)
    assert notes[2][0] == "Is the author “Z. Who” Jane Doe? (3rd author for a source)"


def test_french_workshops_ranked_by_their_main_conference():
    from sci_report_analyzer.pubview import _fr_category
    from sci_report_analyzer.ranking.badge import Category

    cat = Category("ws:a", "Workshop CORE A", "#000", "a", workshop=True)
    assert _fr_category(cat, "intl_workshop", 3) == "dans une conf. CORE A"
    assert _fr_category(cat, "intl_conference", 3) == "Atelier CORE A"


def test_venue_choices_show_acronyms_first():
    with session_scope() as s:
        s.add_all(
            [
                Venue(name="Symposium on Timely Rankings (STR)"),
                Venue(name="Workshop on Quiet Ranks", short_name="QR"),
                Venue(name="Another Meeting"),
                Venue(name="Meeting on Odd Things (MOT)", short_manual=True),  # no acronym
            ]
        )
    choices = list(venues.venue_choices().values())
    # Sorted by acronym, else name; the acronym is searchable as it is in the label.
    assert choices == [
        "Another Meeting",
        "Meeting on Odd Things (MOT)",
        "[QR] Workshop on Quiet Ranks",
        "[STR] Symposium on Timely Rankings (STR)",
    ]


def test_acronym_inferred_set_by_hand_or_none():
    """An inferred acronym is stored as such (not a decision); one set by hand, or "none",
    wins and is kept by a merge."""
    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Meeting on Odd Things (MOT)")])
    stats(pid)
    asyncio.run(venues.venue_rows())
    with session_scope() as s:
        v = s.scalar(select(Venue).where(Venue.name.like("Meeting on Odd Things%")))
        vid = v.id
        assert (v.short_name, v.short_manual, v.has_manual) == ("MOT", False, False)
    venues.update_venue(vid, short_name=None, short_manual=True)  # "no acronym"
    (row,) = [r for r in asyncio.run(venues.venue_rows()) if r.id == vid]
    assert row.short_name is None and row.short_manual
    other, _new = venues.add_venue("Odd Things Meeting", "intl_conference")
    venues.merge_venues(other, [vid])
    with session_scope() as s:
        v = s.get(Venue, other)
        assert (v.short_name, v.short_manual) == (None, True)
    venues.update_venue(other, short_name=None)  # empty: inferred again
    with session_scope() as s:
        assert not s.get(Venue, other).short_manual


def test_a_track_wins_whatever_the_venue_and_different_tracks_conflict():
    from sci_report_analyzer import annotations

    pid = make_person()
    add_source(pid, "hal", "idhal:x", [pub("a", "Paper A", 2020, "Some Odd Meeting", doi="10.1/x")])
    add_source(pid, "dblp", "x", [pub("b", "Paper A", 2020, "ACL (System Demonstrations)")])
    (a,) = stats(pid).values()
    assert a.track == "demo" and not a.track_conflict  # a track wins over none
    add_source(pid, "openalex", "o", [pub("c", "Paper A", 2020, "ACL (Short Papers)")])
    (a,) = stats(pid).values()
    assert a.track_conflict
    assert any("different tracks" in p for p in a.problems)
    annotations.set_venue_source(a.id, "openalex")  # settled by hand
    (a,) = stats(pid).values()
    assert a.track == "short" and not a.track_conflict
    annotations.set_venue_source(a.id, None)
    annotations.set_track_override(a.id, "short")  # or the paper's track by hand
    (a,) = stats(pid).values()
    assert a.track == "short" and not a.track_conflict


EMNLP = "Conference on Empirical Methods in Natural Language Processing"
EMNLP_IJCNLP = f"{EMNLP} and the International Joint Conference on Natural Language Processing"


def _emnlp_setup():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "h",
        [
            pub("a", "Main paper", 2023, f"{EMNLP} (EMNLP)"),
            pub("b", "Other main paper", 2022, f"{EMNLP} (EMNLP)"),
            pub(
                "f",
                "Findings paper",
                2023,
                "Findings of the Association for Computational Linguistics: EMNLP 2023",
            ),
            pub("j", "Joint paper", 2019, EMNLP_IJCNLP),
            pub("k", "Proceedings paper", 2019, f"Proc. {EMNLP_IJCNLP}"),
        ],
    )
    _stats(pid)
    return pid, {
        t: _venue_of(t)
        for t in ("Main paper", "Findings paper", "Joint paper", "Proceedings paper")
    }


def test_related_venues_guessed():
    _, v = _emnlp_setup()
    rows = _rows()
    main = rows[v["Main paper"]]
    guesses = {
        tuple(r.id for r in group): rel
        for group, rel in venues.suggest_related(main, list(rows.values()))
    }
    joint = tuple(sorted((v["Joint paper"], v["Proceedings paper"])))
    assert guesses[(v["Findings paper"],)] == "track:findings"
    # The two joint conference texts: one group, to be merged first.
    assert {tuple(sorted(k)): rel for k, rel in guesses.items()}[joint] == "joint"
    # The other way round, from the Findings venue.
    assert venues.guess_relation(rows[v["Findings paper"]], main) == "~track:findings"
    # IJCAI is one conference, not a joint one.
    ijcai = venues.VenueRow(
        id=0,
        name="International Joint Conference on Artificial Intelligence",
        kind="",
        kind_manual=False,
        badge=None,
        manual=False,
    )
    assert not venues.looks_joint(ijcai)


def test_relate_venues_as_track_and_joint():
    pid, v = _emnlp_setup()
    main = v["Main paper"]
    assert venues.relate_venues(main, [v["Findings paper"]], "track:findings") == main
    st = _stats(pid)
    assert st["Findings paper"].venue_id == main
    with session_scope() as s:
        tracks = {k.track for k in s.scalars(select(VenueKey).where(VenueKey.venue_id == main))}
    assert tracks == {None, "findings"}

    joint, proc = v["Joint paper"], v["Proceedings paper"]
    assert venues.relate_venues(main, [joint, proc], "joint") == main
    st = _stats(pid)
    assert st["Proceedings paper"].venue_id == st["Joint paper"].venue_id == joint
    assert [p for p, *_ in _rows()[joint].parts] == [main]
    # Related now: not suggested again.
    rows = _rows()
    assert not venues.suggest_related(rows[main], list(rows.values()))


WIDG_DEMO = "WIDG (Demonstration) Conference on Widget Processing (WIDG)"


def test_relation_choices_both_ways():
    assert venues.relation_choices("~track:demo")[:2] == ["~track:demo", "same"]
    assert "track:demo" not in venues.relation_choices("~track:demo")
    both = venues.relation_choices("~track:demo", both=True)
    assert both[0] == "~track:demo" and "track:demo" in both and len(set(both)) == len(both)


def test_demo_track_merged_into_its_main_venue():
    pid = make_person()
    add_source(
        pid,
        "hal",
        "h",
        [
            pub("a", "Main paper", 2023, "Conference on Widget Processing (WIDG)"),
            pub("d", "Demo paper", 2023, WIDG_DEMO),
        ],
    )
    _stats(pid)
    main, demo = _venue_of("Main paper"), _venue_of("Demo paper")
    venues.set_level("Conference on Widget Processing (WIDG)", "conference", "A*")
    rows = _rows()
    assert venues.guess_relation(rows[demo], rows[main]) == "~track:demo"
    assert venues.relate_venues(demo, [main], "~track:demo") == main
    st = _stats(pid)
    assert st["Demo paper"].venue_id == main and st["Demo paper"].track == "demo"
    assert st["Demo paper"].category.track == "demo"
    assert st["Demo paper"].category.base_key == "as"
    assert st["Main paper"].track is None


def test_mark_as_track_without_a_main_venue():
    pid = make_person()
    add_source(pid, "hal", "h", [pub("d", "Demo paper", 2023, WIDG_DEMO)])
    _stats(pid)
    demo = _venue_of("Demo paper")
    venues.set_level(WIDG_DEMO, "conference", "A*")
    venues.mark_as_track(demo, "demo", tracks.conference_name("demo", WIDG_DEMO))
    with session_scope() as s:
        assert s.get(Venue, demo).name == "Conference on Widget Processing (WIDG)"
        keys = list(s.scalars(select(VenueKey).where(VenueKey.venue_id == demo)))
    assert keys and {k.track for k in keys} == {"demo"}
    (st,) = _stats(pid).values()
    assert st.venue_id == demo and st.track == "demo"
    assert st.category.track == "demo" and st.category.base_key == "as"


def test_venue_chip_unranked_kind_or_dropped_from_core():
    """A shared task shows no rank; a conference CORE no longer lists shows its last rank as
    such ("CORE A until 2008"), not as its rank."""
    from sci_report_analyzer.ranking.badge import Badge
    from sci_report_analyzer.ui.venues_page import _chip_html

    old = Badge(
        source="core",
        type="conference",
        name="Gadget Retrieval Conference",
        coreRank="A",
        coreEdition="CORE2008",
        coreHistory={"CORE2008": "A"},
    )
    assert venues.dropped_from_core(old) == ("A", 2008)
    assert venues.dropped_from_core(old, 2009) is None  # (still in force then)
    current = Badge(**{**old.to_dict(), "coreHistory": {"CORE2008": "A", "ICORE2026": "B"}})
    assert venues.dropped_from_core(current) is None

    def chip(kind, badge):
        return _chip_html(
            venues.VenueRow(1, "Gadget Retrieval Conference", kind, False, badge, False)
        )

    assert "CORE A until 2008" in chip("intl_conference", old)
    assert "CORE A" not in chip("shared_task", old) and "Shared task" in chip("shared_task", old)


WIDGETS = "Widget Processing Conference"


def _widget_venue() -> tuple[int, int, str]:
    """A venue with three variants (one of the demo track): (person, venue, demo key)."""
    pid = make_person()
    texts = [WIDGETS, f"Intl. {WIDGETS}", f"{WIDGETS} (Demonstrations)"]
    add_source(pid, "hal", "h", [pub(f"p{i}", t, 2023, t) for i, t in enumerate(texts)])
    _stats(pid)
    vid = _venue_of(WIDGETS)
    for raw in texts[1:]:
        venues.add_variant(vid, raw, "hal")
    demo = service.key(texts[2], "hal")
    venues.set_variant_track(demo, "demo")
    return pid, vid, demo


def test_variants_a_rule_matches():
    """A rule made from a variant: the variants it matches are redundant (removed with
    its saving), those of another track in conflict with it."""
    pid, vid, demo = _widget_venue()
    assert venues.text_regex("Widget  Processing (WIDG)") == r"Widget\s+Processing\s+\(WIDG\)"
    rule = VenuePattern(pattern=venues.text_regex(WIDGETS))
    found = venues.rule_variants(vid, rule)
    assert len(found.redundant) == 2 and all(v.track is None for v in found.redundant)
    assert [(v.key, v.track) for v in found.conflicting] == [(demo, "demo")]
    # A rule of the demo track: the demo variant is redundant, the others in conflict.
    found_demo = venues.rule_variants(vid, rule.model_copy(update={"track": "demo"}))
    assert [v.key for v in found_demo.redundant] == [demo]
    venues.save_patterns(vid, [rule], [v.key for v in found.redundant])
    assert venues.variant_keys(vid) == {demo}
    st = _stats(pid)
    assert {s.venue_id for s in st.values()} == {vid}
    assert st[WIDGETS].track is None and st[f"{WIDGETS} (Demonstrations)"].track == "demo"


def test_text_linked_by_hand_is_not_ranked_by_an_approximate_match(monkeypatch):
    """A wrong conference name (HAL) linked by hand to the paper's venue: its approximate
    ranking match is another venue's, neither the rank nor a disagreement."""
    pid = make_person()
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2012, "Meeting on Wrong Names")])
    add_source(pid, "dblp", "d", [pub("b", "Paper A", 2012, "Meeting on Right Names")])
    (a,) = stats(pid).values()
    right = next(m.venue_id for m in a.members if m.source == "dblp")
    venues.add_variant(right, "Meeting on Wrong Names")
    resolve = service.resolve

    async def approximate(raw, *args, **kw):
        if raw == "Meeting on Wrong Names":  # an approximate match of another venue
            b = await resolve("Symposium on Timely Rankings", None, "conference")
            return b.copy(exact=False, score=0.8)
        return await resolve(raw, *args, **kw)

    monkeypatch.setattr(service, "resolve", approximate)
    (a,) = stats(pid).values()
    assert {m.venue_id for m in a.members} == {right}
    assert not a.disagree and not (a.badge and a.badge.coreRank)
    assert a.problem_tab == "publication"


def test_problem_tab_of_different_venues():
    pid = make_person()
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2020, "Venue One", doi="10.1/x")])
    add_source(pid, "dblp", "d", [pub("b", "Paper A", 2020, "Venue Two", doi="10.1/x")])
    (a,) = stats(pid).values()
    assert a.disagree and a.problem_tab == "matching"
