"""Documents of a person within a period / folder: storage, the papers found in them
(titles, ids, citations, links by hand), bookmarks, and their pages."""

import asyncio
import re

import pytest
from helpers import PDF, add_source, make_person, note_status, pub
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import (
    annotations,
    copy_filters,
    documents,
    folders,
    pdfs,
    pubview,
    reports,
)
from sci_report_analyzer.db.models import Publication, SectionText
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ui import copying, documents_page
from sci_report_analyzer.ui.mdedit import MarkdownEditor, inline_quote, quote

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

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


async def test_documents_tab(user: User):
    pid, period, _ = _person()
    await user.open(f"/person/{pid}?tab=report")
    await user.should_see(marker="folder-note")  # (the report, the documents in a menu)
    user.find(marker=f"documents-{period}").click()
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
    await user.should_see("Documents (1)")
    [doc] = documents.of_person(pid)[period]
    user.find(marker=f"document-more-{doc.id}").click()  # (its ⋮: rename, delete…)
    user.find(marker=f"document-rename-{doc.id}").click()
    user.find(marker="document-name").clear().type("CV")
    user.find(marker="document-rename-ok").click()
    await user.should_see("CV")
    user.find(marker=f"document-more-{doc.id}").click()
    user.find(marker=f"document-delete-{doc.id}").click()
    user.find(marker="document-delete-ok").click()
    await user.should_see("No document")
    assert not documents.of_person(pid)


async def test_documents_tab_without_period(user: User):
    pid = make_person("John Roe")
    await user.open(f"/person/{pid}?tab=report")
    await user.should_see(marker="documents-no-period")


async def test_document_page(user: User, monkeypatch, fake_viewer):
    pid, period, ids = _person()
    doc = documents.add(period, "Application.pdf", PDF)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-frame")
    await user.should_not_see(marker="document-close")
    # Opened from the person's Report tab: closed back to it.
    await user.open(f"/person/{pid}?tab=report")
    user.find(marker=f"documents-{period}").click()
    user.find(marker=f"document-open-{doc}").click()
    await user.should_see(marker="document-close")
    # (its person's header there: back to their tabs)
    await user.should_see(marker="tab-report")
    # Left open there: the Report tab goes back to it, until it is closed.
    assert documents_page.open_document(period) == doc
    user.find(marker="document-close").click()
    await user.should_see(marker="notes-side")
    await user.should_see(marker="folder-note")
    assert documents_page.open_document(period) is None
    await user.open(f"/doc/{doc}")
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
    # ... and restored (a wrong rejection).
    await user.should_see(marker="doc-rejected")
    user.find(marker=f"doc-restore-{ids['d']}").click()
    await user.should_see(marker=f"doc-paper-{ids['d']}")
    await user.should_not_see(marker="doc-rejected")
    assert not documents.rejected(doc)
    # The document's own note.
    await user.should_see(marker="doc-note")
    user.find(marker="doc-note").elements.pop().value = "Strong candidate"
    await note_status(user, "doc-note")
    await user.should_see("Strong candidate")
    assert documents.info(doc).note == "Strong candidate"
    # Citing a paper: those of the document first (the latest first).
    user.find(marker="doc-note-cite").click()
    await user.should_see(marker="doc-note-cite-paper")
    options = list(user.find(marker="doc-note-cite-paper").elements.pop().options)
    assert options[:3] == ["anon2023sparse", "anon2022neural", "anon2021deep"]
    # The preview numbers the citations.
    user.find(marker="doc-note").elements.pop().value = "See [@anon2022neural]. %% [CV, p. 2] %%"
    await user.should_see("See [1].")
    [preview] = [m for m in user.find(kind=ui.markdown).elements if "See [1]." in m.content]
    assert "CV, p. 2" not in preview.content  # (the comments: not in the preview)
    # The paper cited at the cursor (or ⌘/Ctrl-clicked): its details, in a new window.
    opened = []
    monkeypatch.setattr(ui.navigate, "to", lambda url, new_tab=False: opened.append((url, new_tab)))
    user.javascript_rules[re.compile(r".*vrCite\.at\(v, v\.state", re.S)] = lambda _: (
        "anon2022neural"
    )
    user.find(marker="doc-note-see-cited").click()
    for _i in range(50):
        if opened:
            break
        await asyncio.sleep(0.02)
    assert opened == [(f"/person/{pid}/{period}?pub={ids['b']}", True)]
    layout = user.client.layout
    clicked = [i for i, ls in layout._event_listeners.items() if ls.type == "vrCiteOpen"]
    [note] = user.find(marker="doc-note").elements
    for editor, key in ((-1, "anon2021deep"), (note.id, "anon2023sparse"), (note.id, "nokey")):
        for listener in clicked:
            layout._handle_event({"listener_id": listener, "args": {"id": editor, "key": key}})
    assert opened[1:] == [(f"/person/{pid}/{period}?pub={ids['c']}", True)]  # (its editor's)
    await user.should_see("No paper cited as @nokey")
    # Shift-click on a citation in the PDF: cited in the notes last used, at the cursor.
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert", lambda self, text: inserted.append(text))
    layout = user.client.layout
    [(listener, _ev)] = [
        (i, ls) for i, ls in layout._event_listeners.items() if ls.type == "vrDocPaper"
    ]
    layout._handle_event({"listener_id": listener, "args": {"i": 0, "cite": True}})
    await user.should_see(marker="doc-note")
    assert len(inserted) == 1 and re.fullmatch(r"\[@anon\d{4}\w+\]", inserted[0])
    layout._handle_event({"listener_id": listener, "args": {"i": 0}})  # (plain click: details)
    await user.should_see(marker="pdf-details")
    assert len(inserted) == 1


async def test_bookmarks_and_selection(user: User, fake_viewer):
    _, period, ids = _person()
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
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
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


async def test_paper_pdf_bookmarks(user: User, fake_viewer):
    _, _, ids = _person()
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


async def test_quote_into_the_notes(user: User, monkeypatch, fake_viewer):
    assert quote("Ranking\n models are  deep", "p. 2") == "> Ranking models are deep (p. 2)"
    assert quote("Selected on the page") == "> Selected on the page"
    assert inline_quote("Ranking\n models", "[CV, p. 2](/doc/1?page=2)") == (
        '"Ranking models" %% [CV, p. 2](/doc/1?page=2) %%'
    )
    _, period, ids = _person()
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
    # Inline, at the cursor: in quotation marks, linked back in an Obsidian comment.
    monkeypatch.setattr(MarkdownEditor, "insert_inline", lambda self, text: inserted.append(text))
    inserted.clear()
    button = user.find(marker="note-quote").elements.pop()  # (shift-clicked)
    [listener] = [x for x in button._event_listeners.values() if x.type == "click"]
    with user.client:
        button._handle_event({"listener_id": listener.id, "args": {"shiftKey": True}})
    for _i in range(50):
        if inserted:
            break
        await asyncio.sleep(0.02)
    assert inserted == [
        f'"Our ranking model beats sparse ones" %% [Application, p. 1](/doc/{doc}?page=1) %%'
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


async def test_folder_notes(user: User, monkeypatch, fake_viewer):
    _, period, ids = _person()
    pdfs.save(ids["a"], PDF, None)
    folder = folders.folder_of_period(period)[0]
    selection = {"text": "Deep models rank", "p": 3}
    user.javascript_rules[re.compile(r"vrPdf\.quoted\(\)")] = lambda _: selection
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert_block", lambda self, text: inserted.append(text))
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="folder-note")
    # (the paper's own notes shown first, the folder's in the last tab)
    tabs = next(  # (not the person's tabs, in the header)
        t for t in user.find(kind=ui.tabs).elements if t.value != "publications"
    )
    assert tabs.value == "notes"
    assert [t._props["name"] for t in tabs.default_slot.children if isinstance(t, ui.tab)][
        -1
    ] == "folder-notes"
    user.find(marker="folder-note").elements.pop().value = "Shortlist: **two** papers"
    await note_status(user, "folder-note")
    assert folders.notes_of(period) == "Shortlist: **two** papers"
    # The person's within the folder: not another person's, not the folder's own notes.
    other_period = folders.add_person(folder, make_person("Ann Smith"))
    assert folders.notes_of(other_period) == ""
    assert not next(f for f in folders.folders() if f.id == folder).notes
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


