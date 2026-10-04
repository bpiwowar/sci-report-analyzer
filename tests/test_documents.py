"""Documents of a person within a period / folder: storage, the papers found in them
(titles, ids, citations, links by hand), bookmarks, and their pages."""

import asyncio
import re

import pytest
from helpers import add_source, make_person, note_saved, pub
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import documents, folders, pdfs, pubview
from sci_report_analyzer.db.models import Publication
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ui.mdedit import MarkdownEditor, quote

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

PDF = b"%PDF-1.4\n% a tiny test file\n%%EOF\n"
TITLES = {
    "a": "Deep ranking models for search",
    "b": "Neural retrieval models for long documents",
    "c": "Sparse representations of queries and documents for efficient retrieval",
    "d": "Deep ranking models",
}


def _person() -> tuple[int, int, dict[str, int]]:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", TITLES["a"], 2021, "SIGIR"),
            pub("b", TITLES["b"], 2022, "ECIR", doi="10.1145/3404835.3462889"),
            pub("c", TITLES["c"], 2023, "ICLR"),
            pub("d", TITLES["d"], 2019, "CIKM"),
        ],
    )
    with session_scope() as s:
        pubs = s.scalars(select(Publication).where(Publication.person_id == pid))
        ids = {k: next(p.id for p in pubs if p.title == t) for k, t in TITLES.items()}
    folder = folders.save_folder(None, "Hiring 2026")
    period = folders.add_person(folder, pid)
    return pid, period, ids


def _line(page: int, y: float, text: str) -> dict:
    return {"p": page, "t": text, "r": [72.0, y, 72.0 + 5 * len(text), y + 10]}


LINES = [
    _line(1, 700, "Research statement"),
    _line(1, 680, "Our ranking model [1] beats sparse ones [2, 3]; see also [1-2]."),
    _line(2, 700, "Publications"),
    _line(2, 680, "[1] J. Doe. Deep ranking models for search. In SIGIR, 2021."),
    _line(2, 665, "[2] J. Doe. Sparse representations of queries and docu-"),
    _line(2, 650, "ments for efficient retrival. In ICLR, 2023."),  # (a typo)
    _line(2, 635, "[3] J. Doe. Long documents. ECIR 2022. doi:10.1145/3404835.3462889"),
    _line(2, 620, "[4] J. Doe. Deep ranking models. In CIKM, 2019."),
]


async def test_find_papers():
    pid, _, ids = _person()
    rows = await pubview.load_stats(pid)
    found = documents.find_papers(LINES, rows)
    refs = [(m.pub_id, m.kind, m.label) for m in found if m.kind != "cite"]
    assert refs == [
        (ids["a"], "title", "1"),  # not "Deep ranking models" (a part of its title)
        (ids["c"], "title", "2"),  # across a broken word, with a typo
        (ids["b"], "id", "3"),  # by its DOI
        (ids["d"], "title", "4"),
    ]
    sparse = next(m for m in found if m.pub_id == ids["c"] and m.kind == "title")
    assert sparse.page == 2 and len(sparse.rects) == 2  # one per line
    cites = [(m.pub_id, m.label) for m in found if m.kind == "cite"]
    assert cites == [
        (ids["a"], "1"),
        (ids["c"], "2"),
        (ids["b"], "3"),
        (ids["a"], "1"),
        (ids["c"], "2"),
    ]
    first = next(m for m in found if m.kind == "cite")
    x0, _, x1, _ = first.rects[0]
    assert LINES[1]["r"][0] < x0 < x1 < LINES[1]["r"][2]  # the citation's part of the line

    # A wrong match, rejected: neither found there nor cited.
    links = [{"pub": ids["d"], "line": 7, "reject": True}]
    again = documents.find_papers(LINES, rows, links)
    assert ids["d"] not in {m.pub_id for m in again}
    # A link by hand (a selection): also gives its reference's label to the citations.
    lines = [*LINES[:3], _line(2, 680, "[1] J. Doe. Something else entirely, 2021.")]
    links = [{"pub": ids["a"], "p": 2, "r": [[80, 682, 200, 688]], "text": "Something else"}]
    found = documents.find_papers(lines, rows, links)
    assert [(m.pub_id, m.kind) for m in found if m.label == "1"] == [
        (ids["a"], "cite"),
        (ids["a"], "cite"),
        (ids["a"], "manual"),
    ]


def test_selection_item():
    item = documents.selection_item("[3] J. Doe. Long docu-\nments. doi:10.1145/3404835.3462889")
    assert item.doi == "10.1145/3404835.3462889" and item.words.startswith("J. Doe. Long documents")


def test_store_and_cleanup():
    pid, period, _ = _person()
    with pytest.raises(pdfs.PdfError):
        documents.add(period, "cv.pdf", b"<html></html>")
    doc = documents.add(period, "Application.pdf", PDF)
    path = documents.file_of(doc)
    assert path.read_bytes() == PDF and path.parent.parent.name == "documents"
    assert [d.name for d in documents.of_person(pid)[period]] == ["Application"]
    documents.save(doc, PDF + b"%annotated")
    assert documents.of_person(pid)[period][0].edited
    u = pdfs.usage()
    assert u.files == 1 and not u.orphans  # a document's file is no orphan
    documents.add_bookmark("doc", doc, "", 3, 500.0)
    documents.add_bookmark("doc", doc, "Teaching", 1, 200.0)
    assert [b["name"] for b in documents.bookmarks("doc", doc)] == ["Teaching", "Page 3"]
    # Taking the person out of the folder deletes its documents (their files: by the
    # clean-up).
    folders.remove_person(folders.folders()[0].id, pid)
    assert documents.info(doc) is None and pdfs.cleanup() == (1, 0)
    assert not path.exists()


