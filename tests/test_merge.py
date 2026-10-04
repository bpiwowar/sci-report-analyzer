from helpers import add_source, make_person, pub, toggle_star
from sqlalchemy import select

from sci_report_analyzer import annotations, sync
from sci_report_analyzer.db.models import Publication, SourcePub
from sci_report_analyzer.db.session import session_scope


def _pubs(person_id):
    with session_scope() as s:
        return {
            p.id: sorted(m.link.source for m in p.members)
            for p in s.scalars(select(Publication).where(Publication.person_id == person_id))
        }


def test_doi_and_title_merge():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Neural ranking models.", 2020, "SIGIR", doi="10.1/abc"),
            pub("b", "Another paper on retrieval evaluation", 2019, "ECIR"),
        ],
    )
    add_source(
        pid,
        "hal",
        "idhal:jd",
        [
            pub("h1", "Totally different title", 2020, "SIGIR", doi="10.1/abc"),
            pub("h2", "Another Paper on Retrieval Evaluation", 2020, "ECIR"),
            pub("h3", "Only in HAL", 2018),
        ],
    )
    groups = sorted(_pubs(pid).values())
    assert groups == [["dblp", "hal"], ["dblp", "hal"], ["hal"]]


def test_years_too_far_apart_do_not_merge():
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Learning to rank with deep models", 2015)])
    add_source(pid, "hal", "idhal:jd", [pub("b", "Learning to rank with deep models", 2019)])
    assert len(_pubs(pid)) == 2


def test_preprint_folds_into_published_version():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("conf/x", "Learning to rank with deep models", 2021, "SIGIR"),
            pub(
                "journals/corr/abs-1901-00001",
                "Learning to rank with deep models",
                2018,
                "CoRR",
                archival=True,
                pdf_url="https://arxiv.org/pdf/1901.00001",
            ),
        ],
    )
    add_source(
        pid,
        "hal",
        "idhal:jd",
        [
            pub(
                "hal-1",
                "Learning to Rank with Deep Models",
                2017,
                None,
                doc_type="UNDEFINED",
                archival=True,
                pdf_url="https://hal.science/hal-1/document",
            ),
        ],
    )
    groups = list(_pubs(pid).values())
    assert len(groups) == 1
    with session_scope() as s:
        p = s.scalar(select(Publication))
        assert p.year == 2021  # the published version's year, not the preprint's


def test_ids_stable_and_annotations_survive_resync():
    pid = make_person()
    link = add_source(pid, "dblp", "x/1", [pub("a", "A stable paper title here", 2020)])
    with session_scope() as s:
        pub_id = s.scalar(select(Publication.id))
    annotations.set_track_override(pub_id, "demo")
    period = annotations.save_period(pid, "HDR", 2015, 2024)
    toggle_star(period, pub_id)
    # Re-sync with one more record
    from sci_report_analyzer.sources.base import FetchResult

    sync.finish_link(
        link,
        FetchResult(
            publications=[
                pub("a", "A stable paper title here", 2020),
                pub("b", "A new one appears", 2021),
            ]
        ),
    )
    with session_scope() as s:
        p = s.get(Publication, pub_id)
        assert p is not None and p.track_override == "demo"
    # The record disappears from the source: publication is kept (annotated) as missing
    sync.finish_link(link, FetchResult(publications=[pub("b", "A new one appears", 2021)]))
    with session_scope() as s:
        assert s.get(Publication, pub_id).missing


def test_hidden_survives_resync_and_new_sources():
    from sci_report_analyzer.sources.base import FetchResult

    pid = make_person()
    link = add_source(pid, "dblp", "x/1", [pub("a", "A misattributed paper title", 2020)])
    with session_scope() as s:
        pub_id = s.scalar(select(Publication.id))
    annotations.set_hidden(pub_id, True)
    sync.finish_link(
        link, FetchResult(publications=[pub("a", "A misattributed paper title", 2020)])
    )
    # Another source brings the same paper: it joins the hidden publication.
    add_source(pid, "hal", "jdoe", [pub("h", "A misattributed paper title", 2020)])
    with session_scope() as s:
        pubs = list(s.scalars(select(Publication).where(Publication.person_id == pid)))
        assert [(p.id, p.hidden, len(p.members)) for p in pubs] == [(pub_id, True, 2)]


