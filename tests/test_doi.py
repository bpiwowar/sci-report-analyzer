import asyncio
import json
from pathlib import Path

from helpers import add_source, make_person, pub
from sqlalchemy import func, select

from sci_report_analyzer import annotations, pubview, sync
from sci_report_analyzer.db.models import DoiRecord, Publication
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.sources import doi

FIXTURES = Path(__file__).parent / "fixtures"
FINDINGS = "10.18653/v1/2025.findings-naacl.398"
SIGIR = "10.1145/3404835.3462812"


def _fixture(name):
    return json.loads((FIXTURES / f"doi_{name}.json").read_text())


def _registry(monkeypatch, calls=None):
    answers = {FINDINGS: _fixture("crossref_findings"), SIGIR: _fixture("crossref_sigir")}

    async def fetch(d, **kw):
        if calls is not None:
            calls.append(d)
        if d not in answers:
            return "not_found", None, None, None
        return "ok", "crossref", doi.parse(answers[d], "crossref"), answers[d]

    monkeypatch.setattr(doi, "fetch_record", fetch)


def test_parse_crossref_and_csl():
    d = doi.parse(_fixture("crossref_sigir"), "crossref")
    assert d["venue"].endswith("Information Retrieval (SIGIR)")  # the event acronym
    assert (d["year"], d["venue_type"], d["archival"]) == (2021, "conference", False)
    assert d["authors"] == ["Andrew Yates", "Rodrigo Nogueira", "Jimmy Lin"]
    d = doi.parse(_fixture("csl_arxiv"), "csl")
    assert d["archival"] and d["venue"] == "arXiv"


def test_doi_record_is_the_main_source(monkeypatch):
    calls = []
    _registry(monkeypatch, calls)
    pid = make_person("Olivier Ferret")
    title = "Women do not have heart attacks! Gender biases in generated clinical cases"
    add_source(
        pid,
        "hal",
        "h/1",
        [pub("h", title, 2024, "NAACL 2025 - Annual Conference of the Nations of the Americas")],
    )
    add_source(
        pid,
        "dblp",
        "d/1",
        [pub("d", title, 2025, "NAACL: North American Chapter of the ACL", doi=FINDINGS)],
    )
    asyncio.run(sync.sync_dois(pid))
    (s,) = asyncio.run(pubview.load_stats(pid))
    assert "doi" in s.sources and s.venue_raw.startswith("Findings of the Association")
    assert s.track == "findings" and s.year == 2025
    assert s.title.startswith("“Women do not have heart attacks!”")  # the publisher's title
    assert s.authors[0] == "Fanny Ducel" and s.author_marks[2] == "owner"
    assert not s.disagree  # the DOI record decides: HAL and DBLP differ, no conflict
    assert not any("different venues" in p for p in s.problems)
    # Cached forever: not asked again.
    asyncio.run(sync.sync_dois(pid))
    assert calls == [FINDINGS]


def test_doi_given_by_hand(monkeypatch):
    _registry(monkeypatch)
    pid = make_person("Jimmy Lin")
    add_source(pid, "hal", "h/1", [pub("h", "BERT and beyond (tutorial)", 2021, "Some venue")])
    with session_scope() as s:
        pub_id = s.scalar(select(Publication.id))
    annotations.set_doi(pub_id, f"https://doi.org/{SIGIR}")
    asyncio.run(sync.sync_dois(pid))
    (s,) = asyncio.run(pubview.load_stats(pid))
    assert s.id == pub_id and s.doi_manual == SIGIR.lower()
    assert s.sources == ["doi", "hal"] and s.badge.source == "core"  # SIGIR, in CORE
    # Unknown DOIs are cached too (asked again later), without a record.
    annotations.set_doi(pub_id, "10.9999/unknown")
    asyncio.run(sync.sync_dois(pid))
    with session_scope() as ss:
        assert ss.get(DoiRecord, "10.9999/unknown").status == "not_found"
        assert ss.scalar(select(func.count()).select_from(Publication)) == 1
    (s,) = asyncio.run(pubview.load_stats(pid))
    assert s.sources == ["hal"]


def test_throttle_spaces_requests(monkeypatch):
    import itertools
    import time

    starts = []

    class Res:
        status_code = 404

    class Client:
        async def get(self, url, **kw):
            starts.append(time.monotonic())
            return Res()

    monkeypatch.setattr(doi, "client", lambda: Client())
    monkeypatch.setattr(doi, "_throttle", doi._Throttle())

    async def run():
        await asyncio.gather(*(doi._get(f"https://x/{i}") for i in range(5)))

    asyncio.run(run())
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert len(starts) == 5 and min(gaps) >= doi.MIN_INTERVAL * 0.9


