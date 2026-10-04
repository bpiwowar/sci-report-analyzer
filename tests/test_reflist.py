"""Tag from a pasted list: its items, and the papers they match."""

from helpers import add_source, make_person, pub

from sci_report_analyzer import annotations, pubview, reflist
from sci_report_analyzer.sources import hal
from sci_report_analyzer.sources.base import FetchResult

PASTED = """\
1- Learning to rank with sparse represen-
tations of documents. J. Doe, B. Smith. SIGIR 2022.
Lien : https://hal.science/hal-03700001v2
2. Neural retrieval models for long documents
J. Doe, A. Other. ECIR, 2021
[3] Deep ranking for serach (with a typo). J. Doe. Neural Computation, 2021.
doi: 10.1162/NECO.2021.1
4) A paper that is not there at all. J. Doe. Somewhere, 2018.
"""


def test_split_items():
    items = reflist.split_items(PASTED)
    assert [i.number for i in items] == [1, 2, 3, 4]
    assert items[0].text.startswith("Learning to rank with sparse representations of documents.")
    assert items[0].hal == "hal-03700001" and items[0].url.startswith("https://hal.science/")
    assert items[1].hal is None and items[1].years == {2021}
    assert items[2].doi == "10.1162/neco.2021.1"
    assert items[0].words.endswith("SIGIR 2022")  # without "Lien : https://…"
    assert items[2].words.endswith("Neural Computation, 2021")
    # Without numbers: one item per paragraph.
    plain = reflist.split_items("A first paper\ncontinued\n\nA second paper")
    assert [(i.number, i.text) for i in plain] == [
        (None, "A first paper continued"),
        (None, "A second paper"),
    ]


# A report's list copied from a PDF: items over several lines, link marks and a page number
# inside item 9, and the next section's heading after the last one.
REPORT_PAPERS = [  # title, DOI or HAL id, year
    ("Sparse retrieval with learned term weights", "10.1234/test.1", 2023),
    ("Dense passage ranking for open domain questions", "10.1234/test.2", 2024),
    ("Query expansion with pseudo relevance feedback", "10.1234/test.3", 2016),
    ("Evaluating rankers on long documents", "10.1234/test.4", 2024),
    ("Cross lingual transfer for document ranking", "10.1234/test.5", 2021),
    ("Efficient inverted indexes for learned sparse models", "10.1234/test.6", 2020),
    ("Conversational search with clarifying questions", "10.1234/test.7", 2021),
    ("A survey of neural information retrieval", "tel-01234567", 2022),
    ("A shared task on table question answering", "10.1234/test.9", 2016),
    ("Distilling cross encoders into bi encoders", "10.1234/test.10", 2025),
]


def _report() -> str:
    out = []
    for n, (title, ref, year) in enumerate(REPORT_PAPERS, 1):
        link = f"https://doi.org/{ref}" if "/" in ref else f"https://hal.science/{ref}v1"
        junk = "🔗\n" * 9 + "3\n" if n == 9 else ""
        out.append(
            f"{n}- J. Doe, A. Author and B. Other ({year}) {title}. In Proceedings of the 10th\n"
            f"{junk}Workshop on Things, pages: 19-30, June, Somewhere\nLien : {link}\n"
        )
    return "".join(out) + "Décrire de manière synthétique (max.3000 caractère\n"


REPORT = _report()


def test_split_items_drops_pdf_junk():
    items = reflist.split_items(REPORT)
    assert [i.number for i in items] == list(range(1, 11))
    assert [(i.ref, min(i.years)) for i in items] == [(r, y) for _, r, y in REPORT_PAPERS]
    assert items[8].words.endswith(
        "Proceedings of the 10th Workshop on Things, pages: 19-30, June, Somewhere"
    )
    # Not a page number: a year alone on its line.
    (item,) = reflist.split_items("1- A paper. J. Doe. Venue,\n2021\n")
    assert item.years == {2021}


def _person() -> int:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Learning to Rank with Sparse Representations of Documents", 2022, "SIGIR"),
            pub("b", "Neural retrieval models for long documents", 2021, "ECIR"),
            pub(
                "c",
                "Deep ranking for search",
                2021,
                "Neural Computation",
                doi="10.1162/neco.2021.1",
            ),
            pub("d", "Neural retrieval models", 2019, "CoRR"),
        ],
    )
    return pid


async def test_match_by_id_then_title():
    pid = _person()
    rows = await pubview.load_stats(pid)
    by_title = {r.title: r.id for r in rows}
    matches = reflist.match(reflist.split_items(PASTED), rows)
    best = [m.best.pub_id if m.best else None for m in matches]
    assert best == [
        by_title["Learning to Rank with Sparse Representations of Documents"],
        by_title["Neural retrieval models for long documents"],  # not its shorter namesake
        by_title["Deep ranking for search"],  # by its DOI, despite the typo
        None,
    ]
    assert matches[2].best.by_id


async def test_tag_numbered_and_report():
    pid = _person()
    period = annotations.save_period(pid, "Report", 2018, 2024)
    rows = await pubview.load_stats(pid)
    matches = reflist.match(reflist.split_items(PASTED), rows)
    star = annotations.starred_tag_id()
    annotations.tag_numbered(
        star, {m.best.pub_id: m.item.number for m in matches if m.best}, period
    )
    rows = await pubview.load_stats(pid)
    numbered = {r.title: r.number_of(star, period) for r in rows if r.tags_in(period)}
    assert numbered == {
        "Learning to Rank with Sparse Representations of Documents": 1,
        "Neural retrieval models for long documents": 2,
        "Deep ranking for search": 3,
    }
    # Tagging again keeps the tag, with its new number.
    first = next(r.id for r in rows if r.number_of(star, period) == 1)
    annotations.tag_numbered(star, {first: 7}, period)
    rows = await pubview.load_stats(pid)
    assert next(r for r in rows if r.id == first).number_of(star, period) == 7


async def test_search_on_hal(monkeypatch):
    async def search_documents(words, rows=10):
        assert "paper" in words
        return [
            {
                "hal": "hal-04000001",
                "title": "A paper that is not there at all",
                "year": 2018,
                "authors": ["Jane Doe"],
                "url": "https://hal.science/hal-04000001",
            },
            {"hal": "hal-1", "title": "Something else entirely", "year": 2018, "authors": []},
        ]

    monkeypatch.setattr(hal, "search_documents", search_documents)
    item = reflist.split_items(PASTED)[3]
    found = await reflist.search(item)
    assert [f.ref for f in found] == ["hal-04000001"]


async def test_added_paper_is_matched(monkeypatch):
    async def fetch(self, external_id, owner_names):
        doc = {
            "halId_s": "hal-04000001",
            "title_s": ["A paper that is not there at all"],
            "producedDateY_i": 2018,
            "docType_s": "ART",
            "journalTitle_s": "Somewhere",
            "authFullName_s": ["Jane Doe"],
        }
        return FetchResult(publications=[hal.meta_from_doc(doc, owner_names)])

    from sci_report_analyzer import manual

    async def no_dois(person_id):  # (no DOI lookups on the network)
        return None

    monkeypatch.setattr(hal.HalAdapter, "fetch", fetch)
    monkeypatch.setattr(manual, "sync_dois", no_dois)
    pid = _person()

    await manual.add_publication(pid, "hal-04000001")
    rows = await pubview.load_stats(pid)
    item = reflist.Item(4, "whatever", hal="hal-04000001")
    (m,) = reflist.match([item], rows)
    assert m.best and m.best.by_id and m.best.title == "A paper that is not there at all"