def test_split_and_join():
    from sci_report_analyzer import merge

    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Same title for two works", 2020, doi="10.1/x")])
    add_source(pid, "hal", "idhal:jd", [pub("h", "Same title for two works", 2020)])
    assert len(_pubs(pid)) == 1
    with session_scope() as s:
        sp = s.scalar(select(SourcePub).where(SourcePub.external_key == "h"))
        merge.split_member(s, sp)
    assert len(_pubs(pid)) == 2
    sync.merge(pid)  # locked: a re-merge keeps them apart
    assert len(_pubs(pid)) == 2
    with session_scope() as s:
        a, b = s.scalars(select(Publication)).all()
        merge.join_publications(s, a, [b])
    sync.merge(pid)
    assert len(_pubs(pid)) == 1


def test_stale_status():
    pid = make_person()
    link = sync.add_link(pid, "dblp", "x/1")
    with session_scope() as s:
        from sci_report_analyzer.db.models import SourceLink

        ln = s.get(SourceLink, link)
        assert ln.is_stale and ln.status_label == "never synced"
    sync.finish_link(
        link, __import__("sci_report_analyzer.sources.base", fromlist=["x"]).FetchResult()
    )
    with session_scope() as s:
        ln = s.get(SourceLink, link)
        assert not ln.is_stale and ln.status_label == "up to date"


def test_two_publications_collapsing_into_one():
    from sci_report_analyzer.sources.base import FetchResult

    pid = make_person()
    dblp = add_source(pid, "dblp", "x/1", [pub("a", "First version of the title", 2020)])
    add_source(pid, "hal", "idhal:jd", [pub("h", "Unrelated at first", 2020, doi="10.9/z")])
    assert len(_pubs(pid)) == 2
    # DBLP now reports the same DOI: both collapse into one publication, no member lost
    sync.finish_link(
        dblp, FetchResult(publications=[pub("a", "First version of the title", 2020, doi="10.9/z")])
    )
    assert list(_pubs(pid).values()) == [["dblp", "hal"]]


async def test_discover_searches_in_natural_name_order(monkeypatch):
    from sci_report_analyzer.sources import ADAPTERS

    seen = []

    async def fake_search(name, affiliation=None):
        seen.append(name)
        return []

    for a in ADAPTERS.values():
        monkeypatch.setattr(a, "search", fake_search)
    pid = make_person("LEFEVRE Hélène")
    await sync.discover(pid)
    assert seen and set(seen) == {"Hélène LEFEVRE"}


def test_purge_removes_papers_and_records():
    from helpers import add_source, make_person, pub
    from sqlalchemy import func, select

    from sci_report_analyzer import annotations, sync
    from sci_report_analyzer.db.models import Publication, SourceLink, SourcePub
    from sci_report_analyzer.db.session import session_scope

    pid = make_person()
    other = make_person("Other Person")
    add_source(pid, "hal", "x", [pub("a", "Paper A", 2020, "V"), pub("b", "Paper B", 2020, "V")])
    add_source(other, "hal", "y", [pub("c", "Paper C", 2020, "V")])
    with session_scope() as s:
        a = s.scalar(select(Publication.id).where(Publication.title == "Paper A"))
    annotations.set_overrides(a, note="x")
    n = sync.purge_counts([pid])
    assert n == {"people": 1, "papers": 2, "records": 2, "annotated": 1}
    sync.purge([pid])
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Publication)) == 1  # the other's
        assert s.scalar(select(func.count()).select_from(SourcePub)) == 1
        link = s.scalar(select(SourceLink).where(SourceLink.person_id == pid))
        assert link.status == "validated" and link.last_synced_at is None