def _viewer(monkeypatch, tmp_path) -> None:
    viewer = tmp_path / "pdfjs"
    (viewer / "web").mkdir(parents=True)
    (viewer / "web" / "viewer.html").write_text("<html></html>")
    monkeypatch.setattr(pdfs, "viewer_dir", lambda: viewer)


async def test_documents_tab(user: User):
    pid, period, _ = _person()
    await user.open(f"/person/{pid}?tab=documents")
    await user.should_see(marker=f"documents-{period}")
    await user.should_see("No document")
    upload = user.find(marker=f"documents-upload-{period}").elements.pop()
    files = [
        SmallFileUpload(name="Application.pdf", content_type="application/pdf", _data=PDF),
        SmallFileUpload(name="notes.txt", content_type="text/plain", _data=b"hello"),
    ]
    for f in files:
        await upload.handle_uploads([f])
    await user.should_see("Application")
    await user.should_see("notes.txt: not a PDF file")
    [doc] = documents.of_person(pid)[period]
    user.find(marker=f"document-rename-{doc.id}").click()
    user.find(marker="document-name").clear().type("CV")
    user.find(marker="document-rename-ok").click()
    await user.should_see("CV")
    user.find(marker=f"document-delete-{doc.id}").click()
    user.find(marker="document-delete-ok").click()
    await user.should_see("No document")
    assert not documents.of_person(pid)


async def test_documents_tab_without_period(user: User):
    pid = make_person("John Roe")
    await user.open(f"/person/{pid}?tab=documents")
    await user.should_see(marker="documents-no-period")


async def test_document_page(user: User, monkeypatch, tmp_path):
    _, period, ids = _person()
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-frame")
    await user.should_see("Looking for the papers…")  # (until the viewer sent the text)
    documents.set_lines(doc, LINES)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"doc-paper-{ids['a']}")
    await user.should_see(marker=f"doc-paper-{ids['b']}")
    # Searching the list (every word, in the title, authors, venue or year).
    user.find(marker="doc-papers-search").type("long ecir")
    await user.should_see(marker=f"doc-paper-{ids['b']}")
    await user.should_not_see(marker=f"doc-paper-{ids['a']}")
    user.find(marker="doc-papers-search").clear()
    await user.should_see(marker=f"doc-paper-{ids['a']}")
    # A paper's details, as in the publications panel; its notes taken there.
    user.find(marker=f"doc-show-{ids['a']}").click()
    await user.should_see(marker="pdf-details")
    await user.should_see(marker="tab-notes")
    user.find(marker="pdf-side-back").click()
    await user.should_see(marker="doc-papers")
    # A wrong match, rejected.
    user.find(marker=f"doc-show-{ids['d']}").click()
    user.find(marker="doc-reject").click()
    await user.should_not_see(marker=f"doc-paper-{ids['d']}")
    # The document's own note.
    await user.should_see(marker="doc-note")
    user.find(marker="doc-note").elements.pop().value = "Strong candidate"
    await note_saved(user, "doc-note")
    await user.should_see("Strong candidate")
    assert documents.info(doc).note == "Strong candidate"
    # Citing a paper: those of the document first (the latest first).
    user.find(marker="doc-note-cite").click()
    await user.should_see(marker="doc-note-cite-paper")
    options = list(user.find(marker="doc-note-cite-paper").elements.pop().options)
    assert options[:3] == ["anon2023sparse", "anon2022neural", "anon2021deep"]
    # The preview numbers the citations.
    user.find(marker="doc-note").elements.pop().value = "See [@anon2022neural]."
    await user.should_see("See [1].")


