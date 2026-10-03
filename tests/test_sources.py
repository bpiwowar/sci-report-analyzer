import pytest

from sci_report_analyzer.sources import ADAPTERS
from sci_report_analyzer.sources.base import NoContactEmail, client
from sci_report_analyzer.sources.hal import meta_from_doc
from sci_report_analyzer.sources.openalex import work_to_pub
from sci_report_analyzer.sources.scholar import parse_profile

SCHOLAR_HTML = """
<div id="gsc_prf_in">Jane Doe</div>
<table><tbody>
<tr class="gsc_a_tr"><td class="gsc_a_t">
 <a class="gsc_a_at" href="/citations?view_op=view_citation&user=AAAAAAAAAAAA&citation_for_view=AAAAAAAAAAAA:k1">A great paper</a>
 <div class="gs_gray">J Doe, B Smith</div>
 <div class="gs_gray">European Conference on Information Retrieval<span class="gs_oph">, 2019</span></div>
</td><td class="gsc_a_y"><span>2019</span></td></tr>
<tr class="gsc_a_tr"><td class="gsc_a_t">
 <a class="gsc_a_at" href="/citations?citation_for_view=AAAAAAAAAAAA:k2">Preprint thing</a>
 <div class="gs_gray">B Smith, C Lee, ...</div>
 <div class="gs_gray">arXiv preprint arXiv:2001.00001</div>
</td><td class="gsc_a_y"><span>2020</span></td></tr>
</tbody></table>"""


def test_parse_scholar_profile():
    r = parse_profile(SCHOLAR_HTML)
    assert r.display_name == "Jane Doe"
    a, b = r.publications
    assert a.venue == "European Conference on Information Retrieval" and a.year == 2019
    assert a.author_pos == 1 and a.num_authors == 2
    assert b.authors == [] and b.archival  # truncated author list, arXiv preprint


def test_hal_doc():
    p = meta_from_doc(
        {
            "halId_s": "hal-1",
            "docType_s": "COMM",
            "conferenceTitle_s": "SIGIR",
            "journalTitle_s": "Nope",
            "producedDateY_i": 2021,
            "title_s": ["T"],
            "authFullName_s": ["A B", "Jane Doe"],
            "fileMain_s": "https://hal/x.pdf",
        },
        ["Jane Doe"],
    )
    assert (p.venue, p.venue_type, p.author_pos, p.pdf_url) == (
        "SIGIR",
        "conference",
        2,
        "https://hal/x.pdf",
    )


def test_hal_wrong_arxiv_doi():
    doc = {"halId_s": "hal-1", "title_s": ["T"], "arxivId_s": "2006.09545v2"}
    wrong = meta_from_doc({**doc, "doiId_s": "10.48550/arXiv.2202.02363"}, [])
    assert wrong.doi == "10.48550/arxiv.2006.09545"
    right = meta_from_doc({**doc, "doiId_s": "10.48550/arXiv.2006.09545"}, [])
    assert right.doi == "10.48550/arxiv.2006.09545"
    published = meta_from_doc({**doc, "doiId_s": "10.1145/1"}, [])
    assert published.doi == "10.1145/1"


def test_openalex_work():
    p = work_to_pub(
        {
            "id": "https://openalex.org/W1",
            "display_name": "T",
            "publication_year": 2020,
            "type": "article",
            "doi": "https://doi.org/10.1/ABC",
            "primary_location": {
                "source": {"display_name": "Nature", "type": "journal", "issn_l": "0028-0836"}
            },
            "authorships": [
                {"author": {"id": "https://openalex.org/A2", "display_name": "X"}},
                {"author": {"id": "https://openalex.org/A1", "display_name": "Me"}},
            ],
        },
        "a1",
    )
    assert (p.author_pos, p.doi, p.venue_type, p.issn) == (2, "10.1/abc", "journal", "0028-0836")


def test_parse_urls():
    assert ADAPTERS["dblp"].parse_url("https://dblp.org/pid/15/2146.html") == "15/2146"
    hal = ADAPTERS["hal"]
    for url in (
        "https://hal.science/search/index/q/*/authIdHal_s/yannis-kostakis",
        "https://hal.science/search/index/?q=*&authIdHal_s=yannis-kostakis",
        'https://api.archives-ouvertes.fr/search/?q=authIdHal_s:"yannis-kostakis"',
        "https://hal.science/search/index/?q=authIdHal_s%3A%22yannis-kostakis%22",
    ):
        assert hal.parse_url(url) == "idhal:yannis-kostakis", url
    assert hal.parse_url("https://hal.science/search/index/q/*/authIdPerson_i/12345") == (
        "person:12345"
    )
    assert ADAPTERS["hal"].parse_url("https://cv.hal.science/benjamin-piwowarski") == (
        "idhal:benjamin-piwowarski"
    )
    assert ADAPTERS["openalex"].parse_url("https://openalex.org/authors/A5023888391") == (
        "A5023888391"
    )
    assert ADAPTERS["orcid"].parse_url("https://orcid.org/0000-0001-6792-3262") == (
        "0000-0001-6792-3262"
    )
    assert (
        ADAPTERS["scholar"].parse_url(
            "https://scholar.google.com/citations?user=AbCdEfGAAAAJ&hl=en"
        )
        == "AbCdEfGAAAAJ"
    )
    assert (
        ADAPTERS["semanticscholar"].parse_url(
            "https://www.semanticscholar.org/author/Julien-Marchetti/1234567"
        )
        == "1234567"
    )
    assert ADAPTERS["thesesfr"].parse_url("https://theses.fr/081580304") == "081580304"


