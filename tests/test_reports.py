"""Reports: citation keys, numbers, substitution, and the report page."""

import asyncio

import pytest
from helpers import add_source, make_person, pub
from nicegui.testing import User

from sci_report_analyzer import annotations, folders, pubview, reports
from sci_report_analyzer.ui.mdedit import MarkdownEditor

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _setup():
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Deep ranking for search", 2021, "SIGIR", authors=["Jane Doe"]),
            pub("b", "The neural retrieval", 2022, "ECIR", authors=["Ann Smith", "Jane Doe"]),
            pub("c", "Deep ranking again", 2021, "SIGIR", authors=["Jane Doe"]),
        ],
    )
    fid = folders.save_folder(None, "Hiring")
    period = folders.add_person(fid, pid)
    return pid, period


def test_keys_numbers_and_substitution():
    pid, period = _setup()
    stats = asyncio.run(pubview.load_stats(pid))
    by = {s.title: s for s in stats}
    keys = reports.citation_keys(stats)
    a, b, c = (
        by[t].id for t in ("Deep ranking for search", "The neural retrieval", "Deep ranking again")
    )
    assert keys[b] == "smith2022neural"
    assert {keys[a], keys[c]} == {"doe2021deep", "doe2021deepa"}
    tag = annotations.save_tag("discuss")
    annotations.toggle_tag(a, tag)
    annotations.toggle_tag(b, tag)
    annotations.set_note(a, "Strong *results*.\n\nSecond paragraph.")
    stats = asyncio.run(pubview.load_stats(pid))
    papers = reports.report_papers(stats, keys, [tag], period)
    assert [(p.key, p.number) for p in papers] == [("smith2022neural", 1), (keys[a], 2)]
    ctx = reports.Context(papers, stats, keys, {tag: "discuss"}, {tag}, period)
    text = (
        f"See [@smith2022neural] and [see @smith2022neural; @{keys[a]}, p. 3].\n"
        "In-text: @smith2022neural. Mail: x@smith2022neural.org `[@smith2022neural]`\n"
        f"- [@{keys[a]}]{{.notes}}\n"
        f"Other: [@{keys[c]}] and [@nobody]."
    )
    r = reports.render(text, ctx)
    lines = r.text.splitlines()
    assert lines[0] == "See **#1** and see **#1**, **#2**, p. 3."
    assert lines[1].startswith("In-text: **#1** **The neural retrieval** · *ECIR* · 2022")
    assert "x@smith2022neural.org `[@smith2022neural]`" in lines[1]  # (mail, code: kept)
    assert lines[2].startswith("- **#2** **Deep ranking for search**")
    assert lines[4] == "  Strong *results*." and lines[6] == "  Second paragraph."
    assert lines[-1] == "Other: **#3** and [@nobody]."  # a paper without the tag: next number
    assert r.unknown == ["nobody"]
    assert r.cited["smith2022neural"] == 3
    assert reports.uncited(ctx, r.cited) == []
    # Templates (parsed: a group without a value is dropped, an unclosed one left as is).
    assert reports.render("[@smith2022neural]{.short-venue (.year)}", ctx).text == "ECIR (2022)"
    out = reports.render(f"[@smith2022neural; @{keys[a]}]{{.number .year}}", ctx).text
    assert out == "**#1** 2022, **#2** 2021"
    assert reports.render("[@smith2022neural]{#.index}", ctx).text == "#1"
    tpl = "{**#.index** (.short-venue .year): .notes}"
    out = reports.render(f"- [@{keys[a]}]{tpl}", ctx).text
    assert out == "- **#2** (SIGIR 2021): Strong *results*.\n\n  Second paragraph."
    assert reports.render(f"[@smith2022neural]{tpl}", ctx).text == "**#1** (ECIR 2022)"
    assert reports.render("[@smith2022neural]{.title ()}", ctx).text == "The neural retrieval"
    assert reports.render("[@smith2022neural]{(.year}", ctx).text == "[@smith2022neural]{(.year}"
    # Tags (not the report's), a number format, the list of papers.
    other = annotations.save_tag("strong")
    annotations.toggle_tag(a, other)
    stats = asyncio.run(pubview.load_stats(pid))
    papers = reports.report_papers(stats, keys, [tag], period)
    names = {tag: "discuss", other: "strong"}
    ctx = reports.Context(papers, stats, keys, names, {tag}, period, number_format="[{n}]")
    out = reports.render(f"[@{keys[a]}]{{.tags}}", ctx).text
    assert out.startswith("[2] **Deep ranking for search**") and out.endswith("#strong")
    assert "#discuss" not in out
    assert reports.uncited(ctx, reports.render("", ctx).cited) == papers
    assert reports.bibliography(ctx).splitlines()[0].startswith("- [1] **The neural")
    # Without tags: the papers of the period's years.
    assert len(reports.report_papers(stats, keys, [], period, (2022, None))) == 1
    # Numbers of a tag put from a list.
    annotations.tag_numbered(tag, {a: 3, b: 7})
    stats = asyncio.run(pubview.load_stats(pid))
    papers = reports.report_papers(stats, keys, [tag], period)
    assert [p.number for p in papers] == [3, 7]
    # With tags: the papers of other years are off-period.
    papers = reports.report_papers(stats, keys, [tag], period, (2022, None))
    assert [(p.key, p.off_period) for p in papers] == [(keys[a], True), ("smith2022neural", False)]