async def test_bookmarks_and_selection(user: User, monkeypatch, tmp_path):
    _, period, ids = _person()
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    selection = {
        "text": "[9] J. Doe. Neural retrieval models for long documents. ECIR 2022.",
        "p": 3,
        "rects": [[72, 400, 300, 410]],
    }
    user.javascript_rules[re.compile(r"vrPdf\.location\(\)")] = lambda _: {
        "p": 2,
        "y": 640.0,
        "text": "Publications",
    }
    user.javascript_rules[re.compile(r"vrPdf\.selection\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"doc-paper-{ids['a']}")
    user.find(marker="pdf-bookmark").click()
    await user.should_see(marker="bookmark-0")
    assert documents.bookmarks("doc", doc) == [{"name": "Publications", "p": 2, "y": 640.0}]
    user.find(marker="bookmark-rename-0").click()
    user.find(marker="bookmark-name").clear().type("Papers")
    user.find(marker="bookmark-rename-ok").click()
    await user.should_see("Papers")
    # The paper of a selection: linked there (and its reference's label cited).
    user.find(marker="pdf-find").click()
    await user.should_see(marker=f"find-link-{ids['b']}")
    user.find(marker=f"find-link-{ids['b']}").click()
    await user.should_see("Linked")
    [link] = documents.links_of(doc)
    assert link["pub"] == ids["b"] and link["p"] == 3
    # Nothing matches: search HAL.
    selection["text"] = "A paper nobody wrote"
    user.find(marker="pdf-find").click()
    await user.should_see(marker="find-none")
    await user.should_see(marker="find-search")


async def test_paper_pdf_bookmarks(user: User, monkeypatch, tmp_path):
    _, _, ids = _person()
    _viewer(monkeypatch, tmp_path)
    pdfs.save(ids["a"], PDF, None)
    user.javascript_rules[re.compile(r"vrPdf\.location\(\)")] = lambda _: {
        "p": 4,
        "y": None,
        "text": "",
    }
    await user.open(f"/pdf/{ids['a']}")
    await user.should_see(marker="paper-note")
    user.find(marker="pdf-bookmark").click()
    await user.should_see(marker="bookmark-0")
    assert documents.bookmarks("pub", ids["a"]) == [{"name": "Page 4", "p": 4, "y": None}]
    await user.should_see("Page 4")


async def test_quote_into_the_notes(user: User, monkeypatch, tmp_path):
    assert quote("Ranking\n models are  deep", "p. 2") == "> Ranking models are deep (p. 2)"
    assert quote("Selected on the page") == "> Selected on the page"
    _, period, ids = _person()
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    selection: dict = {"text": "Our ranking model\nbeats sparse ones", "p": 1}
    user.javascript_rules[re.compile(r"vrPdf\.quoted\(\)")] = lambda _: selection
    inserted = []  # (at the cursor, in the browser)
    monkeypatch.setattr(MarkdownEditor, "insert_block", lambda self, text: inserted.append(text))
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="doc-note")
    assert len(user.find(marker="note-quote").elements) == 2  # (the folder's notes, the note's)
    user.find(marker="note-quote").click()  # (the folder's first: names its source)
    for _i in range(50):  # (its JavaScript: once the selection is back)
        if inserted:
            break
        await asyncio.sleep(0.02)
    assert inserted == [
        f"> Our ranking model beats sparse ones ([Application, p. 1](/doc/{doc}?page=1))"
    ]
    # Nothing selected: says so.
    selection = {}
    user.find(marker="note-quote").click()
    await user.should_see("Select the text to quote")
    # On a paper's PDF: in its notes (the period's too, and the folder's).
    pdfs.save(ids["a"], PDF, None)
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="period-note")
    assert len(user.find(marker="note-quote").elements) == 3


async def test_folder_notes(user: User, monkeypatch, tmp_path):
    _, period, ids = _person()
    _viewer(monkeypatch, tmp_path)
    pdfs.save(ids["a"], PDF, None)
    folder = folders.folder_of_period(period)[0]
    selection = {"text": "Deep models rank", "p": 3}
    user.javascript_rules[re.compile(r"vrPdf\.quoted\(\)")] = lambda _: selection
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert_block", lambda self, text: inserted.append(text))
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="folder-note")
    user.find(marker="folder-note").elements.pop().value = "Shortlist: **two** papers"
    await note_saved(user, "folder-note")
    assert folders.notes_of(folder) == "Shortlist: **two** papers"
    user.find(marker="note-quote").click()
    for _i in range(50):
        if inserted:
            break
        await asyncio.sleep(0.02)
    assert inserted == [
        f"> Deep models rank ([Deep ranking models for search, p. 3](/pdf/{ids['a']}?period={period}&page=3))"
    ]
    # Another window on the same folder shows the saved text.
    await user.open(f"/doc/{documents.add(period, 'CV.pdf', PDF)}")
    await user.should_see(marker="folder-note")
    [other] = user.find(marker="folder-note").elements
    assert other.value == "Shortlist: **two** papers"
    # Without a folder: no folder notes.
    pid = make_person("Zed Alone")
    add_source(pid, "dblp", "z/1", [pub("z", "Alone paper", 2020, "ECIR")])
    with session_scope() as s:
        zid = s.scalar(select(Publication.id).where(Publication.person_id == pid))
    pdfs.save(zid, PDF, None)
    await user.open(f"/pdf/{zid}")
    await user.should_see(marker="paper-note")
    await user.should_not_see(marker="folder-note")


async def test_last_place(user: User, monkeypatch, tmp_path):
    _, period, ids = _person()
    _viewer(monkeypatch, tmp_path)
    pdfs.save(ids["a"], PDF, None)
    assert documents.last_place("pub", ids["a"]) is None
    documents.save_last_place("pub", ids["a"], {"p": "3", "zoom": "page-width", "top": 512.4})
    documents.save_last_place("pub", ids["a"], {"p": 2, "zoom": "1);alert(1"})  # (ignored)
    documents.save_last_place("pub", ids["a"], {"zoom": "auto"})  # (ignored: no page)
    assert documents.last_place("pub", ids["a"]) == {
        "p": 3,
        "zoom": "page-width",
        "left": 0,
        "top": 512,
    }
    # Opened again there, unless at a page asked for.
    await user.open(f"/pdf/{ids['a']}")
    await user.should_see(marker="pdf-frame")
    src = user.find(marker="pdf-frame").elements.pop().props["src"]
    assert src.endswith("#page=3&zoom=page-width,0,512")
    await user.open(f"/pdf/{ids['a']}?page=7")
    await user.should_see(marker="pdf-frame")
    assert user.find(marker="pdf-frame").elements.pop().props["src"].endswith("#page=7")
    # Forgotten with the PDF (or the document).
    pdfs.remove(ids["a"])
    assert documents.last_place("pub", ids["a"]) is None
    doc = documents.add(period, "Application.pdf", PDF)
    documents.save_last_place("doc", doc, {"p": 2, "zoom": "125.5", "left": -3, "top": 700})
    assert documents.place_hash(documents.last_place("doc", doc)) == "#page=2&zoom=125.5,-3,700"
    documents.remove(doc)
    assert documents.last_place("doc", doc) is None