async def test_folder_notes_stale_save_refused(user: User, monkeypatch, fake_viewer):
    """An editor whose notes changed since it loaded them (saved elsewhere) does not save over
    them: it says so, and offers to copy its text or to reload."""
    _, period, ids = _person()
    pdfs.save(ids["a"], PDF, None)
    copied = []
    monkeypatch.setattr(ui.clipboard, "write", lambda text: copied.append(text))
    folders.set_notes(period, "Mine.")
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="folder-note")
    await user.should_not_see(marker="folder-note-conflict")
    # Saved elsewhere meanwhile (e.g. a section appended), then typed here: refused.
    appended = "Mine.\n\n## Starred papers\n\nAppended."
    folders.set_notes(period, appended)
    user.find(marker="folder-note").elements.pop().value = "Mine, edited."
    await note_status(user, "folder-note", "Not saved")
    assert folders.notes_of(period) == appended
    await user.should_see(marker="folder-note-conflict")
    user.find(marker="folder-note-copy-unsaved").click()
    assert copied == ["Mine, edited."]
    # Reloaded: the notes as saved, and saving again from there.
    user.find(marker="folder-note-reload").click()
    editor = user.find(marker="folder-note").elements.pop()
    assert editor.value == appended
    await user.should_not_see(marker="folder-note-conflict")
    editor.value = appended + " More."
    await note_status(user, "folder-note")
    assert folders.notes_of(period) == appended + " More."
    # Saving from the notes as they are: not refused.
    assert folders.set_notes(period, "Same.", base=folders.notes_of(period))
    assert not folders.set_notes(period, "Other.", base="Mine.")
    assert folders.notes_of(period) == "Same."


async def test_folder_notes_cleared_after_asking(user: User, fake_viewer):
    """Emptying the notes saves nothing until confirmed (else restores them)."""
    _, period, ids = _person()
    pdfs.save(ids["a"], PDF, None)
    folders.set_notes(period, "Precious notes.")
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="folder-note")
    editor = user.find(marker="folder-note").elements.pop()
    editor.value = ""
    await note_status(user, "folder-note", "Not saved")
    await user.should_see(marker="folder-note-restore")
    assert folders.notes_of(period) == "Precious notes."
    user.find(marker="folder-note-restore").click()
    assert editor.value == "Precious notes."
    await user.should_not_see(marker="folder-note-restore")
    assert folders.notes_of(period) == "Precious notes."
    # Confirmed: cleared.
    editor.value = "  "
    await note_status(user, "folder-note", "Not saved")
    user.find(marker="folder-note-clear").click()
    await note_status(user, "folder-note", "Saved")
    assert folders.notes_of(period) == ""
    # Empty already: nothing to ask.
    editor.value = "New start."
    await note_status(user, "folder-note")
    assert folders.notes_of(period) == "New start."


async def test_folder_notes_cite_and_copy(user: User, monkeypatch, fake_viewer):
    """The folder's notes cite a paper and copy with the references, in the PDF viewer and on
    the documents page."""
    _, period, ids = _person()
    pdfs.save(ids["a"], PDF, None)
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert", lambda self, text: inserted.append(text))
    copied = []
    monkeypatch.setattr(ui.clipboard, "write", lambda text: copied.append(text))
    await user.open(f"/pdf/{ids['a']}?period={period}")
    await user.should_see(marker="folder-note-cite")
    await asyncio.sleep(0.5)  # (the papers loaded)
    user.find(marker="folder-note-cite").click()
    await user.should_see(marker="folder-note-cite-paper")
    select_ = user.find(marker="folder-note-cite-paper").elements.pop()
    options = list(select_.options)
    assert len(options) >= 3
    assert select_.options[options[0]].startswith(TITLES["a"])  # (the paper shown first)
    select_.value = options[0]
    assert inserted == [f"[@{options[0]}]"]
    user.find(marker="folder-note").elements.pop().value = f"See [@{options[0]}]."
    user.find(marker="folder-note-copy").click()
    assert len(copied) == 1 and copied[0].startswith("See [1].")
    assert TITLES["a"] in copied[0]
    # Shift+click: through the copy filter enabled (none: says so), as rich text.
    rich = []
    monkeypatch.setattr(copying, "copy_rich", lambda html, plain: rich.append((html, plain)))
    [button] = user.find(marker="folder-note-copy").elements
    [listener] = [x for x in button._event_listeners.values() if x.type == "click"]

    def shift_click() -> None:
        with user.client:
            button._handle_event({"listener_id": listener.id, "args": {"shiftKey": True}})

    shift_click()
    await user.should_see("No copy filter enabled")
    copy_filters.set_enabled(["marmotte"])
    shift_click()
    assert len(rich) == 1 and rich[0][0].startswith("<p>See [1].</p>")
    assert rich[0][1].startswith("See [1].")
    assert len(copied) == 1
    # The documents page has them too.
    copied.clear()
    inserted.clear()
    doc = documents.add(period, "CV.pdf", PDF)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="folder-note-copy")
    await asyncio.sleep(0.5)
    user.find(marker="folder-note-copy").click()
    assert len(copied) == 1
    user.find(marker="folder-note-cite").click()
    await user.should_see(marker="folder-note-cite-paper")
    select_ = user.find(marker="folder-note-cite-paper").elements.pop()
    select_.value = next(iter(select_.options))
    assert len(inserted) == 1 and re.fullmatch(r"\[@\w+\]", inserted[0])


async def test_last_place(user: User, fake_viewer):
    _, period, ids = _person()
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
    categories.rename(projects, " Projects ")
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
        "## Research\n\n### Projects\n\n"
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
        "## Research\n\n### Projects\n\n"
        "- Led the ANR project X. %% Application, p. 2 %%\n\n"
        "## Rayonnement\n\n"
        "- **Teaching**\n"
        "  - Taught IR. %% Application, p. 1 %%\n"
        "  - Chaired a workshop. %% Application, p. 3 %% — 2022\n"
        "- **Research › Projects**\n"
        "  - Led the ANR project X. %% Application, p. 2 %%\n"
    )
    # Elsewhere (the block [[influence]]): not at the end; only it, without its heading.
    assert "Rayonnement" not in categories.markdown(folder, period, influence="none")
    assert categories.markdown(folder, period, influence="only") == (
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
        "## Teaching\n\n- Taught and gave courses. %% “Taught IR (M2).” (Application, p. 1); "
        "p. 2; “Elsewhere.” (Other, p. 1) %% — 2019–2020\n"
    )
    # Might be already an excerpt: similar words, or found by a query.
    assert [v.id for v in categories.similar_excerpts(period, "Gave courses, at university")] == [
        later
    ]
    assert categories.similar_excerpts(period, "Wrote a book on ranking") == []
    assert [v.id for v in categories.similar_excerpts(period, "Wrote", "elsewhere")] == [later]
    categories.set_group_text(later, " ")
    assert categories.excerpts(period, grouped=True)[0].group_text is None
    categories.update_excerpt(later, start=2019, end=2020)
    # Ordered within the category (a group with it), taken out of a group, the lead removed.
    y = categories.add_excerpt(teaching, period, "Supervised.", 3, [], document_id=doc)
    categories.place_excerpt(y, e.id, "before")
    assert [v.id for v in categories.excerpts(period, grouped=True)] == [y, later]
    categories.split_excerpt(x)
    assert [v.id for v in categories.excerpts(period, grouped=True)] == [y, later, x]
    categories.remove_excerpt(later)
    lead = next(v for v in categories.excerpts(period, grouped=True) if v.id == e.id)
    assert (lead.years, lead.members) == ("2019–2020", [])


def test_excerpt_tags():
    """An excerpt's tags (by name; new ones made, shared with the papers): Obsidian #tags
    leading its Markdown item; a group's are those of its lead (merged: of both)."""
    from sci_report_analyzer import annotations, categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    teaching = categories.add(folder, "Teaching")
    doc = documents.add(period, "Application.pdf", PDF)
    shared = annotations.save_tag("To discuss")
    a = categories.add_excerpt(
        teaching, period, "Taught IR.", 1, [], document_id=doc, tags=["strong point", "To discuss"]
    )
    b = categories.add_excerpt(teaching, period, "Gave a course.", 2, [], document_id=doc)
    [ea, eb] = categories.excerpts(period)
    assert (ea.tag_names, eb.tags) == (["To discuss", "strong point"], [])
    assert {"To discuss", "strong point"} <= set(categories.tag_names())
    assert [t.id for t in annotations.all_tags() if t.name == "To discuss"] == [shared]
    assert categories.markdown(folder, period) == (
        "## Teaching\n\n- #To-discuss #strong-point Taught IR. %% Application, p. 1 %%\n"
        "- Gave a course. %% Application, p. 2 %%\n"
    )
    assert [v.id for v in categories.similar_excerpts(period, "x", "#strong")] == [a]
    # Edited (None: kept); merged, the group has both's tags.
    categories.update_excerpt(a, influence=True)
    assert categories.excerpts(period)[0].tag_names == ["To discuss", "strong point"]
    categories.update_excerpt(a, tags=["strong point"])
    categories.update_excerpt(b, tags=["2024"])
    assert categories.merge_excerpts(a, b) is None
    [g] = categories.excerpts(period, grouped=True)
    assert (g.id, g.tag_names, g.members[0].tags) == (b, ["2024", "strong point"], [])
    assert categories.markdown(folder, period).startswith(
        "## Teaching\n\n- #tag-2024 #strong-point Taught IR."
    )
    # The lead removed: the next one has the group's tags; a tag deleted: off the excerpts.
    categories.remove_excerpt(b)
    [e] = categories.excerpts(period)
    assert e.tag_names == ["2024", "strong point"]
    annotations.delete_tag(next(t.id for t in annotations.all_tags() if t.name == "2024"))
    assert categories.excerpts(period)[0].tag_names == ["strong point"]


