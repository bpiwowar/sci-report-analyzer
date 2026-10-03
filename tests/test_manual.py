"""Publications added by hand (by DOI or HAL document)."""

import pytest
from helpers import add_source, make_person, pub
from test_doi import SIGIR, _registry

from sci_report_analyzer import manual, pubview
from sci_report_analyzer.sources import ADAPTERS, hal
from sci_report_analyzer.sources.base import FetchResult


def test_parse_reference():
    p = manual.parse_reference
    assert p("10.1145/3404835.3462812") == manual.Reference("doi", "10.1145/3404835.3462812")
    assert p("https://doi.org/10.1145/ABC") == manual.Reference("doi", "10.1145/abc")
    assert p("doi:10.1145/abc") == manual.Reference("doi", "10.1145/abc")
    assert p("hal-01234567") == manual.Reference("hal", "hal-01234567")
    assert p("hal-01234567v2") == manual.Reference("hal", "hal-01234567")
    for url in (
        "https://hal.science/hal-01234567",
        "https://hal.science/hal-01234567v3/document",
        "https://inria.hal.science/hal-01234567/file/x.pdf",
        "https://hal.archives-ouvertes.fr/hal-01234567",
    ):
        assert p(url) == manual.Reference("hal", "hal-01234567"), url
    assert p("https://theses.hal.science/tel-04012345") == manual.Reference("hal", "tel-04012345")
    for url in (
        "http://hal.inria.fr/hal-01234567v1",
        "https://hal.univ-lorraine.fr/hal-01234567/document",
        "https://hal-cea.archives-ouvertes.fr/hal-01234567",
        "hal.science/hal-01234567?lang=en",
    ):
        assert p(url) == manual.Reference("hal", "hal-01234567"), url
    for url in (
        "https://www.doi.org/10.1145/3404835.3462812",
        "http://dx.doi.org/10.1145/3404835.3462812",
        "https://doi.org/10.1145%2F3404835.3462812",
        "https://dl.acm.org/doi/10.1145/3404835.3462812",
        "https://dl.acm.org/doi/pdf/10.1145/3404835.3462812?download=true",
        "https://dl.acm.org/doi/abs/10.1145/3404835.3462812#sec-1",
    ):
        assert p(url) == manual.Reference("doi", "10.1145/3404835.3462812"), url
    assert p("https://link.springer.com/chapter/10.1007/978-3-030-45439-5_1/") == (
        manual.Reference("doi", "10.1007/978-3-030-45439-5_1")
    )
    assert p("https://example.org/hal-01234567") is None  # not a HAL portal
    assert p("benjamin-piwowarski") is None
    assert p("not a reference") is None
    # In the "add a profile" box, a document becomes a link to it (not an idHAL).
    assert ADAPTERS["hal"].parse_url("hal-01234567") == "doc:hal-01234567"
    assert ADAPTERS["hal"].parse_url("yannis-kostakis") == "idhal:yannis-kostakis"


async def test_add_by_doi(monkeypatch):
    _registry(monkeypatch)
    pid = make_person("Jimmy Lin")
    add_source(pid, "dblp", "d/1", [pub("a", "Another paper", 2020, "Some venue")])
    title = await manual.add_publication(pid, f"https://doi.org/{SIGIR}")
    assert title.startswith("Pretrained Transformers")
    stats = await pubview.load_stats(pid)
    assert {s.title for s in stats} == {"Another paper", title}
    assert next(s for s in stats if s.title == title).sources == ["doi"]
    (item,) = manual.added(pid)
    assert (item.kind, item.id, item.title) == ("doi", SIGIR, title)
    # Adding it again changes nothing.
    await manual.add_publication(pid, SIGIR)
    assert len(manual.added(pid)) == 1

    await manual.remove(pid, item)
    assert manual.added(pid) == []
    assert [s.title for s in await pubview.load_stats(pid)] == ["Another paper"]


async def test_unknown_doi_is_refused(monkeypatch):
    _registry(monkeypatch)
    pid = make_person()
    with pytest.raises(ValueError, match="not registered"):
        await manual.add_publication(pid, "10.9999/unknown")
    with pytest.raises(ValueError, match="Not a DOI"):
        await manual.add_publication(pid, "hello")
    assert manual.added(pid) == []


async def test_add_by_hal_merges_with_the_other_sources(monkeypatch):
    queries = []

    async def fetch(self, external_id, owner_names):
        queries.append(hal._query(external_id))
        if external_id != "doc:hal-01234567":
            return FetchResult()
        doc = {
            "halId_s": "hal-01234567",
            "title_s": ["Deep ranking for search"],
            "producedDateY_i": 2021,
            "docType_s": "ART",
            "journalTitle_s": "Neural Computation",
            "authFullName_s": ["Bob", "Jane Doe"],
        }
        return FetchResult(publications=[hal.meta_from_doc(doc, owner_names)])

    monkeypatch.setattr(hal.HalAdapter, "fetch", fetch)
    pid = make_person("Jane Doe")
    add_source(pid, "dblp", "d/1", [pub("a", "Deep ranking for search", 2021, "Neural Comp.")])

    assert await manual.add_publication(pid, "https://hal.science/hal-01234567v2") == (
        "Deep ranking for search"
    )
    assert queries == ['halId_s:"hal-01234567"']
    (s,) = await pubview.load_stats(pid)
    assert sorted(s.sources) == ["dblp", "hal"] and s.author_pos == 2
    (item,) = manual.added(pid)
    assert (item.kind, item.id, item.url) == (
        "hal",
        "hal-01234567",
        "https://hal.science/hal-01234567",
    )

    with pytest.raises(ValueError, match="not found"):
        await manual.add_publication(pid, "hal-07654321")
    assert len(manual.added(pid)) == 1  # (its link is not kept)

    await manual.remove(pid, item)
    assert manual.added(pid) == []
    (s,) = await pubview.load_stats(pid)
    assert s.sources == ["dblp"]
