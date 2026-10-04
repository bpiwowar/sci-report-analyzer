"""The papers' numbers within a tag (their rank in its list): set, cleared, reordered."""

import asyncio

import pytest
from helpers import add_source, make_person, pub
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import annotations, folders, pubview, reports
from sci_report_analyzer.db.models import PeriodTag, Publication, PublicationTag, Tag
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ui.tags import moved

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

TITLES = ("Deep ranking for search", "The neural retrieval", "Deep ranking again")


def _setup():
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", TITLES[0], 2021, "SIGIR", authors=["Jane Doe"]),
            pub("b", TITLES[1], 2022, "ECIR", authors=["Ann Smith", "Jane Doe"]),
            pub("c", TITLES[2], 2020, "SIGIR", authors=["Jane Doe"]),
        ],
    )
    fid = folders.save_folder(None, "Hiring")
    period = folders.add_person(fid, pid)
    with session_scope() as s:
        by = dict(s.execute(select(Publication.title, Publication.id)).all())
    return pid, period, [by[t] for t in TITLES]


def _numbers(_pid: int, tag: int, period: int | None) -> dict[int, int | None]:
    """The papers having the tag (global, else within the period), and their numbers."""
    with session_scope() as s:
        if s.get(Tag, tag).per_period:
            rows = s.scalars(
                select(PeriodTag).where(PeriodTag.tag_id == tag, PeriodTag.period_id == period)
            )
        else:
            rows = s.scalars(select(PublicationTag).where(PublicationTag.tag_id == tag))
        return {r.publication_id: r.number for r in rows}


def test_set_clear_and_renumber():
    pid, _period, (a, b, c) = _setup()
    tag = annotations.save_tag("discuss")
    annotations.toggle_tag(a, tag)
    annotations.toggle_tag(b, tag)
    annotations.set_tag_number(a, tag, 4)
    annotations.set_tag_number(c, tag, 2)  # (the tag put on it too)
    assert _numbers(pid, tag, None) == {a: 4, b: None, c: 2}
    # Their order: by number, then those without one (latest first).
    stats = asyncio.run(pubview.load_stats(pid))
    assert [s.id for s in pubview.tag_order(stats, tag, None)] == [c, a, b]
    annotations.set_tag_number(a, tag, None)
    assert _numbers(pid, tag, None) == {a: None, b: None, c: 2}
    annotations.number_in_order(tag, [b, a, c])
    assert _numbers(pid, tag, None) == {b: 1, a: 2, c: 3}
    annotations.clear_tag_numbers(tag, [a, b, c])
    assert _numbers(pid, tag, None) == {a: None, b: None, c: None}  # (still tagged)


def test_numbers_within_a_period():
    pid, period, (a, b, _c) = _setup()
    other = annotations.save_period(pid, "Other", 2018, 2024)
    star = annotations.starred_tag_id()
    with pytest.raises(ValueError, match="period"):
        annotations.set_tag_number(a, star, 1)
    annotations.number_in_order(star, [b, a], period)
    annotations.set_tag_number(a, star, 5, other)
    assert _numbers(pid, star, period) == {b: 1, a: 2}
    assert _numbers(pid, star, other) == {a: 5}
    annotations.clear_tag_numbers(star, [a, b], period)
    assert _numbers(pid, star, period) == {a: None, b: None}
    assert _numbers(pid, star, other) == {a: 5}


def test_moved():
    assert moved([1, 2, 3], 3, 1, "before") == [3, 1, 2]
    assert moved([1, 2, 3], 1, 3, "after") == [2, 3, 1]
    assert moved([1, 2, 3], 1, 2, "before") == [1, 2, 3]
    assert moved([1, 2, 3], 4, 2) == [1, 2, 3]


async def test_order_dialog_from_the_panel(user: User) -> None:
    pid, period, (a, b, c) = _setup()
    star = annotations.starred_tag_id()
    for p in (a, b, c):
        annotations.toggle_tag(p, star, period)
    annotations.set_tag_number(c, star, 1, period)
    await user.open(f"/person/{pid}/{period}?tags={star}")
    await user.should_see(TITLES[0])
    user.find("tag-order").click()
    await user.should_see(marker=f"tag-order-row-{c}")
    # Numbered first, then by year: c (#1), b (2022), a (2021). Moving one numbers them all.
    user.find(f"tag-order-down-{c}").click()
    await asyncio.sleep(0.1)
    assert _numbers(pid, star, period) == {b: 1, c: 2, a: 3}
    # Dropped onto the top of another.
    user.find(f"tag-order-row-{c}").trigger("drop", {"id": a, "where": "before"})
    assert _numbers(pid, star, period) == {b: 1, a: 2, c: 3}
    # A number typed (Escape: cancelled; empty: none).
    user.find(f"tag-order-number-{a}").trigger("click", {"altKey": True})
    user.find(f"tag-order-number-{a}-input").clear().type("7").trigger("keydown.escape")
    assert _numbers(pid, star, period)[a] == 2
    user.find(f"tag-order-number-{a}").trigger("click", {"altKey": True})
    user.find(f"tag-order-number-{a}-input").clear().type("7").trigger("keydown.enter")
    assert _numbers(pid, star, period) == {b: 1, a: 7, c: 3}
    user.find(f"tag-order-number-{c}").click()  # (here a plain click too)
    user.find(f"tag-order-number-{c}-input").clear().trigger("keydown.enter")
    assert _numbers(pid, star, period) == {b: 1, a: 7, c: None}
    user.find("tag-order-number-all").click()
    assert _numbers(pid, star, period) == {b: 1, a: 2, c: 3}
    user.find("tag-order-clear").click()
    assert _numbers(pid, star, period) == {a: None, b: None, c: None}