async def test_excerpt_tags_in_the_viewer(user: User, fake_viewer):
    """Tags given when filing an excerpt (an existing one, or typed), shown on its row, and
    edited."""
    from sci_report_analyzer import annotations, categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    annotations.save_tag("To discuss")
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "Led the Quokka project", "p": 1, "rects": []}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{research}")
    tags = user.find(marker="excerpt-tags").elements.pop()
    assert "To discuss" in tags.options
    tags.value = ["To discuss", "Quokka"]
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    [e] = categories.excerpts(period)
    assert e.tag_names == ["Quokka", "To discuss"]
    await user.should_see(marker=f"excerpt-tags-{e.id}")
    user.find(marker=f"excerpt-edit-{e.id}").click()
    await user.should_see(marker="excerpt-edit-save")
    tags = user.find(marker="excerpt-tags").elements.pop()
    assert tags.value == ["Quokka", "To discuss"]
    tags.value = ["Quokka"]
    user.find(marker="excerpt-edit-save").click()
    await user.should_not_see(marker="excerpt-edit-save")
    assert categories.excerpts(period)[0].tag_names == ["Quokka"]


def test_language_of_the_reports():
    """A folder's reports generated in its language (none: the interface's), whatever that
    of the interface."""
    from sci_report_analyzer import categories, i18n, reports

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Recherche")
    x = categories.add_excerpt(research, period, "Projet Comète.", 1, [])
    categories.update_excerpt(x, start=2021)
    assert reports.language(folder) is None
    assert "since 2021" in categories.markdown(folder, period)
    reports.save_language(folder, "fr")
    assert reports.language(folder) == "fr" and i18n.language() == "en"
    assert "depuis 2021" in categories.markdown(folder, period)
    ctx = reports.folder_context([], period)
    assert "depuis 2021" in reports.render("[]{.excerpts}", ctx).text
    assert i18n.language() == "en"  # (the interface's, as it was)


def test_nested_influence():
    """Optionally, the "rayonnement" excerpts only in its section, under their categories."""
    from sci_report_analyzer import categories, reports

    _, period, _ = _person()
    folder = folders.folders()[0].id
    societies = categories.add(folder, "Comités et sociétés savantes")
    research = categories.add(folder, "Recherche")
    projects = categories.add(folder, "Projets", research)
    doc = documents.add(period, "Dossier.pdf", PDF)
    member = categories.add_excerpt(
        societies, period, "Membre du Comité Galactique.", 1, [], document_id=doc
    )
    categories.update_excerpt(member, influence=True)
    led = categories.add_excerpt(projects, period, "Projet Nébuleuse.", 2, [], document_id=doc)
    categories.update_excerpt(led, start=2021, end=2024, influence=True)
    categories.add_excerpt(projects, period, "Projet Comète.", 3, [], document_id=doc)
    flat = categories.markdown(folder, period)
    assert flat.startswith("## Comités et sociétés savantes\n\n- Membre du Comité Galactique.")
    assert categories.markdown(folder, period, mode="copy") == flat
    assert categories.markdown(folder, period, mode="move") == (
        "## Recherche\n\n### Projets\n\n- Projet Comète. %% Dossier, p. 3 %%\n\n"
        "## Rayonnement\n\n"
        "### Comités et sociétés savantes\n\n- Membre du Comité Galactique. %% Dossier, p. 1 %%\n\n"
        "### Recherche\n\n#### Projets\n\n- Projet Nébuleuse. %% Dossier, p. 2 %% — 2021–2024\n"
    )
    # Only the section (the block [[influence]]): its subsections at its level.
    assert categories.markdown(folder, period, mode="move", level=3, influence="only") == (
        "### Comités et sociétés savantes\n\n"
        "- Membre du Comité Galactique. %% Dossier, p. 1 %%\n\n"
        "### Recherche\n\n#### Projets\n\n"
        "- Projet Nébuleuse. %% Dossier, p. 2 %% — 2021–2024\n"
    )
    # Marked: only in their categories.
    assert categories.markdown(folder, period, mode="mark") == (
        "## Comités et sociétés savantes\n\n"
        "- #influence Membre du Comité Galactique. %% Dossier, p. 1 %%\n\n"
        "## Recherche\n\n### Projets\n\n"
        "- #influence Projet Nébuleuse. %% Dossier, p. 2 %% — 2021–2024\n"
        "- Projet Comète. %% Dossier, p. 3 %%\n"
    )
    # The folder's choice (a block's options, formerly, ignored).
    assert reports.influence_mode(folder) == "copy"
    reports.save_influence_mode(folder, "move")
    assert categories.markdown(folder, period) == categories.markdown(folder, period, mode="move")
    ctx = reports.folder_context([], period)
    out = reports.render("# Dossier\n\n[[excerpts]]\n", ctx).text
    assert "\n## Comités" not in out and "\n### Comités et sociétés savantes\n" in out
    assert reports.render("# Dossier\n\n[]{.excerpts .flat-influence}\n", ctx).text == out
    # Where [[influence]] (or [[rayonnement]]) is: there, no longer after [[excerpts]].
    out = reports.render(
        "# Dossier\n\n[[excerpts]]\n\n## Rayonnement\n\n[[rayonnement]]\n", ctx
    ).text
    assert out.count("Rayonnement") == 1 and out.endswith(
        "## Rayonnement\n\n### Comités et sociétés savantes\n\n"
        "- Membre du Comité Galactique. %% Dossier, p. 1 %%\n\n"
        "### Recherche\n\n#### Projets\n\n"
        "- Projet Nébuleuse. %% Dossier, p. 2 %% — 2021–2024\n"
    )
    reports.save_influence_mode(folder, "copy")
    ctx = reports.folder_context([], period)
    assert reports.render("[]{.excerpts}", ctx).text.startswith("## Comités")
    # (formerly a flag: "nested_influence")
    reports._save_citations(folder, influence_mode=None, nested_influence=True)
    assert reports.influence_mode(folder) == "move"