async def test_author_year_citations():
    pid = make_person("Lena Martí Vidal")
    add_source(
        pid,
        "dblp",
        "d/2",
        [
            pub(
                "x",
                "Let's play mono-poly: BERT can reveal words' polysemy level",
                2021,
                authors=["Lena Martí Vidal", "Clara Montiverdi"],
            ),
            pub(
                "y",
                "BERT knows Punta Cana is not just beautiful, it's gorgeous",
                2020,
                authors=["Lena Martí Vidal", "Clara Montiverdi"],
            ),
            pub(
                "z",
                "Probing pretrained language models for lexical semantics",
                2020,
                authors=["Petar Horvać", "Marco Bellini"],
            ),
        ],
    )
    rows = await pubview.load_stats(pid)
    ids = {r.title[:4]: r.id for r in rows}
    lines = [
        _line(1, 700, "Words are polysemous (Marti Vidal and Montiverdi, 2021, 2020; Horvać"),
        _line(1, 685, "et al., 2020; Ferrante et al., 2019). In Horvać et al. (2020b) also."),
        _line(2, 700, "References"),
        _line(2, 680, "Lena Martí Vidal and Clara Montiverdi. 2021. Let's play mono-poly:"),
        _line(2, 665, "BERT can reveal words' polysemy level. TACL."),
        _line(2, 650, "Lena Martí Vidal and Clara Montiverdi. 2020. BERT knows Punta Cana"),
        _line(2, 635, "is not just beautiful, it's gorgeous. CoNLL."),
        _line(2, 620, "Petar Horvać et al. 2020a. Probing pretrained language models for lexical"),
        _line(2, 605, "semantics. EMNLP."),
    ]
    cites = [(m.pub_id, m.label) for m in documents.find_papers(lines, rows) if m.kind == "cite"]
    assert cites == [
        (ids["Let'"], "Marti Vidal and Montiverdi, 2021"),
        (ids["BERT"], "Marti Vidal and Montiverdi, 2020"),
        (ids["Prob"], "Horvać et al., 2020"),  # across lines; "2020b" is not "2020a"
    ]


