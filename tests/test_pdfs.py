"""Stored PDFs: downloads, the merge, the clean-up, and the viewer page."""

import asyncio

import pytest
from helpers import PDF, add_source, make_person, note_status, pub
from nicegui import ElementFilter, ui
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import annotations, pdfs, pubview, sync
from sci_report_analyzer.db.models import Publication
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.sources.base import FetchResult
from sci_report_analyzer.ui import pdf_viewer

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _person() -> tuple[int, int, dict[str, int]]:
    pid = make_person("Jane Doe")
    link = add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Deep ranking for search", 2021, "SIGIR", pdf_url="https://example.org/a.pdf"),
            pub("b", "Neural retrieval models for long documents", 2022, "ECIR"),
            pub("c", "Sparse representations", 2023, "ICLR", doi="10.1/c"),
        ],
    )
    add_source(
        pid,
        "hal",
        "h/1",
        [
            pub(
                "hal-1",
                "Deep ranking for search",
                2021,
                "SIGIR",
                pdf_url="https://hal.science/hal-1/document",
            )
        ],
    )
    with session_scope() as s:
        pubs = s.scalars(select(Publication).where(Publication.person_id == pid))
        ids = {p.title: p.id for p in pubs}
    return pid, link, ids


def test_save_and_remove():
    pid, _, ids = _person()
    a = ids["Deep ranking for search"]
    with pytest.raises(pdfs.PdfError):
        pdfs.save(a, b"<html>a landing page</html>", "https://example.org")
    path = pdfs.save(a, PDF, "https://example.org/a.pdf")
    assert path.read_bytes() == PDF and path.name.startswith(f"{a}-Deep-ranking")
    assert pdfs.stored(pid) == {a: False}
    pdfs.save(a, PDF + b"%annotated", None, edited=True)
    assert pdfs.stored(pid) == {a: True} and pdfs.file_of(a) == path
    pdfs.remove(a)
    assert pdfs.stored(pid) == {} and not path.exists()


async def test_download_many(monkeypatch):
    pid, _, ids = _person()
    tried = []

    async def fetch(url):
        tried.append(url)
        if "hal.science" in url:
            return PDF
        raise pdfs.PdfError("not a PDF (a web page?)")

    async def unpaywall(doi):
        return [f"https://oa.example.org/{doi}.pdf"] if doi == "10.1/c" else []

    monkeypatch.setattr(pdfs, "fetch", fetch)
    monkeypatch.setattr(pdfs, "unpaywall", unpaywall)
    a, c = ids["Deep ranking for search"], ids["Sparse representations"]
    steps = []
    batch = await pdfs.download_many([a, c], lambda d, t: steps.append((d, t)))
    assert batch.done == [a]
    assert tried[0] == "https://hal.science/hal-1/document"  # the open archive's link first
    assert "https://oa.example.org/10.1/c.pdf" in tried  # then Unpaywall's, by its DOI
    assert set(batch.failed) == {c} and steps[-1] == (2, 2)
    rows = {r.id: r for r in await pubview.load_stats(pid)}
    assert rows[a].pdf == "stored" and rows[c].pdf is None
    again = await pdfs.download_many([a])
    assert again.skipped == 1 and not again.done


def _resync(link: int, pubs) -> None:
    sync.finish_link(link, FetchResult(publications=pubs))


def test_merge_and_cleanup():
    pid, link, ids = _person()
    a, b = ids["Deep ranking for search"], ids["Neural retrieval models for long documents"]
    c = ids["Sparse representations"]
    pa = pdfs.save(a, PDF, "https://example.org/a.pdf")  # still in a source (HAL)
    pb = pdfs.save(b, PDF, "https://example.org/b.pdf")  # downloaded: goes with its paper
    pc = pdfs.save(c, PDF, None)  # uploaded by hand: keeps its paper
    _resync(link, [pub("a", "Deep ranking for search", 2021, "SIGIR")])
    assert pdfs.file_of(a) == pa and pdfs.file_of(c) == pc
    assert pdfs.file_of(b) is None and pb.exists()  # the paper is gone, its file left behind
    u = pdfs.usage()
    assert u.files == 2 and u.orphans == [pb]
    pc.unlink()  # deleted by hand
    assert pdfs.cleanup() == (1, 1)
    assert not pb.exists() and pdfs.stored(pid) == {a: False}


async def test_viewer_page(user: User, fake_viewer):
    pid, _, ids = _person()
    a = ids["Deep ranking for search"]

    await user.open(f"/pdf/{a}")
    await user.should_see("No PDF stored for this paper.")
    await user.should_see(marker="pdf-upload")

    pdfs.save(a, PDF, None)
    await user.open(f"/pdf/{a}")
    await user.should_see(marker="pdf-frame")
    assert "'enableComment', true" in user.client.head_html  # (PDF.js's comments, on)
    await user.should_see(marker="paper-note")  # its tags and notes, next to it
    user.find(marker="paper-new-tag").type("to read").trigger("keydown.enter")
    user.find(marker="paper-note").elements.pop().value = "Read section 3, $x^2$"
    await note_status(user, "paper-note")
    await user.should_see(kind=ui.markdown)
    rows = {r.id: r for r in await pubview.load_stats(pid)}
    tag = next(t for t in annotations.all_tags() if t.name == "to read")
    assert tag.id in rows[a].tags and rows[a].note == "Read section 3, $x^2$"
    [shown] = [m for m in user.find(ui.markdown).elements if "section 3" in m.content]
    assert "<math" in shown.props["innerHTML"]  # LaTeX, as MathML
    # Within a period: its per-period tags (starred) too.
    period = annotations.save_period(pid, "Report", 2018, 2024)
    await user.open(f"/pdf/{a}?period={period}")
    await user.should_see(marker="tag-starred")
    user.find(marker="tag-starred").click()
    rows = {r.id: r for r in await pubview.load_stats(pid)}
    assert annotations.starred_tag_id() in rows[a].tags_in(period)
    # Opened from a folder: no excerpts (filed from the documents, not the papers).
    from sci_report_analyzer import folders

    in_folder = folders.add_person(folders.save_folder(None, "Hiring"), pid)
    await user.open(f"/pdf/{a}?period={in_folder}")
    await user.should_see(marker="pdf-bookmark")
    await user.should_not_see(marker="pdf-excerpt")
    await user.should_not_see(marker="side-tab-categories")