def test_excerpts_of_a_category_block():
    """[[excerpts#id]]: the excerpts of the category of that id (and below), its
    subcategories' headings at the block's level, then left out of [[excerpts]]; an unknown
    id: a warning."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    projects = categories.add(folder, "Projects", research)
    teaching = categories.add(folder, "Teaching")
    doc = documents.add(period, "Dossier.pdf", PDF)
    categories.add_excerpt(research, period, "Ten papers.", 1, [], document_id=doc)
    categories.add_excerpt(projects, period, "Led Nebula.", 2, [], document_id=doc)
    led = categories.add_excerpt(teaching, period, "Two courses.", 3, [], document_id=doc)
    categories.update_excerpt(led, influence=True)
    assert categories.set_key(research, " my research ")
    assert categories.tree(folder)[0].key == "my-research"
    assert not categories.set_key(teaching, "my-research")  # (taken)
    ctx = reports.folder_context([], period)
    out = reports.render(
        "# Research\n\n[[excerpts#my-research]]\n\n# Other stuff\n\n[[excerpts]]\n", ctx
    ).text
    assert out == (
        "# Research\n\n- Ten papers. %% Dossier, p. 1 %%\n\n## Projects\n\n"
        "- Led Nebula. %% Dossier, p. 2 %%\n\n# Other stuff\n\n"
        "## Teaching\n\n- Two courses. %% Dossier, p. 3 %%\n\n"
        "## Rayonnement\n\n- **Teaching**\n  - Two courses. %% Dossier, p. 3 %%\n"
    )
    # Nested ones: each its own (a block above another one leaving it out).
    assert categories.set_key(projects, "projects")
    out = reports.render("[[excerpts#my-research]]\n\n# P\n\n[[excerpts#projects]]", ctx).text
    assert out == "- Ten papers. %% Dossier, p. 1 %%\n\n# P\n\n- Led Nebula. %% Dossier, p. 2 %%"
    r = reports.render("[[excerpts#nope]]", ctx)
    assert "⚠" in r.text and r.errors == ["No category with the id “nope”"]
    # The old syntax, still read.
    assert reports.render("[]{.excerpts}", ctx).text == reports.render("[[excerpts]]", ctx).text


def test_excerpt_markers():
    """Salient and caveat excerpts tagged (the folder's tags, by default in its language);
    marked (or no longer), moved first in their categories (salient, influence, caveat) unless
    not, then moved as one likes (the report in their order)."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Recherche")
    doc = documents.add(period, "Dossier.pdf", PDF)
    plain = categories.add_excerpt(research, period, "Projet Comète.", 1, [], document_id=doc)
    weak = categories.add_excerpt(
        research, period, "Peu d'encadrement.", 2, [], document_id=doc, caveat=True
    )
    prize = categories.add_excerpt(research, period, "Prix Andromède.", 3, [], document_id=doc)
    categories.update_excerpt(prize, influence=True)
    best = categories.add_excerpt(research, period, "Projet Nébuleuse.", 4, [], document_id=doc)
    categories.update_excerpt(best, salient=True, tags=["ERC"])
    view = {e.id: e for e in categories.excerpts(period)}
    assert view[best].salient and view[weak].caveat and not view[plain].salient
    assert categories.markdown(folder, period, mode="mark") == (
        "## Recherche\n\n"
        "- #ERC #salient Projet Nébuleuse. %% Dossier, p. 4 %%\n"
        "- #influence Prix Andromède. %% Dossier, p. 3 %%\n"
        "- #caveat Peu d'encadrement. %% Dossier, p. 2 %%\n"
        "- Projet Comète. %% Dossier, p. 1 %%\n"
    )
    # On the PDF, next to them: their icons.
    marks = {t["id"]: t["marks"] for t in categories.tints(period, doc, {})}
    assert marks == {best: ["⭐"], prize: ["🌍"], weak: ["⚠️"], plain: []}
    # Moved as one likes: kept so.
    categories.place_excerpt(plain, best, "before")
    order = [e.id for e in categories.excerpts(period)]
    assert order == [plain, best, prize, weak]
    # The folder's tags (in French by default), and markers not moving their excerpts.
    reports.save_language(folder, "fr")
    assert reports.markers(folder)["caveat"] == reports.Marker("bemol")
    marks = reports.markers(folder)
    reports.save_markers(
        folder,
        {
            **marks,
            "salient": reports.Marker("#fort", first=False),
            "caveat": reports.Marker("bemol", first=False),
        },
    )
    assert reports.markers(folder)["salient"] == reports.Marker("fort", first=False)
    assert reports.markers(folder)["influence"] == reports.Marker("rayonnement")
    categories.update_excerpt(weak)  # (no longer a caveat: not moved)
    assert [e.id for e in categories.excerpts(period)] == order
    categories.update_excerpt(weak, influence=True)  # (first)
    assert [e.id for e in categories.excerpts(period)] == [weak, plain, best, prize]
    categories.update_excerpt(prize)  # (after those marked first)
    assert [e.id for e in categories.excerpts(period)] == [weak, prize, plain, best]
    assert categories.markdown(folder, period, mode="mark") == (
        "## Recherche\n\n"
        "- #rayonnement Peu d'encadrement. %% Dossier, p. 2 %%\n"
        "- Prix Andromède. %% Dossier, p. 3 %%\n"
        "- Projet Comète. %% Dossier, p. 1 %%\n"
        "- #ERC #fort Projet Nébuleuse. %% Dossier, p. 4 %%\n"
    )
    reports.save_language(folder, None)  # (a default tag follows the language)
    assert reports.markers(folder)["caveat"] == reports.Marker("caveat", first=False)
    # Passed on to the next one of a group.
    assert categories.merge_excerpts(plain, best) is None
    categories.remove_excerpt(best)
    assert {e.id: e for e in categories.excerpts(period)}[plain].salient


def test_excerpt_levels():
    """An excerpt's level (one of the folder's; none: its default one) tagged after its
    markers; the folder's levels (local, national, international by default) and their tags
    (by default in its language), turned into a text when copied."""
    from sci_report_analyzer import categories, copy_filters

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Dossier.pdf", PDF)
    talk = categories.add_excerpt(
        research, period, "Keynote at SIGIR.", 1, [], document_id=doc, level="international"
    )
    seminar = categories.add_excerpt(research, period, "Seminar in Lyon.", 2, [], document_id=doc)
    assert [lv.key for lv in reports.levels(folder)] == ["local", "national", "international"]
    assert reports.default_level(folder) is None
    assert categories.markdown(folder, period) == (
        "## Research\n\n"
        "- #international Keynote at SIGIR. %% Dossier, p. 1 %%\n"
        "- Seminar in Lyon. %% Dossier, p. 2 %%\n"
    )
    # A default level; another tag, a new level; then, salient: after its marker's tag.
    levels = reports.levels(folder)
    reports.save_levels(
        folder,
        [*levels[:2], reports.Level("international", "#intl"), reports.Level("", "regional")],
        "national",
    )
    assert reports.levels(folder)[2:] == [
        reports.Level("international", "intl"),
        reports.Level("regional", "regional"),
    ]
    categories.update_excerpt(talk, salient=True, level="international")
    md = categories.markdown(folder, period)
    assert "- #salient #intl Keynote at SIGIR." in md
    assert "- #national Seminar in Lyon." in md
    # A level removed: its excerpts with none (nor the default one), the default with it.
    reports.save_levels(folder, reports.levels(folder)[:2], "regional")
    assert reports.default_level(folder) is None
    md = categories.markdown(folder, period)
    assert "- #salient Keynote at SIGIR." in md and "- Seminar in Lyon." in md
    assert {e.id: e.level for e in categories.excerpts(period)}[talk] == "international"
    # In French by default (the same words); copied: after the line's text.
    reports.save_language(folder, "fr")
    assert reports.levels(folder)[1] == reports.Level("national", "national")
    categories.update_excerpt(seminar, level="national")
    _html, plain = copy_filters.marmotte(categories.markdown(folder, period))
    assert "• Seminar in Lyon. (national)" in plain
    # Passed on to the next one of a group.
    assert categories.merge_excerpts(talk, seminar) is None
    categories.remove_excerpt(seminar)
    assert categories.excerpts(period)[0].level == "national"


async def test_excerpt_levels_in_the_viewer(user: User, fake_viewer):
    """A level chosen when editing an excerpt, shown on its row (the folder's default one, in
    italics), searched; the folder's levels in its reports' settings."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    e = categories.add_excerpt(research, period, "Led the Quokka project", 1, [], document_id=doc)
    other = categories.add_excerpt(research, period, "Taught IR", 2, [], document_id=doc)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"excerpt-edit-{e}")
    await user.should_not_see(marker=f"excerpt-level-{e}")
    user.find(marker=f"excerpt-edit-{e}").click()
    await user.should_see(marker="excerpt-level")
    level = user.find(marker="excerpt-level").elements.pop()
    assert list(level.chips) == ["local", "national", "international"]
    # A badge chosen, clicked again: no level; then chosen.
    user.find(marker="excerpt-level-badge-local").click()
    assert level.value == "local"
    await user.should_not_see(marker="excerpt-level-none")
    user.find(marker="excerpt-level-badge-local").click()
    assert level.value == ""
    await user.should_see(marker="excerpt-level-none")
    user.find(marker="excerpt-level-badge-national").click()
    user.find(marker="excerpt-edit-save").click()
    await user.should_not_see(marker="excerpt-edit-save")
    assert {x.id: x.level for x in categories.excerpts(period)}[e] == "national"
    await user.should_see(marker=f"excerpt-level-{e}")
    user.find(marker="excerpts-search").type("nation")
    await user.should_not_see(marker=f"excerpt-{other}")
    await user.should_see(marker=f"excerpt-{e}")
    # The folder's levels: one added, a default one.
    await user.open(f"/?folder={folder}")
    user.find(marker="folder-edit").click()
    await user.should_see(marker="folder-tab-notes")
    user.find(marker="folder-tab-notes").click()
    await user.should_see(marker="folder-level-tag-2")
    user.find(marker="folder-level-add").click()
    await user.should_see(marker="folder-level-tag-3")
    user.find(marker="folder-level-tag-3").type("regional")
    user.find(marker="folder-level-remove-0").click()
    await user.should_not_see(marker="folder-level-tag-3")
    user.find(marker="folder-level-default").elements.pop().value = "international"
    user.find(marker="folder-save").click()
    await user.should_not_see(marker="folder-save")
    assert [lv.tag for lv in reports.levels(folder)] == ["national", "international", "regional"]
    assert reports.default_level(folder) == "international"


def test_section_texts():
    """A text per category and person (Markdown, e.g. a summary): after the heading of its
    section; nested, that of a section left out in the Rayonnement section."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    teaching = categories.add(folder, "Enseignement")
    research = categories.add(folder, "Recherche")
    projects = categories.add(folder, "Projets", research)
    empty = categories.add(folder, "Divers")
    doc = documents.add(period, "Dossier.pdf", PDF)
    categories.add_excerpt(teaching, period, "Cours de Zorglub.", 1, [], document_id=doc)
    led = categories.add_excerpt(projects, period, "Projet Nébuleuse.", 2, [], document_id=doc)
    categories.update_excerpt(led, influence=True)
    # Stored (trimmed; blank: removed), by person.
    assert (
        categories.section_texts(period) == {} and categories.section_text(period, teaching) == ""
    )
    categories.set_section_text(period, teaching, "  Deux **cours**.\n")
    categories.set_section_text(period, research, "Une recherche active.")
    categories.set_section_text(period, projects, "Un projet.")
    categories.set_section_text(period, empty, "Rien.")
    assert categories.section_text(period, teaching) == "Deux **cours**."
    categories.set_section_text(period, research, "Une recherche *très* active.")
    assert categories.section_texts(period)[research] == "Une recherche *très* active."
    # After the heading, before the items (a section left out: its text too).
    assert categories.markdown(folder, period, mode="copy") == (
        "## Enseignement\n\nDeux **cours**.\n\n- Cours de Zorglub. %% Dossier, p. 1 %%\n\n"
        "## Recherche\n\nUne recherche *très* active.\n\n### Projets\n\nUn projet.\n\n"
        "- Projet Nébuleuse. %% Dossier, p. 2 %%\n\n"
        "## Rayonnement\n\n- **Recherche › Projets**\n  - Projet Nébuleuse. %% Dossier, p. 2 %%\n"
    )
    # Nested: the sections left out (all their excerpts taken out), their texts in the
    # Rayonnement section.
    assert categories.markdown(folder, period, mode="move") == (
        "## Enseignement\n\nDeux **cours**.\n\n- Cours de Zorglub. %% Dossier, p. 1 %%\n\n"
        "## Rayonnement\n\n"
        "### Recherche\n\nUne recherche *très* active.\n\n#### Projets\n\nUn projet.\n\n"
        "- Projet Nébuleuse. %% Dossier, p. 2 %%\n"
    )
    # A section kept: its text stays there.
    categories.add_excerpt(projects, period, "Projet Comète.", 3, [], document_id=doc)
    out = categories.markdown(folder, period, mode="move")
    assert "## Recherche\n\nUne recherche *très* active.\n\n### Projets\n\nUn projet.\n\n" in out
    assert "### Recherche\n\n#### Projets\n\n- Projet Nébuleuse." in out
    # In the notes, through the block.
    ctx = reports.folder_context([], period)
    assert "## Enseignement\n\nDeux **cours**.\n" in reports.render("[]{.excerpts}", ctx).text
    # Moved with the excerpts (after the text there), deleted with the category.
    assert categories.move_excerpts(research, teaching) == 2
    assert categories.section_texts(period) == {
        teaching: "Deux **cours**.\n\nUne recherche *très* active.\n\nUn projet.",
        empty: "Rien.",
    }
    assert categories.delete(empty)
    assert list(categories.section_texts(period)) == [teaching]
    categories.set_section_text(period, teaching, " ")
    assert categories.section_texts(period) == {}
    # Deleted with the person's period.
    categories.set_section_text(period, teaching, "Encore.")
    folders.delete_period(period)
    with session_scope() as s:
        assert s.scalars(select(SectionText)).all() == []