async def test_number_typed_on_a_paper(user: User) -> None:
    pid, period, (a, b, _c) = _setup()
    tag = annotations.save_tag("discuss")
    annotations.toggle_tag(a, tag)
    annotations.set_tag_number(b, tag, 3)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see(TITLES[0])
    # On the panel's row: its chip's number, Alt-clicked (a click: the row's, its details).
    user.find(f"pub-tag-number-{b}-{tag}").click()
    await user.should_not_see(marker=f"pub-tag-number-{b}-{tag}-input")
    user.find(f"pub-tag-number-{b}-{tag}").trigger("click", {"altKey": True})
    user.find(f"pub-tag-number-{b}-{tag}-input").clear().type("2").trigger("keydown.enter")
    assert _numbers(pid, tag, None)[b] == 2
    # In the paper's details: next to its tag.
    user.find(f"pub-{a}").click()
    user.find("tab-notes").click()
    await user.should_see(marker="tag-number-discuss")
    user.find("tag-number-discuss").trigger("click", {"altKey": True})
    user.find("tag-number-discuss-input").clear().type("#5").trigger("keydown.enter")
    assert _numbers(pid, tag, None) == {a: 5, b: 2}
    await user.should_see("#5", marker="tag-number-discuss")


async def test_reordered_beside_the_folder_notes(user: User, monkeypatch, tmp_path) -> None:
    from sci_report_analyzer import pdfs
    from sci_report_analyzer.ui.papers_pane import PapersPane

    pid, period, (a, b, c) = _setup()
    viewer = tmp_path / "pdfjs"
    (viewer / "web").mkdir(parents=True)
    (viewer / "web" / "viewer.html").write_text("<html></html>")
    monkeypatch.setattr(pdfs, "viewer_dir", lambda: viewer)
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    star = annotations.starred_tag_id()
    annotations.number_in_order(star, [a, b], period)
    fid = folders.folder_of_period(period)[0]
    reports.save_numbering(fid, reports.Numbering(star, "#{index}", "#{index}"))
    pdfs.save(a, b"%PDF-1.4\n%%EOF\n", None)
    cited: list[str] = []
    monkeypatch.setattr(PapersPane, "cite", lambda _self, key, _attrs=None: cited.append(key))
    await user.open(f"/pdf/{a}?period={period}")
    await user.should_see(marker="folder-note")
    user.find(marker="folder-note").elements.pop().value = f"First: [@{keys[a]}]."
    await user.should_see("First: #1.")  # (the preview)
    user.find(marker="folder-note-mode").elements.pop().value = "side"
    await user.should_see(marker=f"folder-papers-down-{keys[a]}")
    user.find(f"folder-papers-down-{keys[a]}").click()
    assert _numbers(pid, star, period) == {b: 1, a: 2}
    user.find(marker="folder-note-mode").elements.pop().value = "split"
    await user.should_see("First: #2.")
    # Its number: a click cites it, an Alt-click types it; dragged.
    user.find(marker="folder-note-mode").elements.pop().value = "side"
    await user.should_see(marker=f"folder-papers-cite-{keys[a]}")
    user.find(marker=f"folder-papers-cite-{keys[a]}").click()
    assert cited == [keys[a]]
    await user.should_not_see(marker=f"folder-papers-number-{a}-input")
    user.find(marker=f"folder-papers-cite-{keys[a]}").trigger("click", {"altKey": True})
    user.find(f"folder-papers-number-{a}-input").clear().type("9").trigger("keydown.enter")
    assert _numbers(pid, star, period) == {b: 1, a: 9}
    user.find(f"folder-papers-row-{keys[b]}").trigger("drop", {"id": a, "where": "before"})
    assert _numbers(pid, star, period) == {a: 1, b: 2}
    assert c not in _numbers(pid, star, period)