def test_categories_and_markdown():
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    teaching = categories.add(folder, "Teaching")
    projects = categories.add(folder, "Projects", research)
    categories.update(projects, "Projects", 2020, 2025)
    categories.move(teaching, -1)
    assert [(n.path, n.depth) for n in categories.tree(folder)] == [
        ("Teaching", 0),
        ("Research", 0),
        ("Research › Projects", 1),
    ]
    doc = documents.add(period, "Application.pdf", PDF)
    led = categories.add_excerpt(
        projects, period, "Led the  ANR\nproject X.", 2, [], document_id=doc
    )
    categories.add_excerpt(teaching, period, "Taught IR.", 1, [], document_id=doc)
    again = categories.add_excerpt(teaching, period, "Taught  IR.", 1, [], document_id=doc)
    assert len(categories.excerpts(period)) == 2 and again  # (filed there already)
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught IR. %% Application, p. 1 %%\n\n"
        "## Research\n\n### Projects (2020–2025)\n\n"
        "- Led the ANR project X. %% Application, p. 2 %%\n"
    )
    # Its influence ("rayonnement") ones: in their category, and in a last section too.
    [taught] = [e.id for e in categories.excerpts(period) if e.text == "Taught IR."]
    chaired = categories.add_excerpt(
        teaching, period, "Chaired a workshop.", 3, [], document_id=doc
    )
    categories.update_excerpt(chaired, start=2022, end=2022, influence=True)
    categories.update_excerpt(taught, influence=True)
    categories.update_excerpt(led, influence=True)
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught IR. %% Application, p. 1 %%\n"
        "- Chaired a workshop. %% Application, p. 3 %% — 2022\n\n"
        "## Research\n\n### Projects (2020–2025)\n\n"
        "- Led the ANR project X. %% Application, p. 2 %%\n\n"
        "## Rayonnement\n\n"
        "- **Teaching**\n"
        "  - Taught IR. %% Application, p. 1 %%\n"
        "  - Chaired a workshop. %% Application, p. 3 %% — 2022\n"
        "- **Research › Projects**\n"
        "  - Led the ANR project X. %% Application, p. 2 %%\n"
    )
    categories.remove_excerpt(chaired)
    categories.update_excerpt(taught)
    categories.update_excerpt(led)
    other = folders.save_folder(None, "Hiring 2027")
    assert categories.copy_tree(folder, other) == 3
    assert [n.path for n in categories.tree(other)][2] == "Research › Projects"
    # Deleted (with its subcategory) once its excerpts are moved elsewhere.
    assert not categories.delete(research) and categories.excerpt_count(research) == 1
    assert categories.move_excerpts(research, projects) == 0  # (within it)
    assert categories.move_excerpts(research, teaching) == 1
    assert categories.delete(research)
    assert [e.text for e in categories.excerpts(period)] == ["Taught IR.", "Led the ANR project X."]
    categories.remove_excerpt(led)
    # Its text, years and "influence" flag.
    [e] = categories.excerpts(period)
    categories.update_excerpt(e.id, text="Taught\n IR (M2).", start=2021, influence=True)
    [e] = categories.excerpts(period)
    assert (e.text, e.years, e.influence) == ("Taught IR (M2).", "since 2021", True)
    assert e.original == "Taught IR."  # (as selected)
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught IR (M2). %% Application, p. 1 %% — since 2021\n\n"
        "## Rayonnement\n\n- **Teaching**\n  - Taught IR (M2). %% Application, p. 1 %% — since 2021\n"
    )
    # Merged: grouped (each its own quote, in the order of their PDFs; the target's years…).
    later = categories.add_excerpt(
        teaching, period, "Gave a course.", 2, [[1, 2, 3, 4]], document_id=doc, start=2019, end=2020
    )
    assert categories.merge_excerpts(e.id, later) is None
    [g] = categories.excerpts(period, grouped=True)
    assert g.id == later and [x.id for x in g.parts] == [e.id, later]
    other_doc = documents.add(period, "Other.pdf", PDF)
    x = categories.add_excerpt(teaching, period, "Elsewhere.", 1, [], document_id=other_doc)
    assert categories.merge_excerpts(x, e.id) is None  # (onto a member: its group)
    assert categories.merge_excerpts(e.id, x)  # (together already)
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught IR (M2). %% Application, p. 1 %% […] Gave a course. %% p. 2 %% […] "
        "Elsewhere. %% Other, p. 1 %% — 2019–2020\n"
    )
    # Merged as a reference (only its place cited), then the text of the group edited.
    categories.set_ref_only(later, True)
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught IR (M2). %% Application, p. 1; p. 2 %% […] "
        "Elsewhere. %% Other, p. 1 %% — 2019–2020\n"
    )
    assert [t["page"] for t in categories.tints(period, doc, {})] == [1, 2]  # (tinted still)
    assert categories.cited_documents(period) == [(doc, "Application"), (other_doc, "Other")]
    documents.rename(doc, "Dossier")
    assert "Dossier, p. 1; p. 2 %%" in categories.markdown(folder, period)
    documents.rename(doc, "Application")
    categories.set_group_text(later, "Taught  and gave\ncourses.")
    assert [t["page"] for t in categories.tints(period, doc, {})] == [1, 2]
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- Taught and gave courses. %% Application, p. 1; p. 2; Other, p. 1 %%"
        " — 2019–2020\n"
    )
    # Might be already an excerpt: similar words, or found by a query.
    assert [v.id for v in categories.similar_excerpts(period, "Gave courses, at university")] == [
        later
    ]
    assert categories.similar_excerpts(period, "Wrote a book on ranking") == []
    assert [v.id for v in categories.similar_excerpts(period, "Wrote", "elsewhere")] == [later]
    categories.set_group_text(later, " ")
    assert categories.excerpts(period, grouped=True)[0].group_text is None
    categories.update_excerpt(later, start=2019, end=2020, colour="#4caf50")
    assert categories.excerpts(period, grouped=True)[0].colour == "#4caf50"
    # Ordered within the category (a group with it), taken out of a group, the lead removed.
    y = categories.add_excerpt(teaching, period, "Supervised.", 3, [], document_id=doc)
    categories.place_excerpt(y, e.id, "before")
    assert [v.id for v in categories.excerpts(period, grouped=True)] == [y, later]
    categories.split_excerpt(x)
    assert [v.id for v in categories.excerpts(period, grouped=True)] == [y, later, x]
    categories.remove_excerpt(later)
    lead = next(v for v in categories.excerpts(period, grouped=True) if v.id == e.id)
    assert (lead.colour, lead.years, lead.members) == ("#4caf50", "2019–2020", [])