def test_section_excerpts():
    """A section's excerpts and those of its subsections (under their paths), for reference
    when writing its text."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    projects = categories.add(folder, "Projects", research)
    other = categories.add(folder, "Teaching")
    doc = documents.add(period, "Dossier.pdf", PDF)
    categories.add_excerpt(projects, period, "Led Nebula.", 3, [], document_id=doc)
    categories.add_excerpt(research, period, "Ten papers.", 1, [], document_id=doc)
    categories.add_excerpt(other, period, "Two courses.", 2, [], document_id=doc)
    assert categories.section_excerpts(period, research) == (
        "- Ten papers. (Dossier, p. 1)\n\n**Research › Projects**\n\n- Led Nebula. (Dossier, p. 3)"
    )
    assert categories.section_excerpts(period, projects) == "- Led Nebula. (Dossier, p. 3)"
    assert categories.section_excerpts(period, categories.add(folder, "Misc")) == ""


def test_section_summarised_by_its_text():
    """A section summarised by its text only: its excerpts not listed, kept in the text in a
    comment (not rendered); nested, a section left out: in the Rayonnement section too."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    teaching = categories.add(folder, "Enseignement")
    research = categories.add(folder, "Recherche")
    doc = documents.add(period, "Dossier.pdf", PDF)
    categories.add_excerpt(teaching, period, "Cours de Zorglub.", 1, [], document_id=doc)
    categories.add_excerpt(teaching, period, "Cours d'IR.", 2, [], document_id=doc)
    led = categories.add_excerpt(research, period, "Projet Nébuleuse.", 3, [], document_id=doc)
    categories.update_excerpt(led, influence=True)
    # The excerpts in a comment (their places in parentheses: comments do not nest), added
    # once, after the text.
    block = (
        "%% Excerpts (not shown):\n- Cours de Zorglub. (Dossier, p. 1)\n"
        "- Cours d'IR. (Dossier, p. 2)\n%%"
    )
    assert categories.excerpts_comment(period, teaching) == block
    text = categories.with_excerpts_comment("Deux **cours**.", period, teaching)
    assert text == f"Deux **cours**.\n\n{block}"
    assert categories.with_excerpts_comment(text, period, teaching) == text
    assert categories.with_excerpts_comment("", period, teaching) == block
    assert categories.excerpts_comment(period, categories.add(folder, "Divers")) == ""
    reports.save_language(folder, "fr")  # (in the language of the reports)
    french = categories.with_excerpts_comment("", period, teaching)
    assert french.startswith("%% Extraits (non affichés) :\n- Cours de Zorglub.")
    assert categories.with_excerpts_comment(french, period, teaching) == french
    reports.save_language(folder, None)
    # Summarised: the text only (its comment kept, not rendered).
    categories.set_section_text(period, teaching, text, summary_only=True)
    assert categories.summarised(period) == {teaching}
    categories.set_section_text(period, teaching, text)  # (unchanged)
    assert categories.summarised(period) == {teaching}
    out = categories.markdown(folder, period, mode="copy")
    assert out.startswith(f"## Enseignement\n\nDeux **cours**.\n\n{block}\n\n## Recherche\n")
    assert "- Cours de Zorglub. %%" not in out  # (listed: with its places in a comment)
    shown = copy_filters.strip_comments(
        reports.render("[]{.excerpts}", reports.folder_context([], period)).text
    )
    assert "Deux **cours**." in shown and "Zorglub" not in shown
    # (the text without its excerpts: they follow it in the notes)
    categories.set_section_text(period, teaching, "Deux **cours**.")
    assert categories.markdown(folder, period, mode="copy") == out
    # Nested, a section left out: its text in the Rayonnement section (its excerpts after it,
    # in a comment).
    categories.set_section_text(period, research, "Un projet.", summary_only=True)
    assert categories.markdown(folder, period, mode="move").endswith(
        "## Rayonnement\n\n### Recherche\n\nUn projet.\n\n"
        "%% Excerpts (not shown):\n- Projet Nébuleuse. (Dossier, p. 3)\n%%\n"
    )
    # Not summarised: listed again; moved with the text.
    categories.set_section_text(period, teaching, text, summary_only=False)
    assert "- Cours de Zorglub. %% Dossier, p. 1 %%" in categories.markdown(folder, period)
    assert categories.move_excerpts(research, teaching) == 1
    assert categories.summarised(period) == set()
    other = categories.add(folder, "Autre")
    assert categories.move_excerpts(teaching, other) == 3
    categories.set_section_text(period, other, "Tout.", summary_only=True)
    third = categories.add(folder, "Troisième")
    categories.move_excerpts(other, third)
    assert categories.summarised(period) == {third}