async def test_probe_overlap_counts_known_papers(monkeypatch):
    from sci_report_analyzer.db.models import SourceLink
    from sci_report_analyzer.sources import ADAPTERS
    from sci_report_analyzer.sources.base import FetchResult

    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d",
        [pub("a", "Neural ranking models for search", 2020, doi="10.1/a"), pub("b", "Other", 2021)],
    )
    cand = sync.add_link(pid, "hal", "h", validated=False)

    async def fake_fetch(self, ext, names):
        return FetchResult(
            publications=[
                pub("x", "Unrelated title", doi="https://doi.org/10.1/A"),  # same DOI
                pub("y", "Neural ranking models for search", 2021),  # same title
                pub("z", "Something else entirely", 2019),
                pub("w", "Yet another paper", 2018),
            ]
        )

    # (on the class: patching the instance would leave the real method on it afterwards)
    monkeypatch.setattr(type(ADAPTERS["hal"]), "fetch", fake_fetch)
    assert await sync.probe_overlap(cand) == {"matched": 2, "total": 4}
    with session_scope() as s:
        link = s.get(SourceLink, cand)
        assert link.evidence["overlap"] == {"matched": 2, "total": 4}
        assert not link.source_pubs  # nothing stored
    assert len(_pubs(pid)) == 2  # the merged list is untouched


def test_tags_and_notes_survive_resync():
    from sci_report_analyzer import pubview
    from sci_report_analyzer.sources.base import FetchResult

    pid = make_person()
    link = add_source(pid, "dblp", "x/1", [pub("a", "A stable paper title here", 2020)])
    with session_scope() as s:
        pub_id = s.scalar(select(Publication.id))
    period = annotations.save_period(pid, "HDR", 2015, 2024)
    glob = annotations.save_tag("survey")
    local = annotations.save_tag("discuss", per_period=True)

    def check(rec_pub_id=pub_id) -> None:
        import asyncio

        [s] = [x for x in asyncio.run(pubview.load_stats(pid)) if x.id == rec_pub_id]
        assert s.tags_in(period) == {glob, local}
        assert (s.note, s.period_notes) == ("**read**", {period: "in HDR"})

    # Each annotation alone keeps the paper; together they survive re-syncs.
    for annotate in (
        lambda: annotations.toggle_tag(pub_id, glob),
        lambda: annotations.toggle_tag(pub_id, local, period),
        lambda: annotations.set_note(pub_id, "**read**"),
        lambda: annotations.set_note(pub_id, "in HDR", period),
    ):
        annotate()
        sync.finish_link(
            link, FetchResult(publications=[pub("a", "A stable paper title here", 2020)])
        )
    check()
    sync.finish_link(
        link,
        FetchResult(
            publications=[
                pub("a", "A stable paper title here", 2020),
                pub("b", "A new one appears", 2021),
            ]
        ),
    )
    check()
    # Another source brings the same paper: it joins, annotations kept.
    add_source(pid, "hal", "jdoe", [pub("h", "A stable paper title here", 2020)])
    check()
    # Gone from the sources: kept (as missing) with its annotations.
    sync.finish_link(link, FetchResult(publications=[pub("b", "A new one appears", 2021)]))
    with session_scope() as s:
        assert s.get(Publication, pub_id) is not None


def test_annotations_follow_a_paper_merged_by_a_sync():
    """Two papers that a new record shows to be one: the absorbed one's tags and notes
    move to the one kept (no stale "missing" copy)."""
    from sci_report_analyzer.sources.base import FetchResult

    pid = make_person()
    link = add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Learning to rank for search", 2020, doi="10.1/a"),
            pub("b", "Learning to rank for web search engines", 2020),
        ],
    )
    with session_scope() as s:
        ids = {p.title: p.id for p in s.scalars(select(Publication))}
    assert len(ids) == 2
    loser = ids["Learning to rank for web search engines"]
    period = annotations.save_period(pid, "HDR", 2015, 2024)
    toggle_star(period, loser)
    annotations.set_note(loser, "keep this")
    # The second record now carries the DOI: the same paper.
    sync.finish_link(
        link,
        FetchResult(
            publications=[
                pub("a", "Learning to rank for search", 2020, doi="10.1/a"),
                pub("b", "Learning to rank for web search engines", 2020, doi="10.1/a"),
            ]
        ),
    )
    with session_scope() as s:
        pubs = list(s.scalars(select(Publication).where(Publication.person_id == pid)))
        assert len(pubs) == 1, [(p.id, p.title, p.missing) for p in pubs]
        [p] = pubs
        assert p.note == "keep this"
    [star] = annotations.periods(pid)[0].stars
    assert star.publication_id == p.id