async def test_report_page(user: User, monkeypatch):
    pid, period = _setup()
    stats = await pubview.load_stats(pid)
    by = {s.title: s for s in stats}
    keys = reports.citation_keys(stats)
    tag = annotations.save_tag("discuss")
    a = by["Deep ranking for search"].id
    annotations.toggle_tag(a, tag)
    reports.save(period, tag_ids=[tag])
    await user.open(f"/person/{pid}?period={period}")
    await user.should_see(marker="report")
    await user.open(f"/report/{period}")
    await user.should_see(marker="report-text")
    await user.should_see("1 papers · 1 not discussed")
    await user.should_see(marker=f"report-cite-{keys[a]}")
    editor = user.find(marker="report-text").elements.pop()
    editor.value = f"Good: [@{keys[a]}]."
    await user.should_see("1 papers · 0 not discussed")
    await user.should_see("Good: **#1**.")  # the preview
    # The quick search, among the person's other papers too.
    user.find(marker="report-search").type("neural")
    await user.should_see(marker=f"report-cite-{keys[by['The neural retrieval'].id]}")
    for _ in range(50):  # (saved every 2 s)
        if reports.get(period).text:
            break
        await asyncio.sleep(0.1)
    assert reports.get(period).text == f"Good: [@{keys[a]}]."
    assert reports.get(period).tag_ids == [tag]
    # The citation inserted on a click: as chosen (the default template, saved).
    assert reports.load_templates().default == "{**#.index** (.short-venue .year)}"
    options = user.find(marker="report-cite-form").elements.pop().options
    assert options["{**#.index** (.short-venue .year)}"] == "Number, venue (year)\t#1 (SIGIR 2021)"
    user.find(marker="report-cite-form").elements.pop().value = "{.notes}"
    assert reports.load_templates().default == "{.notes}"
    user.find(marker="report-search").clear()
    await user.should_see(marker=f"report-cite-{keys[a]}")
    inserted = []  # (inserted by the browser: the call is checked)
    monkeypatch.setattr(MarkdownEditor, "insert", lambda self, text: inserted.append(text))
    user.find(marker=f"report-cite-{keys[a]}").click()
    assert inserted == [f"[@{keys[a]}]{{.notes}}"]


def test_report_templates():
    t = reports.load_templates()
    assert t.cite("k") == "[@k]{**#.index** (.short-venue .year)}"
    assert t.cite("k", "") == "[@k]"
    assert reports.check_template("{.year (}") and reports.check_template(".year")
    t.items.append(reports.Template("Year", "{.year}"))
    t.default = "{.year}"
    reports.save_templates(t)
    assert reports.load_templates().cite("k") == "[@k]{.year}"
    t.items.append(reports.Template("Bad", "{(.year}"))
    with pytest.raises(ValueError):
        reports.save_templates(t)


async def test_report_templates_settings(user: User):
    await user.open("/settings?tab=reports")
    await user.should_see(marker="report-template-0")
    user.find(marker="report-template-add").click()
    await user.should_see(marker="report-template-attrs-7")
    user.find(marker="report-template-attrs-7").elements.pop().value = "{.title}"
    user.find(marker="report-template-default-7").click()
    user.find(marker="report-templates-save").click()
    t = reports.load_templates()
    assert t.items[-1].attrs == "{.title}" and t.default == "{.title}"


async def test_report_page_shows_notes_and_follows_edits(user: User):
    pid, period = _setup()
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    a = next(s.id for s in stats if s.title == "Deep ranking for search")
    annotations.set_note(a, "A strong *paper*")
    await user.open(f"/report/{period}")
    await user.should_see(marker=f"report-note-{keys[a]}")
    user.find(marker="report-text").elements.pop().value = f"[@{keys[a]}]{{.notes}}"
    await user.should_see("A strong")
    # Edited elsewhere (e.g. in the PDF's window): the sidebar and the preview follow.
    annotations.set_note(a, "Its note, edited", period)
    for _ in range(50):
        if "Its note, edited" in user.find(marker=f"report-note-{keys[a]}").elements.pop().content:
            break
        await asyncio.sleep(0.1)
    await user.should_see("Its note, edited")


def test_notes_keep_line_breaks():
    import markdown2

    from sci_report_analyzer.ui.theme import NOTE_EXTRAS

    html = markdown2.markdown("one\ntwo\n\n```\nx\ny\n```", extras=NOTE_EXTRAS)
    assert "one<br />\ntwo" in html and "x\ny" in html  # (as in Obsidian; not in code)


async def test_report_page_off_period(user: User):
    from sci_report_analyzer.db.models import Period
    from sci_report_analyzer.db.session import session_scope

    pid, period = _setup()
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    tag = annotations.save_tag("discuss")
    for st in stats:
        annotations.toggle_tag(st.id, tag)
    reports.save(period, tag_ids=[tag])
    with session_scope() as s:
        s.get(Period, period).start_year = 2022
    await user.open(f"/report/{period}")
    await user.should_see("1 papers · 1 not discussed")
    await user.should_see("+ 2 off-period · 2 not discussed")
    await user.should_see(marker="report-off-period")
    old = next(st for st in stats if st.year == 2021)
    user.find(marker="report-text").elements.pop().value = f"[@{keys[old.id]}]"
    await user.should_see("+ 2 off-period · 1 not discussed")


def test_note_with_references():
    """A note (e.g. on a document) cites papers: numbered as first cited, then listed."""
    pid, _ = _setup()
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    ctx = reports.note_context(stats, keys)
    text = "Strong [@smith2022neural], see [@doe2021deep; @smith2022neural]; [@nobody]."
    out = reports.with_references(text, ctx).splitlines()
    assert out[0] == "Strong [1], see [2], [1]; [@nobody]."
    assert out[2] == "## References"
    assert out[4].startswith("- [1] **The neural retrieval** · *ECIR* · 2022")
    assert out[5].startswith("- [2] **Deep ranking for search**")
    assert len(out) == 6
    assert reports.with_references("No citation.", reports.note_context(stats, keys)) == (
        "No citation.\n"
    )