def test_excerpt_filed_first_in_any_category():
    """Dropped onto a category's heading (another one, an empty sub-category, its own): the
    excerpt (with its group) moves there, first."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    teaching = categories.add(folder, "Teaching")
    projects = categories.add(folder, "Projects", research)
    doc = documents.add(period, "Application.pdf", PDF)
    a = categories.add_excerpt(research, period, "Wrote papers.", 1, [], document_id=doc)
    b = categories.add_excerpt(teaching, period, "Taught.", 1, [], document_id=doc)
    c = categories.add_excerpt(teaching, period, "Supervised.", 2, [], document_id=doc)
    m = categories.add_excerpt(research, period, "More papers.", 2, [], document_id=doc)
    categories.merge_excerpts(m, a)  # (a group: moved with its lead)

    def where() -> list[tuple[int, int]]:
        return [(v.id, v.category_id) for v in categories.excerpts(period, grouped=True)]

    categories.file_excerpt_first(m, teaching)
    assert where() == [(a, teaching), (b, teaching), (c, teaching)]
    assert {e.category_id for e in categories.excerpts(period)} == {teaching}
    categories.file_excerpt_first(c, teaching)  # (its own: first)
    assert [v.id for v in categories.excerpts(period, grouped=True)] == [c, a, b]
    categories.file_excerpt_first(b, projects)  # (an empty sub-category)
    assert (b, projects) in where()


def test_category_colours():
    """Each category has a colour (the palette's, in turn; changed): that of its excerpts,
    tinted in it on the PDFs."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    teaching = categories.add(folder, "Teaching")
    projects = categories.add(folder, "Projects", research)
    p = categories.PALETTE
    assert [n.colour for n in categories.tree(folder)] == [p[0], p[2], p[1]]
    categories.set_colour(research, "#123456")
    assert categories.tree(folder)[0].colour == "#123456"
    doc = documents.add(period, "Application.pdf", PDF)
    led = categories.add_excerpt(
        projects, period, "Led a project.", 2, [[1, 2, 3, 4]], document_id=doc
    )
    taught = categories.add_excerpt(teaching, period, "Taught.", 1, [[1, 2, 3, 4]], document_id=doc)
    names = {n.id: n.path for n in categories.tree(folder)}
    assert [(t["category"], t["colour"]) for t in categories.tints(period, doc, names)] == [
        ("Research › Projects", categories.PALETTE[2]),
        ("Teaching", categories.PALETTE[1]),
    ]
    # Merged: the group takes the category, its colour with it; moved: the new one's.
    categories.merge_excerpts(taught, led)
    assert {t["colour"] for t in categories.tints(period, doc, names)} == {categories.PALETTE[2]}
    categories.split_excerpt(taught)
    categories.move_excerpt(taught, research)
    by_id = {e.id: e.colour for e in categories.excerpts(period)}
    assert by_id == {led: categories.PALETTE[2], taught: "#123456"}
    # None: the default; a new one takes the first free colour of the palette.
    categories.set_colour(research, None)
    assert categories.tree(folder)[0].colour == categories.DEFAULT_COLOUR
    other = categories.add(folder, "Other")
    assert (
        next(n.colour for n in categories.tree(folder) if n.id == other) == (categories.PALETTE[0])
    )
    # Copied with the categories.
    second = folders.save_folder(None, "Second committee")
    categories.copy_tree(folder, second)
    assert [n.colour for n in categories.tree(second)] == [
        n.colour for n in categories.tree(folder)
    ]


async def test_excerpt_from_an_area(user: User, monkeypatch, fake_viewer):
    """An area (a rectangle) of a page, selected: its text filed as an excerpt, the
    rectangle as its place (tinted as a text's)."""
    from nicegui import Client

    from sci_report_analyzer import categories

    scripts = []
    run = Client.run_javascript
    monkeypatch.setattr(
        Client,
        "run_javascript",
        lambda self, code, **kw: (scripts.append(code), run(self, code))[1],
    )
    _, period, _ = _person()
    folder = folders.folders()[0].id
    figures = categories.add(folder, "Figures")
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "", "p": 2, "rects": [[60.5, 400, 320, 520.2, 2]], "area": True}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
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
    # PDF.js's highlights under it: removed (its tint in their place).
    assert "vrPdf.unhighlight([[60.5, 400, 320, 520.2, 2]], 2)" in scripts
    # Filed already (its text selected again, or it clicked, nothing selected): edited.
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="excerpt-edit-text")
    assert user.find(marker="excerpt-edit-text").elements.pop().value == e.text
    user.find("Cancel").click()
    await user.should_not_see(marker="excerpt-edit-text")
    selection["text"] = ""
    user.find(kind=ui.label, content=e.text).click()  # (selected in the side panel)
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="excerpt-edit-text")


async def test_tag_from_a_list_in_the_pdf(user: User, fake_viewer):
    """A list selected in the PDF (e.g. areas over numbered references): its papers found
    and tagged, as from a pasted list."""
    from sci_report_analyzer import annotations

    pid, period, ids = _person()
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "", "p": 3, "rects": [], "area": True}
    user.javascript_rules[re.compile(r"vrPdf\.selection\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-tag-list")
    user.find(marker="pdf-tag-list").click()
    await user.should_see("Select the list (or an area over it) in the PDF first")
    selection["text"] = (
        "1- Neural retrieval models for long documents. J. Doe. ECIR, 2022.\n"
        "2- Deep ranking models for search. J. Doe. SIGIR, 2021."
    )
    user.find(marker="pdf-tag-list").click()
    await user.should_see("2 of 2 items matched")  # (found at once)
    user.find(marker="reflist-apply").click()
    await user.should_see("“starred” put on 2 papers")
    rows = {r.id: r for r in await pubview.load_stats(pid)}
    starred = annotations.starred_tag_id()
    assert rows[ids["b"]].number_of(starred, period) == 1
    assert rows[ids["a"]].number_of(starred, period) == 2
    state = annotations.panel_state(pid)
    assert not state.get("tag_filter") and not state.get("list_filter")  # (the panel as it was)


async def test_last_category_proposed(user: User, fake_viewer):
    """Filing an excerpt: the category of the one filed last proposed (marked; Enter, nothing
    typed, picks it), then the one chosen last."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    impact = categories.add(folder, "Impact")
    doc = documents.add(period, "Application.pdf", PDF)
    categories.add_excerpt(impact, period, "Cited a lot.", 1, [], document_id=doc)
    assert categories.last_category(period) == impact
    selection = {"text": "Our ranking model beats sparse ones", "p": 1, "rects": []}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-last")
    user.find(marker="category-search").trigger("keydown.enter")
    await user.should_see("Added to Impact")
    # Another category chosen: proposed next.
    selection = {**selection, "text": "Ten papers at SIGIR."}
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{research}")
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    selection = {**selection, "text": "Two at ECIR."}
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-last")
    user.find(marker="category-search").trigger("keydown.enter")
    await user.should_see("Added to Research")
    assert sorted(x.category_id for x in categories.excerpts(period)) == sorted(
        [impact, impact, research, research]
    )


async def test_excerpts_in_the_viewer(user: User, fake_viewer):
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    selection = {
        "text": "Our ranking model beats sparse ones",
        "p": 1,
        "rects": [[72, 680, 300, 690]],
    }
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{research}")
    user.find(marker="excerpt-start").elements.pop().value = 2020  # (its properties)
    user.find(marker="excerpt-influence").click()
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    [e] = categories.excerpts(period)
    assert e.category_id == research and e.page == 1 and e.document_id == doc
    assert (e.start_year, e.end_year, e.influence) == (2020, None, True)
    assert e.colour == categories.tree(folder)[0].colour  # (its category's)
    await user.should_see(marker=f"category-dot-{research}")
    await user.should_see(marker=f"excerpt-{e.id}")
    # Another passage (the same one filed: edited instead), in a new category typed in the
    # picker: created (the picker stays open), then picked.
    selection = {**selection, "text": "Our ranking model beats sparse ones."}  # (not filed)
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-search")
    user.find(marker="category-search").type("Impact").trigger("keydown.enter")
    await user.should_see("Category “Impact” created")
    impact = categories.tree(folder)[1].id
    assert [n.name for n in categories.tree(folder)] == ["Research", "Impact"]
    await user.should_see(marker=f"pick-category-{impact}")
    user.find(marker=f"pick-category-{impact}").click()
    await user.should_see("Added to Impact")
    assert categories.last_category(period) == impact
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
    # Dragged onto the top of another one: before it, in its category.
    [other] = [x for x in categories.excerpts(period) if x.id != e.id]
    user.find(marker=f"excerpt-{other.id}").trigger("drop", {"id": e.id, "where": "before"})
    await user.should_see(marker=f"excerpt-{e.id}")
    assert [x.id for x in categories.excerpts(period)] == [e.id, other.id]
    assert {x.category_id for x in categories.excerpts(period)} == {other.category_id}
    # Dragged onto the heading of another category (here empty): first in it.
    user.find(marker=f"category-header-{impact}").trigger("drop", {"id": e.id})
    await user.should_see(marker=f"excerpt-{e.id}")
    assert next(x for x in categories.excerpts(period) if x.id == e.id).category_id == impact
    # Merged: its merge icon, then a click on the other one (confirmed).
    user.find(marker=f"excerpt-merge-{e.id}").click()
    await user.should_see(marker="excerpt-merge-cancel")
    user.find(content="Our ranking model beats sparse ones.").click()
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
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: {
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


async def test_excerpt_years_from_its_text(user: User, fake_viewer):
    """The years in a passage (e.g. "2026-32"): its years when added, taken out of its text
    when leading it (with a colon) or ending it; the clear button empties them (the text as
    selected again)."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "2026-32: Led the Quokka project", "p": 1, "rects": []}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker=f"pick-category-{research}")
    start, end = (user.find(marker=m).elements.pop() for m in ("excerpt-start", "excerpt-end"))
    assert (start.value, end.value) == (2026, 2032)
    await user.should_see("“Led the Quokka project”")
    # Cleared: no years, the text as selected.
    user.find(marker="excerpt-years-clear").click()
    assert (start.value, end.value) == (None, None)
    await user.should_see("“2026-32: Led the Quokka project”")
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    [e] = categories.excerpts(period)
    assert (e.text, e.start_year, e.end_year) == ("2026-32: Led the Quokka project", None, None)
    # Ending it (after a colon): taken out too; kept, they are its years.
    selection["text"] = "Led the Wombat network: 2018-22."
    user.find(marker="pdf-excerpt").click()
    await user.should_see("“Led the Wombat network.”")
    user.find(marker=f"pick-category-{research}").click()
    await user.should_see("Added to Research")
    [e2] = [x for x in categories.excerpts(period) if x.id != e.id]
    assert (e2.text, e2.start_year, e2.end_year) == ("Led the Wombat network.", 2018, 2022)


async def test_categories_editor(user: User, fake_viewer):
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
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
    # A colour (that of its excerpts), picked.
    user.find(marker=f"category-colour-pick-{teaching}").trigger("click", "#2196f3")
    assert categories.tree(folder)[0].colour == "#2196f3"
    # Merged into another one (not within it): its excerpts, texts and subcategories there.
    extra = categories.add(folder, "Extra")
    sub = categories.add(folder, "Sub", extra)
    categories.add_excerpt(extra, period, "Filed in extra.", 1, [], document_id=doc)
    categories.set_section_text(period, extra, "About extra.")
    categories.set_key(extra, "extra")
    assert not categories.merge(extra, sub)
    user.find(marker=f"category-colour-pick-{teaching}").trigger("click", "#4caf50")  # (listed)
    await user.should_see(marker=f"category-merge-{extra}")
    user.find(marker=f"category-merge-{extra}").click()
    await user.should_see(marker="category-merge-target")
    user.find(marker="category-merge-target").elements.pop().value = teaching
    user.find(marker="category-merge-ok").click()
    await user.should_not_see(marker=f"category-{extra}")
    assert [n.path for n in categories.tree(folder)] == [
        "Teaching",
        "Teaching › Sub",
        "Research",
        "Research › New",
    ]
    assert [e.category_id for e in categories.excerpts(period)] == [teaching]
    assert categories.section_texts(period) == {teaching: "About extra."}
    assert categories.tree(folder)[0].key == "extra"
    categories.set_key(teaching, None)
    categories.remove_excerpt(categories.excerpts(period)[0].id)
    categories.set_section_text(period, teaching, "")
    assert categories.delete(sub)
    # Its id in the reports ([[excerpts#id]]), unique.
    user.find(marker=f"category-key-{teaching}").clear().type("Teaching duties").trigger("blur")
    assert categories.tree(folder)[0].key == "Teaching-duties"
    user.find(marker=f"category-key-{research}").type("Teaching-duties").trigger("blur")
    await user.should_see("Another category has this id")
    assert categories.tree(folder)[1].key is None
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


async def test_split_scroll_sync_installed(user: User, monkeypatch, fake_viewer):
    from nicegui import Client

    scripts = []
    run = Client.run_javascript
    monkeypatch.setattr(
        Client,
        "run_javascript",
        lambda self, code, **kw: (scripts.append(code), run(self, code))[1],
    )
    _, period, _ids = _person()
    doc = documents.add(period, "Application.pdf", PDF)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="doc-note")
    sync = [c for c in scripts if "follow(p, e)" in c]  # (the preview drives the editor too)
    assert sync and "requestAnimationFrame" in sync[0]


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

        def rebase(self, text):
            seen.append(f"rebased: {text}")

    clean, saver, same = Fake("old"), Fake("old"), Fake("new")
    folder_notes._editors()[folder] = {clean, saver, same}  # (as the registry's sets)
    folder_notes.saved(folder, "new", saver)
    # The other ones only (not the saver); the one up to date: as is, but saving from there.
    assert sorted(seen) == ["new", "rebased: new"]


