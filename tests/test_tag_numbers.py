"""The papers' numbers in a star list (their place in it, within a period): set, cleared,
reordered; the tags have none."""

import asyncio

import pytest
from helpers import add_source, make_person, pub
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import annotations, folders, pubview, reports
from sci_report_analyzer.db.models import PeriodTag, Publication
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
    """The papers of the star list within the period, and their numbers."""
    with session_scope() as s:
        rows = s.scalars(
            select(PeriodTag).where(PeriodTag.tag_id == tag, PeriodTag.period_id == period)
        )
        return {r.publication_id: r.number for r in rows}


def test_set_clear_and_renumber():
    pid, period, (a, b, c) = _setup()
    tag = annotations.save_list(None, "discuss")
    annotations.toggle_tag(a, tag, period)
    annotations.toggle_tag(b, tag, period)
    annotations.set_tag_number(a, tag, 4, period)
    annotations.set_tag_number(c, tag, 2, period)  # (put in the list too)
    assert _numbers(pid, tag, period) == {a: 4, b: None, c: 2}
    # Their order: by number, then those without one (latest first).
    stats = asyncio.run(pubview.load_stats(pid))
    assert [s.id for s in pubview.tag_order(stats, tag, period)] == [c, a, b]
    annotations.set_tag_number(a, tag, None, period)
    assert _numbers(pid, tag, period) == {a: None, b: None, c: 2}
    annotations.number_in_order(tag, [b, a, c], period)
    assert _numbers(pid, tag, period) == {b: 1, a: 2, c: 3}
    annotations.clear_tag_numbers(tag, [a, b, c], period)
    assert _numbers(pid, tag, period) == {a: None, b: None, c: None}  # (still in it)
    # A tag: no number.
    with pytest.raises(ValueError, match="star list"):
        annotations.set_tag_number(a, annotations.save_tag("plain"), 1, period)


def test_star_lists_of_a_folder():
    """A folder's lists (its settings'), after those of every folder; the ★ button's the
    first; a name taken refused."""
    _pid, period, _ids = _setup()
    fid = folders.folder_of_period(period)[0]
    settings = folders.settings_of(fid)
    star = annotations.starred_tag_id()
    mine = annotations.save_list(settings, "shortlist", number_format="[{index}]", must_cite=True)
    other = folders.save_folder(None, "Other")
    theirs = annotations.save_list(folders.settings_of(other), "theirs")
    assert [t.id for t in annotations.star_lists(period)] == [star, mine]
    assert annotations.first_list(period).id == star
    annotations.order_lists([mine, star])
    assert [t.id for t in annotations.star_lists(period)] == [mine, star]
    assert theirs not in {t.id for t in annotations.star_lists(period)}
    assert [x.name for x in reports.star_lists(period)] == ["shortlist", "starred"]
    # A name: unique among the settings' lists, those of every folder and the tags.
    annotations.save_list(settings, "theirs")  # (another folder's: fine)
    for taken in ("shortlist", "starred"):
        with pytest.raises(ValueError):
            annotations.save_list(settings, taken)
    with pytest.raises(ValueError):
        annotations.save_tag("plain") and annotations.save_list(None, "plain")


def test_star_lists_follow_the_settings():
    """A folder getting its own settings: copies of its lists, its stars in them (numbers
    kept); back to its parent's: in its lists of the same names (copied there if missing)."""
    _pid, period, (a, b, _c) = _setup()
    fid = folders.folder_of_period(period)[0]
    top = folders.settings_of(fid)
    short = annotations.save_list(top, "shortlist", number_format="[{index}]")
    annotations.number_in_order(short, [b, a], period)
    sub = folders.save_folder(None, "Sub")
    folders.set_parent(sub, fid)
    folders.use_parent_settings(sub)
    inner = folders.add_person(sub, _pid)
    assert [t.name for t in annotations.star_lists(inner)] == ["starred", "shortlist"]
    annotations.toggle_tag(a, short, inner)
    folders.use_own_settings(sub)
    own = folders.settings_of(sub)
    lists = {t.name: t for t in annotations.star_lists(inner)}
    assert lists["shortlist"].id != short and lists["shortlist"].settings_id == own
    assert lists["shortlist"].number_format == "[{index}]"
    assert _numbers(_pid, lists["shortlist"].id, inner) == {a: None}
    assert _numbers(_pid, short, period) == {b: 1, a: 2}  # (the parent's: as they were)
    mine = annotations.save_list(own, "mine")
    annotations.toggle_tag(b, mine, inner)
    folders.use_parent_settings(sub)
    back = {t.name: t.id for t in annotations.star_lists(inner)}
    assert back["shortlist"] == short and _numbers(_pid, short, inner) == {a: None}
    assert _numbers(_pid, back["mine"], inner) == {b: None}  # (copied to the parent's)