async def test_thesesfr_search_skips_records_without_idref(monkeypatch) -> None:
    from sci_report_analyzer.sources import thesesfr

    async def fake_get_json(url, **kw):
        return {
            "personnes": [
                {"id": "070709076", "nom": "Gallinari", "prenom": "Patrick", "has_idref": True},
                {"id": "SRqb8KABMCY-ejDrwziO", "nom": "Gallinari", "prenom": "Patrick"},
                {"id": "9Rea8KABMCY-ejDriKhl", "nom": "Gallinari", "has_idref": False},
            ]
        }

    monkeypatch.setattr(thesesfr, "get_json", fake_get_json)
    found = await ADAPTERS["thesesfr"].search("Patrick Gallinari")
    assert [c.external_id for c in found] == ["070709076"]


async def test_hal_search_finds_authors_through_documents(monkeypatch) -> None:
    from sci_report_analyzer.sources import hal

    async def fake_get_json(url, params=None, **kw):
        if "/ref/author/" in url:
            return {"response": {"docs": []}}  # the referential misses them
        if params.get("facet"):
            sep = "_FacetSep_"
            return {
                "facet_counts": {
                    "facet_fields": {
                        "authFullNamePersonIDIDHal_fs": [
                            f"Hélène Lefèvre{sep}20620{sep}helene-lefevre",
                            150,
                            f"Hélène Lefèvre{sep}0{sep}",  # unidentified
                            9,
                            f"Marc Weissberg{sep}14995{sep}marc-weissberg",
                            80,
                        ]
                    }
                }
            }
        return {"response": {"numFound": 150, "docs": [{"title_s": ["A paper"]}]}}

    monkeypatch.setattr(hal, "get_json", fake_get_json)
    found = await ADAPTERS["hal"].search("Hélène Lefevre")
    assert [c.external_id for c in found] == ["idhal:helene-lefevre"]


def test_dblp_venue_skips_book_series():
    from sci_report_analyzer.sources.dblp import dblp_venue

    lncs = "Lecture Notes in Computer Science (LNCS)"
    aime = "International Conference on Artificial Intelligence in Medicine (AIME)"
    # Series known from the stream URI, or recognised by title for older records.
    assert dblp_venue("AIME (1)", [lncs, aime], [lncs]) == aime
    assert dblp_venue("AIME (1)", [lncs, aime], []) == aime
    # The acronym picks the venue among several non-series streams.
    icad = "International Conference on Auditory Display (ICAD)"
    cmmr = "Computer Music Modeling and Retrieval (CMMR)"
    assert dblp_venue("CMMR", [icad, lncs, cmmr], []) == cmmr
    # A series alone is still better than nothing.
    assert dblp_venue("Foo", [lncs], [lncs]) == lncs


def test_dblp_colocated_workshop_keeps_its_title():
    from sci_report_analyzer.ranking.badge import detect_track
    from sci_report_analyzer.ranking.kinds import WORKSHOP_RE, host_text
    from sci_report_analyzer.sources.dblp import dblp_venue

    mm = "ACM International Conference on Multimedia (MM)"
    v = dblp_venue("Trustworthy AI @ ACM Multimedia", [mm], [])
    assert v == f"Trustworthy AI @ ACM Multimedia: {mm}"
    assert WORKSHOP_RE.search(v) and detect_track(v) is None  # a workshop, not a track
    assert host_text(v).startswith("ACM Multimedia")
    assert dblp_venue("ACM Multimedia", [mm], []) == mm  # the main conference
    # The stream is the satellite event itself: no prefix (one venue whatever the host).
    bionlp = "Workshop on Biomedical Natural Language Processing (BioNLP)"
    assert dblp_venue("BioNLP@ACL", [bionlp], []) == bionlp
    wmt = "Conference on Machine Translation (WMT)"
    assert dblp_venue("WMT@EMNLP", [wmt], []) == wmt
    assert WORKSHOP_RE.search("Proc. of X, co-located with ECIR")
    assert host_text("Proc. of X, co-located with ECIR") == "ECIR"
    assert not WORKSHOP_RE.search("jane@example.org")


def test_person_orcid_helps_matching():
    from helpers import make_person

    from sci_report_analyzer import sync
    from sci_report_analyzer.db.models import Person, SourceLink
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    mine, other = "0000-0002-1825-0097", "0000-0001-5109-3700"
    with session_scope() as s:
        for ext, orcid in (("a", mine), ("b", other), ("c", None)):
            s.add(
                SourceLink(
                    person_id=pid,
                    source="hal",
                    external_id=ext,
                    display_name="Jane Doe",
                    evidence={"orcid": orcid},
                    score=0,
                    status="candidate",
                )
            )
    assert sync.normalize_orcid(f"https://orcid.org/{mine.replace('-', '')}") == mine
    assert sync.normalize_orcid("not one") is None
    sync.set_orcid(pid, f"https://orcid.org/{mine}")
    with session_scope() as s:
        person = s.get(Person, pid)
        scores = {ln.external_id: ln.score for ln in person.links}
        assert person.orcid == mine
    assert scores["a"] > scores["c"] > scores["b"]  # same ORCID, none, another one
    # Proposed from a validated profile, when the person has none.
    sync.set_orcid(pid, None)
    with session_scope() as s:
        ln = s.query(SourceLink).filter_by(external_id="a").one()
        assert sync.suggested_orcid(s.get(Person, pid)) is None
        ln.status = "validated"
        s.flush()
        assert sync.suggested_orcid(s.get(Person, pid)) == mine


def test_no_request_without_email(monkeypatch):
    monkeypatch.delenv("SCI_REPORT_ANALYZER_EMAIL")
    with pytest.raises(NoContactEmail):
        client()