def _page_event(user: User, name: str, args) -> None:
    """An event of the page (ui.on: ``name`` camel-cased), as sent by its JavaScript."""
    layout = user.client.layout
    [lid] = [i for i, x in layout._event_listeners.items() if x.type == name]
    layout._handle_event({"listener_id": lid, "args": args})


async def test_side_panel_in_another_window(user: User, monkeypatch, fake_viewer):
    """The side panel in its own window (the pane, its PDF window's token in the URL): its
    tabs without the PDF, its questions to the PDF (the selection) as in the PDF window."""
    _, period, ids = _person()
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "Our ranking model beats sparse ones", "p": 1}
    user.javascript_rules[re.compile(r"vrPdf\.quoted\(\)")] = lambda _: selection
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert_block", lambda self, text: inserted.append(text))
    await user.open(f"/doc/{doc}?pane=k3y9token")
    await user.should_see(marker="pane-back")
    await user.should_see(marker="doc-note")
    await user.should_see(marker="folder-note")
    await user.should_not_see(marker="pdf-frame")
    await user.should_not_see(marker="pdf-pane")
    user.find(marker="note-quote").click()  # (the folder's first: names its source)
    for _i in range(50):
        if inserted:
            break
        await asyncio.sleep(0.02)
    assert inserted == [
        f"> Our ranking model beats sparse ones ([Application, p. 1](/doc/{doc}?page=1))"
    ]
    # A paper's: its notes (the period's too).
    pdfs.save(ids["a"], PDF, None)
    await user.open(f"/pdf/{ids['a']}?period={period}&pane=k3y9token")
    await user.should_see(marker="period-note")
    await user.should_not_see(marker="pdf-frame")
    # Not a token: the PDF window itself.
    await user.open(f"/doc/{doc}?pane=<script>")
    await user.should_see(marker="pdf-frame")
    await user.should_see(marker="pdf-pane")


async def test_side_panel_detached_and_back(user: User, monkeypatch, fake_viewer):
    """While the side panel is in another window: hidden in the PDF window, the header's
    actions sent there; back, up to date (the note edited there)."""
    from nicegui import Client

    scripts = []
    run = Client.run_javascript
    monkeypatch.setattr(
        Client,
        "run_javascript",
        lambda self, code, **kw: (scripts.append(code), run(self, code))[1],
    )
    _, period, _ids = _person()
    doc = documents.add(period, "Application.pdf", PDF)
    user.javascript_rules[re.compile(r"vrPdf\.location\(\)")] = lambda _: {
        "p": 2,
        "y": None,
        "text": "",
    }
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="doc-note")
    [side] = user.find(marker="pdf-side").elements
    _page_event(user, "vrPane", True)
    for _i in range(50):
        if not side.visible:
            break
        await asyncio.sleep(0.02)
    assert not side.visible
    user.find(marker="pdf-bookmark").click()  # (in the pane: sent there)
    for _i in range(50):
        if any("vrPane.emit" in c for c in scripts):
            break
        await asyncio.sleep(0.02)
    assert 'vrPane.emit("vr-pdf-bookmark")' in scripts
    assert documents.bookmarks("doc", doc) == []
    documents.set_note(doc, "Edited in the pane")
    _page_event(user, "vrPane", False)
    for _i in range(50):
        if side.visible and user.find(marker="doc-note").elements.pop().value.strip():
            break
        await asyncio.sleep(0.02)
    assert user.find(marker="doc-note").elements.pop().value == "Edited in the pane"
    user.find(marker="pdf-bookmark").click()  # (here again)
    await user.should_see(marker="bookmark-0")


async def test_excerpt_selected(user: User, monkeypatch, fake_viewer):
    """An excerpt clicked on the PDF (its tint): highlighted there and in the categories' tab
    (shown); a click elsewhere: none. Its entry clicked: the same, on the PDF too."""
    from nicegui import Client

    from sci_report_analyzer import categories

    scripts = []
    run = Client.run_javascript
    monkeypatch.setattr(
        Client,
        "run_javascript",
        lambda self, code, **kw: (scripts.append(code), run(self, code))[1],
    )
    _, period, _ = _person()
    research = categories.add(folders.folders()[0].id, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    first = categories.add_excerpt(
        research, period, "Our ranking model.", 1, [[72, 680, 300, 690]], document_id=doc
    )
    second = categories.add_excerpt(
        research, period, "Beats sparse ones.", 2, [[72, 600, 300, 610]], document_id=doc
    )
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"excerpt-part-{first}")
    assert {t["id"] for t in categories.tints(period, doc, {})} == {first, second}
    [tabs] = [  # (the side column's: not those of the person's header)
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.tabs) and "inline-label" not in e.props
    ]
    tabs.value = "notes"

    def on() -> list[int]:
        return [
            i
            for i in (first, second)
            if "vr-excerpt-on" in user.find(marker=f"excerpt-part-{i}").elements.pop().classes
        ]

    _page_event(user, "vrDocExcerpt", {"id": second})
    assert on() == [second] and tabs.value == "categories"
    assert any(c.startswith(f"vrDoc.select({second});") for c in scripts)
    _page_event(user, "vrDocExcerpt", {"id": None})  # (a click elsewhere)
    assert on() == []
    user.find(content="Our ranking model.").click()  # (its entry)
    assert on() == [first]
    assert scripts[-1].startswith(f"vrDoc.select({first});")