def test_numbers_within_a_period():
    pid, period, (a, b, _c) = _setup()
    other = annotations.save_period(pid, "Other", 2018, 2024)
    star = annotations.starred_tag_id()
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


async def test_star_filter_apart_from_the_tags(user: User) -> None:
    """The star list filter and the tag filter both apply (earlier links: one filter)."""
    pid, period, (a, b, c) = _setup()
    star = annotations.starred_tag_id()
    for p in (a, b):
        annotations.toggle_tag(p, star, period)
    tag = annotations.save_tag("neural")
    annotations.put_tag(tag, [b, c])
    await user.open(f"/person/{pid}/{period}?tags={tag},{star}")
    await user.should_see(TITLES[1])  # starred and tagged
    await user.should_not_see(TITLES[0])  # starred only
    await user.should_not_see(TITLES[2])  # tagged only
    # The ★ toggle off: the tagged papers, starred or not.
    user.find("list-filter").click()
    await user.should_see(TITLES[2])
    await user.should_not_see(TITLES[0])
    state = annotations.panel_state(pid)
    assert state["tag_filter"] == [tag] and state["list_filter"] == []


async def test_a_toggle_per_star_list(user: User) -> None:
    """Several star lists: a ★ toggle each, the papers in one of those on."""
    pid, period, (a, b, _c) = _setup()
    star = annotations.starred_tag_id()
    short = annotations.save_list(None, "shortlist")
    annotations.toggle_tag(a, star, period)
    annotations.toggle_tag(b, short, period)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see(TITLES[2])
    user.find(f"list-filter-{short}").click()
    await user.should_see(TITLES[1])
    await user.should_not_see(TITLES[0])
    await user.should_not_see(TITLES[2])
    user.find(f"list-filter-{star}").click()
    await user.should_see(TITLES[0])
    await user.should_see(TITLES[1])
    await user.should_not_see(TITLES[2])
    assert annotations.panel_state(pid)["list_filter"] == [short, star]


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
    tag = annotations.save_list(None, "discuss")
    annotations.toggle_tag(a, tag, period)
    annotations.set_tag_number(b, tag, 3, period)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see(TITLES[0])
    # On the panel's row: its chip's number, Alt-clicked (a click: the row's, its details).
    user.find(f"pub-tag-number-{b}-{tag}").click()
    await user.should_not_see(marker=f"pub-tag-number-{b}-{tag}-input")
    user.find(f"pub-tag-number-{b}-{tag}").trigger("click", {"altKey": True})
    user.find(f"pub-tag-number-{b}-{tag}-input").clear().type("2").trigger("keydown.enter")
    assert _numbers(pid, tag, period)[b] == 2
    # In the paper's details: next to its tag.
    user.find(f"pub-{a}").click()
    user.find("tab-notes").click()
    await user.should_see(marker="tag-number-discuss")
    user.find("tag-number-discuss").trigger("click", {"altKey": True})
    user.find("tag-number-discuss-input").clear().type("#5").trigger("keydown.enter")
    assert _numbers(pid, tag, period) == {a: 5, b: 2}
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
    annotations.save_list(None, "starred", number_format="#{index}", must_cite=True, list_id=star)
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