async def test_viewer_restart_notice(user: User, monkeypatch, fake_viewer):
    """With --live-reload: a new version of the code, offered in the viewer's header too."""
    from sci_report_analyzer import livereload

    _, _, ids = _person()
    a = ids["Deep ranking for search"]
    pdfs.save(a, PDF, None)
    monkeypatch.setattr(livereload, "enabled", True)
    monkeypatch.setattr(livereload, "changed", {"ui/pdf_viewer.py"})
    await user.open(f"/pdf/{a}")
    await user.should_see(marker="pdf-frame")
    await user.should_see("New version available (1 file changed)")
    await user.should_see("Restart & reload")
    # The side panel in its own window: there too.
    await user.open(f"/pdf/{a}?pane=k3y9token")
    await user.should_see(marker="pane-back")
    await user.should_see("New version available (1 file changed)")


async def test_viewer_details(user: User, fake_viewer):
    _, _, ids = _person()
    a = ids["Deep ranking for search"]
    pdfs.save(a, PDF, None)

    await user.open(f"/pdf/{a}")
    user.find(marker="side-tab-details").click()
    await user.should_see(marker="pdf-pub-details")  # as in the publications panel
    await user.should_see(marker="hide-publication")
    await user.should_see(marker="tab-matching")
    await user.should_see("Sources")
    # Tags and notes: in their own tab; always there: no close button.
    await user.should_not_see(marker="tab-notes")
    await user.should_not_see(marker="details-close")
    user.find(marker="hide-publication").click()  # (stays, refreshed)
    await user.should_see("Unhide")
    with session_scope() as s:
        assert s.get(Publication, a).hidden


async def test_pdf_buttons(user: User, monkeypatch):
    pid, _, ids = _person()
    a, b = ids["Deep ranking for search"], ids["Neural retrieval models for long documents"]
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    await user.should_see(marker=f"get-pdf-{a}")  # an open-access link: to download
    await user.should_not_see(marker=f"get-pdf-{b}")  # none, and no DOI
    user.find(marker=f"get-pdf-{a}").click()
    await user.should_see("Download the PDF?")
    await user.should_see("https://hal.science/hal-1/document")
    pdfs.save(a, PDF, None)
    await user.open(f"/person/{pid}")
    await user.should_see(marker=f"view-pdf-{a}")


async def test_settings_cleanup(user: User):
    _, link, ids = _person()
    b = ids["Neural retrieval models for long documents"]
    pb = pdfs.save(b, PDF, "https://example.org/b.pdf")
    _resync(link, [pub("a", "Deep ranking for search", 2021, "SIGIR")])
    await user.open("/settings?tab=data")
    await user.should_see("1 files of papers no longer in the database")
    user.find(marker="pdfs-cleanup").click()
    await user.should_see("1 files deleted, 0 PDFs forgotten")
    assert not pb.exists()


async def _shown(user: User, marker: str) -> bool:
    """Whether ``marker`` shows up, letting background reloads run."""
    for _ in range(50):
        with user.client:
            if any(marker in e._markers for e in ElementFilter()):
                return True
        await asyncio.sleep(0.05)
    return False


async def test_viewer_changes_reach_the_panel(user: User):
    from sci_report_analyzer.ui import pdf_viewer

    pid, _, ids = _person()
    a = ids["Deep ranking for search"]
    await user.open(f"/person/{pid}")
    await user.should_see(marker=f"get-pdf-{a}")
    # (as from a viewer window: the PDF stored, then annotated)
    pdfs.save(a, PDF, None)
    pdf_viewer.changed(pid)
    assert await _shown(user, f"view-pdf-{a}")
    pdfs.save(a, PDF, None, edited=True)
    pdf_viewer.changed(pid)
    for _ in range(50):
        await asyncio.sleep(0.05)
        with user.client:
            tips = [e.text for e in ElementFilter(kind=ui.tooltip)]
        if "View the PDF (annotated)" in tips:
            break
    else:
        raise AssertionError("not shown as annotated")


async def test_viewer_side_width(user: User, fake_viewer):
    _, _, ids = _person()
    a = ids["Deep ranking for search"]
    pdfs.save(a, PDF, None)

    await user.open(f"/pdf/{a}")
    await user.should_see(marker="pdf-splitter")
    [side] = user.find(marker="pdf-side").elements
    assert side.style["width"] == f"{pdf_viewer.SIDE_DEFAULT}px"
    annotations.save_ui_state(pdf_viewer.SIDE_WIDTH, 512)  # (set by dragging the splitter)
    await user.open(f"/pdf/{a}")
    [side] = user.find(marker="pdf-side").elements
    assert side.style["width"] == "512px"