def test_excerpt_discarded():
    """An excerpt discarded: left out of the generated text (its section too, without any
    other), struck through in the section's excerpts for reference; put back."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    teaching = categories.add(folder, "Teaching")
    doc = documents.add(period, "Dossier.pdf", PDF)
    kept = categories.add_excerpt(research, period, "Ten papers.", 1, [], document_id=doc)
    out = categories.add_excerpt(research, period, "A poster.", 2, [], document_id=doc)
    alone = categories.add_excerpt(teaching, period, "Two courses.", 3, [], document_id=doc)
    categories.set_discarded(out, True)
    categories.set_discarded(alone, True)
    text = categories.markdown(folder, period)
    assert "Ten papers." in text and "A poster." not in text and "Teaching" not in text
    assert categories.section_excerpts(period, research) == (
        "- Ten papers. (Dossier, p. 1)\n- ~~A poster. (Dossier, p. 2)~~"
    )
    categories.set_section_text(period, research, "Summary.", summary_only=True)
    assert "A poster." not in categories.markdown(folder, period)
    # A group's lead removed: the next one leads it, discarded too.
    categories.merge_excerpts(kept, out)
    categories.remove_excerpt(out)
    assert [e.discarded for e in categories.excerpts(period) if e.id == kept] == [True]
    categories.set_discarded(kept, False)
    categories.set_discarded(alone, False)
    assert "Two courses." in categories.markdown(folder, period)


def test_excerpt_comment():
    """A commentary on an excerpt (Markdown; a group's: its lead's): an Obsidian comment
    after its item in the notes (over lines: indented as the item's text), none in a
    section's excerpts (in a comment already); merged, both kept; the lead removed, passed
    on."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Dossier.pdf", PDF)
    a = categories.add_excerpt(research, period, "Ten papers.", 1, [], document_id=doc)
    b = categories.add_excerpt(research, period, "A prize.", 2, [], document_id=doc)
    categories.set_comment(a, "  Mostly *workshops*.\n\nAsk them.  ")
    assert categories.excerpts(period)[0].comment == "Mostly *workshops*.\n\nAsk them."
    assert categories.markdown(folder, period) == (
        "## Research\n\n- Ten papers. %% Dossier, p. 1 %% %% Mostly *workshops*.\n\n"
        "  Ask them. %%\n- A prize. %% Dossier, p. 2 %%\n"
    )
    assert "workshops" not in categories.section_excerpts(period, research)
    assert [v.id for v in categories.similar_excerpts(period, "x", "workshops")] == [a]
    categories.set_comment(b, "Minor.")
    assert categories.merge_excerpts(a, b) is None
    [g] = categories.excerpts(period, grouped=True)
    assert (g.id, g.comment, g.members[0].comment) == (
        b,
        "Minor.\n\nMostly *workshops*.\n\nAsk them.",
        None,
    )
    categories.remove_excerpt(b)
    assert categories.excerpts(period)[0].comment.startswith("Minor.")
    categories.set_comment(a, " ")
    assert categories.excerpts(period)[0].comment is None


async def test_excerpt_comment_in_the_viewer(user: User, fake_viewer):
    """An excerpt's commentary edited from its row (Markdown), shown under it, searched."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    x = categories.add_excerpt(research, period, "Ten papers.", 1, [], document_id=doc)
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"excerpt-comment-{x}")
    await user.should_not_see(marker=f"excerpt-comment-text-{x}")
    user.find(marker=f"excerpt-comment-{x}").click()
    await user.should_see(marker="excerpt-comment-input")
    user.find(marker="excerpt-comment-input").elements.pop().value = "Mostly *Zorglub*."
    user.find(marker="excerpt-url").type("https://example.org/zorglub")
    user.find(marker="excerpt-edit-save").click()
    await user.should_see(marker=f"excerpt-comment-text-{x}")
    await user.should_see(marker=f"excerpt-url-{x}")
    e = categories.excerpts(period)[0]
    assert (e.comment, e.url) == ("Mostly *Zorglub*.", "https://example.org/zorglub")
    assert (
        "Ten papers. %% Application, p. 1 %% ([link](https://example.org/zorglub))"
        " %% Mostly *Zorglub*. %%" in categories.markdown(folder, period)
    )
    user.find(marker="excerpts-search").type("zorglub")
    await user.should_see(marker=f"excerpt-{x}")
    # Moved (its tab of the same dialog): what was edited saved first.
    teaching = categories.add(folder, "Teaching")
    user.find(marker=f"excerpt-move-{x}").click()
    await user.should_see(marker=f"pick-category-{teaching}")
    user.find(marker="excerpt-tab-text").click()
    await user.should_see(marker="excerpt-minor")
    user.find(marker="excerpt-minor").click()
    user.find(marker="excerpt-tab-move").click()
    user.find(marker=f"pick-category-{teaching}").click()
    await user.should_not_see(marker="excerpt-edit-save")
    e = categories.excerpts(period)[0]
    assert (e.category_id, e.minor, e.comment) == (teaching, True, "Mostly *Zorglub*.")
    assert "- #minor Ten papers." in categories.markdown(folder, period)


async def test_insert_block_of_a_category(user: User, monkeypatch, fake_viewer):
    """The menu of blocks: also those of the categories with an id, as they are when
    opened."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    doc = documents.add(period, "Application.pdf", PDF)
    inserted = []
    monkeypatch.setattr(MarkdownEditor, "insert_block", lambda self, text: inserted.append(text))
    await user.open(f"/doc/{doc}")
    await user.should_see(marker="folder-note-insert")
    categories.set_key(research, "research")
    [button] = user.find(marker="folder-note-insert").elements
    [menu] = [c for c in button.default_slot.children if isinstance(c, ui.menu)]
    for listener in menu._event_listeners.values():  # (opened: as they are now)
        if listener.type == "beforeShow":
            listener.handler()
    menu.open()
    await user.should_see(marker="folder-note-excerpts-research")
    user.find(marker="folder-note-excerpts-research").click()
    user.find(marker="folder-note-influence").click()
    assert inserted == ["[[excerpts#research]]", "[[influence]]"]


async def test_excerpts_collapsed_searched_discarded(user: User, fake_viewer):
    """In the categories' tab: a category collapsed (its excerpts and subcategories hidden;
    sticky), all of them collapsed or expanded; the excerpts searched (their text, a
    section's text); an excerpt selected on the PDF, hidden: shown; one discarded, back."""
    from sci_report_analyzer import categories

    _, period, _ = _person()
    folder = folders.folders()[0].id
    research = categories.add(folder, "Research")
    projects = categories.add(folder, "Projects", research)
    teaching = categories.add(folder, "Teaching")
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    paper = categories.add_excerpt(
        research, period, "Ten papers.", 1, [[72, 680, 300, 690]], document_id=doc
    )
    nebula = categories.add_excerpt(projects, period, "Led Nebula.", 1, [], document_id=doc)
    course = categories.add_excerpt(teaching, period, "Two courses.", 2, [], document_id=doc)
    categories.set_section_text(period, teaching, "Taught **Zorglub** studies.")
    await user.open(f"/doc/{doc}")
    await user.should_see(marker=f"excerpt-{nebula}")
    user.find(marker=f"category-toggle-{research}").click()
    await user.should_not_see(marker=f"excerpt-{paper}")
    await user.should_not_see(marker=f"category-header-{projects}")
    await user.should_see(marker=f"excerpt-{course}")
    assert annotations.ui_state("ui.excerpts.collapsed") == [research]
    user.find(marker="categories-expand-all").click()
    await user.should_see(marker=f"excerpt-{nebula}")
    user.find(marker="categories-collapse-all").click()
    await user.should_not_see(marker=f"excerpt-{course}")
    await user.should_see(marker=f"category-header-{teaching}")
    # Selected on the PDF while hidden: its categories expanded.
    _page_event(user, "vrDocExcerpt", {"id": paper})
    await user.should_see(marker=f"excerpt-part-{paper}")
    await user.should_not_see(marker=f"excerpt-{course}")
    user.find(marker="categories-expand-all").click()
    # Searched: only the excerpts matching (and their categories, those above them).
    user.find(marker="excerpts-search").type("nebula")
    await user.should_not_see(marker=f"excerpt-{paper}")
    await user.should_see(marker=f"excerpt-{nebula}")
    await user.should_see(marker=f"category-header-{research}")
    await user.should_not_see(marker=f"category-header-{teaching}")
    user.find(marker="excerpts-search").clear().type("zorglub")  # (a section's text)
    await user.should_see(marker=f"category-header-{teaching}")
    await user.should_not_see(marker=f"excerpt-{course}")
    user.find(marker="excerpts-search").clear().type("nothing like it")
    await user.should_see(marker="excerpts-search-none")
    user.find(marker="excerpts-search").clear()
    await user.should_see(marker=f"excerpt-{course}")
    # Discarded: left out of the generated text, still listed; back.
    user.find(marker=f"excerpt-discard-{course}").click()
    await user.should_see(marker=f"excerpt-discarded-{course}")
    assert "Two courses." not in categories.markdown(folder, period)
    user.find(marker=f"excerpt-discard-{course}").click()
    await user.should_not_see(marker=f"excerpt-discarded-{course}")
    assert "Two courses." in categories.markdown(folder, period)