def test_springer_chapter_names_its_conference():
    # No "event": the conference is in the assertions, not the book's title.
    d = doi.parse(_fixture("crossref_cmmr"), "crossref")
    assert d["venue"] == (
        "International Symposium on Computer Music Multidisciplinary Research (CMMR)"
    )
    assert (d["venue_type"], d["doc_type"], d["venue_reliable"]) == (
        "conference",
        "proceedings-article",
        True,
    )
    assert d["container"] == "Perception, Representations, Image, Sound, Music"
    # A workshop's text names its main conference already: its acronym is not appended.
    msg = {**_fixture("crossref_cmmr"), "type": "proceedings-article", "assertion": []}
    msg["container-title"] = ["Workshop on Clinical NLP @ LREC 2026"]
    msg["event"] = {"name": "LREC 2026", "acronym": "LREC"}
    assert doi.parse(msg, "crossref")["venue"] == "Workshop on Clinical NLP @ LREC 2026"


def test_venue_text_of_doi_containers():
    v = doi.venue_text
    assert v(
        "Proceedings of the 55th Annual Meeting of the Association for\n  Computational "
        "Linguistics (Volume 2: Short Papers)"
    ) == (
        "Annual Meeting of the Association for Computational Linguistics",
        "Volume 2: Short Papers",
    )
    assert v("Proceedings of the Third Conference on Machine Translation: Shared Task Papers") == (
        "Conference on Machine Translation",
        "Shared Task Papers",
    )
    assert v("2010 Ninth International Conference on Machine Learning and Applications")[0] == (
        "International Conference on Machine Learning and Applications"
    )
    assert (
        v("Proceedings of the 2024 Joint International Conference on X (LREC-COLING 2024)")[0]
        == "Joint International Conference on X (LREC-COLING)"
    )
    # "Findings of ...: NAACL 2025" is the venue, not a part.
    assert v("Findings of the Association for Computational Linguistics: NAACL 2025")[1] is None
    assert doi.anthology_acronym("10.18653/v1/2022.acl-long.583") == "ACL"
    assert doi.anthology_acronym("10.18653/v1/p17-2035") == "ACL"
    assert doi.anthology_acronym("10.18653/v1/w18-6403") is None  # workshops vary


def test_doi_series_chapters_and_repositories():
    msg = {
        "type": "book-chapter",
        "DOI": "10.1007/978-3-319-24027-5_44",
        "container-title": ["Lecture Notes in Computer Science", "Experimental IR Meets X"],
        "ISSN": ["0302-9743"],
    }
    d = doi.parse(msg, "crossref")
    assert (
        d["venue"] == "Experimental IR Meets X"
        and d["series"] == "Lecture Notes in Computer Science"
    )
    assert d["issn"] is None and not d["venue_reliable"]  # no event: not the paper's venue
    zenodo = doi.parse({"type": "dataset", "DOI": "10.5281/zenodo.1", "publisher": "Zenodo"}, "csl")
    assert zenodo["archival"] and zenodo["venue"] == "Zenodo"


def test_joint_conference_acronym_resolves_to_one_of_them():
    from sci_report_analyzer.ranking.service import service

    b = asyncio.run(
        service.resolve("Joint Meeting on Timely Rankings and Things (STR-XYZ)", None, "conference")
    )
    assert b is not None and b.name == "Symposium on Timely Rankings"


def test_an_incomplete_doi_author_list_is_not_used(monkeypatch):
    # DataCite lists a single creator of a three-author paper.
    d = "10.48465/fa.2020.0972"
    msg = {
        "DOI": d,
        "type": "article-journal",
        "title": "A technological platform",
        "author": [{"family": "Rozier", "given": "Jules"}],
        "issued": {"date-parts": [[2020]]},
    }

    async def fetch(x, **kw):
        return ("ok", "csl", doi.parse(msg, "csl"), msg) if x == d else ("not_found",) + (None,) * 3

    monkeypatch.setattr(doi, "fetch_record", fetch)
    pid = make_person("Yuki Tanabe")
    authors = ["Jules Rozier", "Yuki Tanabe", "Henri Kessler-Martin"]
    add_source(
        pid,
        "hal",
        "h/1",
        [pub("h", "A technological platform", 2020, "Forum Acusticum", doi=d, authors=authors)],
    )
    asyncio.run(sync.sync_dois(pid))
    (s,) = asyncio.run(pubview.load_stats(pid))
    assert "doi" in s.sources and s.authors == authors and s.num_authors == 3


def test_crossref_is_asked_by_batches(monkeypatch):
    batches, singles = [], []
    sigir = _fixture("crossref_sigir")

    async def batch(dois):
        batches.append(list(dois))
        return {SIGIR: sigir}

    async def fetch(d, *, crossref=True):
        singles.append((d, crossref))
        return "not_found", None, None, None

    monkeypatch.setattr(doi, "fetch_crossref_batch", batch)
    monkeypatch.setattr(doi, "fetch_record", fetch)
    monkeypatch.setattr(doi, "BATCH", 2)
    other = [f"10.9999/x{i}" for i in range(3)]
    counts = asyncio.run(doi.ensure([SIGIR, *other]))
    assert batches == [[SIGIR, other[0]], other[1:]]  # 4 DOIs in 2 requests
    # Unknown to Crossref: only doi.org is asked (not Crossref again).
    assert sorted(singles) == [(d, False) for d in other]
    assert counts == {"fetched": 1, "not_found": 3, "errors": 0}
    assert doi.cached([SIGIR])[SIGIR].origin == "crossref"