async def test_excerpt_from_an_area(user: User, monkeypatch, tmp_path):
    """An area (a rectangle) of a page, selected: its text filed as an excerpt, the
    rectangle as its place (tinted as a text's)."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    figures = categories.add(folder, "Figures")
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "", "p": 2, "rects": [[60.5, 400, 320, 520.2, 2]], "area": True}
    user.javascript_rules[re.compile(r"vrPdf\.selection\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-area")
    # Without text in it: nothing to file.
    user.find(marker="pdf-excerpt").click()
    await user.should_see("Select the text to file (or an area with text, or click a highlight)")
    assert categories.excerpts(period) == []
    selection["text"] = "Figure 2: recall of the ranking model"
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{figures}")
    user.find(marker=f"pick-category-{figures}").click()
    await user.should_see("Added to Figures")
    [e] = categories.excerpts(period)
    assert (e.text, e.page, e.rects) == (
        "Figure 2: recall of the ranking model",
        2,
        [[60.5, 400, 320, 520.2, 2]],
    )
    [tint] = categories.tints(period, doc, {})
    assert (tint["page"], tint["rects"]) == (2, [[60.5, 400, 320, 520.2, 2]])


async def test_excerpts_in_the_viewer(user: User, monkeypatch, tmp_path):
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    user.javascript_rules[re.compile(r"vrPdf\.selection\(\)")] = lambda _: {
        "text": "Our ranking model beats sparse ones",
        "p": 1,
        "rects": [[72, 680, 300, 690]],
    }
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{research}")
    user.find(marker="excerpt-start").elements.pop().value = 2020  # (its properties)
    user.find(marker="excerpt-influence").click()
    user.find(marker="excerpt-colour-2196f3").click()
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    [e] = categories.excerpts(period)
    assert e.category_id == research and e.page == 1 and e.document_id == doc
    assert (e.start_year, e.end_year, e.influence, e.colour) == (2020, None, True, "#2196f3")
    await user.should_see(marker=f"excerpt-{e.id}")
    # A new category, typed in the picker: created (the picker stays open), then picked.
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-search")
    user.find(marker="category-search").type("Impact").trigger("keydown.enter")
    await user.should_see("Category “Impact” created")
    impact = categories.tree(folder)[1].id
    assert [n.name for n in categories.tree(folder)] == ["Research", "Impact"]
    await user.should_see(marker=f"pick-category-{impact}")
    user.find(marker=f"pick-category-{impact}").click()
    await user.should_see("Added to Impact")
    # Edited (text, years), then moved with the picker.
    user.find(marker=f"excerpt-edit-{e.id}").click()
    await user.should_see(marker=f"excerpt-original-{e.id}")
    user.find(marker="excerpt-edit-text").clear().type("Our model beats sparse ones")
    user.find(marker="excerpt-start").elements.pop().value = 2022
    user.find(marker="excerpt-end").elements.pop().value = 2024
    user.find(marker="excerpt-edit-save").click()
    await user.should_see(marker=f"excerpt-influence-{e.id}")
    await user.should_see("2022–2024")
    [e2] = [x for x in categories.excerpts(period) if x.id == e.id]
    assert (e2.text, e2.start_year, e2.end_year, e2.influence) == (
        "Our model beats sparse ones",
        2022,
        2024,
        True,
    )
    user.find(marker=f"excerpt-move-{e.id}").click()
    await user.should_see("(its category)")
    user.find(marker="category-search").type("imp").trigger("keydown.enter")
    await user.should_see("Moved to Impact")
    assert next(x for x in categories.excerpts(period) if x.id == e.id).category_id == impact
    # Merged: its merge icon, then a click on the other one (confirmed).
    [other] = [x for x in categories.excerpts(period) if x.id != e.id]
    user.find(marker=f"excerpt-merge-{e.id}").click()
    await user.should_see(marker="excerpt-merge-cancel")
    user.find(content="Our ranking model beats sparse ones").click()
    await user.should_see(marker="excerpt-merge-confirm")
    user.find(marker="excerpt-merge-confirm").click()
    await user.should_not_see(marker=f"excerpt-{e.id}")  # (now within the other's block)
    await user.should_see(marker=f"excerpt-part-{e.id}")
    [g] = categories.excerpts(period, grouped=True)
    assert g.id == other.id and [m.id for m in g.members] == [e.id]
    # Removed (from the group), once confirmed.
    user.find(marker=f"excerpt-remove-{e.id}").click()
    await user.should_see(marker="excerpt-remove-confirm")
    assert any(x.id == e.id for x in categories.excerpts(period))
    user.find(marker="excerpt-remove-confirm").click()
    await user.should_not_see(marker=f"excerpt-part-{e.id}")
    assert all(x.id != e.id for x in categories.excerpts(period))
    # Selected again (elsewhere): might be already an excerpt, merged with it as a reference.
    user.javascript_rules[re.compile(r"vrPdf\.selection\(\)")] = lambda _: {
        "text": "Our ranking model: it beats sparse ones",
        "p": 2,
        "rects": [[72, 600, 300, 610]],
    }
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"excerpt-similar-{other.id}")
    await user.should_see("Might be already an excerpt (1): merge with it?")
    user.find(marker="excerpt-similar-mode").elements.pop().value = True
    user.find(marker=f"excerpt-similar-merge-{other.id}").click()
    await user.should_see("Merged with the excerpt")
    [g] = categories.excerpts(period, grouped=True)
    [m] = g.members
    assert g.id == other.id and m.ref_only and m.page == 2
    await user.should_see(marker=f"excerpt-cite-{m.id}")
    # The text of the group, edited.
    user.find(marker=f"excerpt-edit-{other.id}").click()
    await user.should_see(marker=f"excerpt-original-{m.id}")  # (each, as selected)
    await user.should_see("cited by its place only")
    user.find(marker=f"excerpt-original-use-{m.id}").click()  # (added to the text)
    assert (
        user.find(marker="excerpt-edit-text")
        .elements.pop()
        .value.endswith(" […] Our ranking model: it beats sparse ones")
    )
    user.find(marker="excerpt-edit-text").clear().type("Our model beats sparse ones (twice)")
    user.find(marker="excerpt-edit-save").click()
    await user.should_see(marker=f"excerpt-group-text-{other.id}")
    assert categories.excerpts(period, grouped=True)[0].group_text == (
        "Our model beats sparse ones (twice)"
    )
    # In the report: inserted as Markdown.
    await user.open(f"/report/{period}")
    await user.should_see(marker="report-excerpts")


async def test_categories_editor(user: User, monkeypatch, tmp_path):
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    _viewer(monkeypatch, tmp_path)
    doc = documents.add(period, "Application.pdf", PDF)
    await user.open(f"/doc/{doc}")
    # (also the folder page's "categories" button)
    user.find(marker="categories-edit").click()
    await user.should_see(marker="category-new")
    for name in ("Research", "Teaching"):
        user.find(marker="category-new").type(name).trigger("keydown.enter")
        await user.should_see(marker="category-name-" + str(len(categories.tree(folder))))
        user.find(marker="category-new").clear()
    research, teaching = (n.id for n in categories.tree(folder))
    user.find(marker=f"category-sub-{research}").click()
    await user.should_see(marker="category-name-3")
    # Dragged: Teaching inside Research, then before it.
    user.find(marker=f"category-{research}").trigger("drop", {"id": teaching, "where": "inside"})
    await user.should_see(marker=f"category-{teaching}")
    assert [n.path for n in categories.tree(folder)] == [
        "Research",
        "Research › New",
        "Research › Teaching",
    ]
    user.find(marker=f"category-{research}").trigger("drop", {"id": teaching, "where": "before"})
    user.find(marker=f"category-{teaching}").trigger("drop", {"id": research, "where": "inside"})
    assert [n.path for n in categories.tree(folder)] == [
        "Teaching",
        "Teaching › Research",
        "Teaching › Research › New",
    ]
    user.find(marker=f"category-{research}").trigger("drop", {"id": teaching, "where": "inside"})
    await user.should_see("A category cannot go within itself")
    user.find(marker="category-drop-end").trigger("drop", {"id": research})
    assert [n.path for n in categories.tree(folder)] == ["Teaching", "Research", "Research › New"]
    # Not deleted with excerpts there (or below): moved first.
    new = categories.tree(folder)[2].id
    categories.add_excerpt(new, period, "Filed below.", 1, [], document_id=doc)
    user.find(marker=f"category-delete-{research}").click()
    await user.should_see(marker="category-delete-blocked")
    user.find(marker="category-delete-ok").click()
    await user.should_see("Choose where to move them")
    user.find(marker="category-delete-target").elements.pop().value = teaching
    user.find(marker="category-delete-ok").click()
    await user.should_not_see(marker=f"category-{research}")
    assert [n.path for n in categories.tree(folder)] == ["Teaching"]
    assert [e.category_id for e in categories.excerpts(period)] == [teaching]
    # The documents named (as cited in the copied excerpts).
    user.find(marker="excerpts-sources").click()
    await user.should_see(marker=f"doc-name-{doc}")
    user.find(marker=f"doc-name-{doc}").clear().type("Dossier")
    user.find(marker="doc-names-ok").click()
    assert categories.cited_documents(period) == [(doc, "Dossier")]


async def test_author_year_citations_by_authors():
    pid = make_person("Clara Montiverdi")
    papers = {
        "limsi": ("LIMSI: Translations as source of indirect supervision", 2015, ["Tao"]),
        "meteor": ("Alignment-based sense selection in METEOR", 2015, ["Durand", "Montiverdi"]),
        "smt": (
            "WSD for n-best reranking and local language modeling in SMT",
            2012,
            ["Roux", "Lebrun"],
        ),
        "clust": ("An algorithm for cross-lingual sense clustering", 2010, ["Li"]),
        "solo": ("Vector space models of word meaning in translation", 2012, []),
    }
    add_source(
        pid,
        "dblp",
        "d/3",
        [
            pub(
                k,
                t,
                y,
                authors=co if "Durand" in co else ["Clara Montiverdi", *co],
            )
            for k, (t, y, co) in papers.items()
        ],
    )
    rows = await pubview.load_stats(pid)
    ids = {k: next(r.id for r in rows if r.title == t) for k, (t, _, _) in papers.items()}
    lines = [
        _line(1, 700, "Senses are found in translations (Montiverdi and Tao, 2015; Montiverdi"),
        _line(1, 685, "et al., 2012; Montiverdi and Li, 2010; Durand"),
        _line(1, 670, "and Montiverdi, 2015), or not (Montiverdi, 2015; Durand and"),
        _line(1, 655, "Montiverdi, 2015; Montiverdi et al., 2012; Monti-"),
        _line(1, 640, "verdi, 2012)."),
        _line(2, 700, "References"),
        _line(2, 680, "Clara Montiverdi and Wei Tao. 2015. LIMSI: Translations as source of"),
        _line(2, 665, "indirect supervision. In SemEval."),
        _line(2, 650, "Clara Montiverdi, Louis Roux and Anne Lebrun. 2012. WSD for n-best"),
        _line(2, 635, "reranking and local language modeling in SMT. In SSST."),
        _line(2, 620, "Clara Montiverdi and Jun Li. 2010. An algorithm for cross-lingual"),
        _line(2, 605, "sense clustering. In WMT."),
        _line(2, 590, "Paul Durand and Clara Montiverdi. 2015. Alignment-based sense"),
        _line(2, 575, "selection in METEOR. In WMT."),
        _line(2, 560, "Clara Montiverdi. 2012. Vector space models of word meaning in"),
        _line(2, 545, "translation. In CICLing."),
    ]
    found = [m for m in documents.find_papers(lines, rows) if m.kind == "cite"]
    assert [(m.pub_id, m.label) for m in found] == [
        (ids["limsi"], "Montiverdi and Tao, 2015"),
        # Across lines ("Montiverdi" then "et al."): of more than one author.
        (ids["smt"], "Montiverdi et al., 2012"),
        (ids["clust"], "Montiverdi and Li, 2010"),
        # "Durand" then "and Montiverdi": not LIMSI (by Montiverdi and Tao).
        (ids["meteor"], "Durand and Montiverdi, 2015"),
        # "Montiverdi, 2015" (alone): none of the papers.
        (ids["meteor"], "Durand and Montiverdi, 2015"),
        (ids["smt"], "Montiverdi et al., 2012"),
        (ids["solo"], "Montiverdi, 2012"),  # (a broken word)
    ]
    assert [len(m.rects) for m in found] == [1, 2, 1, 2, 2, 1, 2]  # (one per line)


async def test_author_year_citations_suffix_and_sections():
    pid = make_person("Hao Quan")
    lyu = ["Hao Quan", "Ada Okafor", "Tom Brenner"]
    add_source(
        pid,
        "dblp",
        "d/4",
        [
            pub("a", "Towards faithful model explanation in NLP: a survey", 2023, authors=lyu),
            pub("b", "Faithful chain-of-thought reasoning", 2023, authors=lyu),
            pub("c", "Neural lexical semantics in the main paper", 2021, authors=lyu[:1]),
            pub("d", "Symbolic lexical semantics in the appendix", 2021, authors=lyu[:1]),
        ],
    )
    rows = await pubview.load_stats(pid)
    ids = {
        k: next(r.id for r in rows if r.title.startswith(t))
        for k, t in [("a", "Towards"), ("b", "Faithful"), ("c", "Neural"), ("d", "Symbolic")]
    }
    lines = [
        _line(
            1, 700, "Explanations (Quan et al., 2023a) and chains (Quan et al., 2023b; Quan, 2021)."
        ),
        _line(2, 700, "References"),
        _line(2, 680, "Quan, H., Montiverdi, C., and Hart-Ellis, R. (2023a). Towards faithful"),
        _line(2, 665, "model explanation in NLP: a survey. Computational Linguistics."),
        _line(
            2,
            650,
            "Quan, H., Okafor, A., Brenner, T., Moreau, L., Lindqvist, D., Sato, E., Montiverdi, C., and",
        ),
        _line(
            2,
            635,
            "Hart-Ellis, R. (2023b). Faithful chain-of-thought reasoning. In Proceedings",
        ),
        _line(2, 620, "of IJCNL/AACL, pages 305–329, Nusa Dua, Bali."),
        _line(2, 605, "Quan, H. (2021). Neural lexical semantics in the main paper. ACL."),
        _line(3, 700, "A Appendix"),
        _line(3, 685, "As before (Quan, 2021)."),
        _line(4, 700, "References"),
        _line(4, 680, "Quan, H. (2021). Symbolic lexical semantics in the appendix. EMNLP."),
    ]
    cites = [(m.pub_id, m.label) for m in documents.find_papers(lines, rows) if m.kind == "cite"]
    assert cites == [
        (ids["a"], "Quan et al., 2023a"),
        (ids["b"], "Quan et al., 2023b"),  # (the "2023a" just before is another reference's)
        (ids["c"], "Quan, 2021"),  # (the closest reference list after)
        (ids["d"], "Quan, 2021"),
    ]


async def test_numbered_citations_two_lists():
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/5",
        [
            pub("a", "Neural ranking models for the main paper", 2020),
            pub("b", "Sparse retrieval models for the appendix", 2021),
        ],
    )
    rows = await pubview.load_stats(pid)
    ids = {r.title.split()[0]: r.id for r in rows}
    lines = [
        _line(1, 700, "As shown in [1], ranking works."),
        _line(2, 700, "References"),
        _line(2, 680, "[1] J. Doe. Neural ranking models for the main paper. 2020."),
        _line(3, 700, "Appendix: as in [1]."),
        _line(4, 700, "References"),
        _line(4, 680, "[1] J. Doe. Sparse retrieval models for the appendix. 2021."),
    ]
    cites = [m.pub_id for m in documents.find_papers(lines, rows) if m.kind == "cite"]
    assert cites == [ids["Neural"], ids["Sparse"]]


async def test_folder_notes_sync(user: User):
    from sci_report_analyzer.ui import folder_notes

    folder = folders.save_folder(None, "Sync")
    seen = []

    class Fake:
        def __init__(self, value):
            self.value = value
            self.editor = type("E", (), {"is_deleted": False})()

        def is_dirty(self):
            return False

        def adopt(self, text):
            seen.append(text)

    clean, saver, same = Fake("old"), Fake("old"), Fake("new")
    folder_notes._editors()[folder] = {clean, saver, same}  # (as the registry's sets)
    folder_notes.saved(folder, "new", saver)
    assert seen == ["new"]  # (the other one only: not the saver, not the one up to date)
