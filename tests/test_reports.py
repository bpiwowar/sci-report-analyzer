"""Citations: keys, numbers, substitution, templates, a folder's numbering and status."""

import asyncio

import pytest
from helpers import add_source, make_person, pub
from nicegui.testing import User

from sci_report_analyzer import annotations, folders, pubview, reports

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
    papers = reports.papers_to_discuss(stats, keys, [tag], period)
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
    papers = reports.papers_to_discuss(stats, keys, [tag], period)
    names = {tag: "discuss", other: "strong"}
    ctx = reports.Context(papers, stats, keys, names, {tag}, period, number_format="[{n}]")
    out = reports.render(f"[@{keys[a]}]{{.tags}}", ctx).text
    assert out.startswith("[2] **Deep ranking for search**") and out.endswith("#strong")
    assert "#discuss" not in out
    assert reports.uncited(ctx, reports.render("", ctx).cited) == papers
    assert reports.bibliography(ctx).splitlines()[0].startswith("- [1] **The neural")
    # Without tags: the papers of the period's years.
    assert len(reports.papers_to_discuss(stats, keys, [], period, (2022, None))) == 1
    # Numbers of a tag put from a list.
    annotations.tag_numbered(tag, {a: 3, b: 7})
    stats = asyncio.run(pubview.load_stats(pid))
    papers = reports.papers_to_discuss(stats, keys, [tag], period)
    assert [p.number for p in papers] == [3, 7]
    # With tags: the papers of other years are off-period.
    papers = reports.papers_to_discuss(stats, keys, [tag], period, (2022, None))
    assert [(p.key, p.off_period) for p in papers] == [(keys[a], True), ("smith2022neural", False)]


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


def test_notes_keep_line_breaks():
    import markdown2

    from sci_report_analyzer.ui.theme import NOTE_EXTRAS

    html = markdown2.markdown("one\ntwo\n\n```\nx\ny\n```", extras=NOTE_EXTRAS)
    assert "one<br />\ntwo" in html and "x\ny" in html  # (as in Obsidian; not in code)


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


def test_named_templates_within_others_and_cycles():
    pid, _ = _setup()
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    templates = {"starred": "{.index (.short-venue .year)}", "long": "{.starred: .title}"}
    ctx = reports.Context([], stats, keys, {}, templates=templates)
    assert reports.render("[@smith2022neural]{.starred}", ctx).text == "1 (ECIR 2022)"
    out = reports.render("[@smith2022neural]{.long}", ctx).text
    assert out == "1 (ECIR 2022): The neural retrieval"
    # A cycle: the citation left as is, with the error (not a crash).
    templates.update(a="{.b}", b="{x .a}")
    r = reports.render("[@smith2022neural]{.a} ok", ctx)
    assert r.text.startswith("[@smith2022neural]{.a} (⚠ ") and r.text.endswith(" ok")
    assert r.errors == ["Template cycle: .a → .b → .a"]
    assert reports.template_cycle(templates) == ("a", "b", "a")
    # Refused when saved: a cycle, a name twice, a reserved name.
    t = reports.load_templates()
    t.items.append(reports.Template("A", "{.b}", "a"))
    t.items.append(reports.Template("B", "{.a}", "b"))
    with pytest.raises(ValueError, match="cycle"):
        reports.save_templates(t)
    t.items[-1] = reports.Template("B", "{.year}", "a")
    with pytest.raises(ValueError, match="Two templates"):
        reports.save_templates(t)
    t.items[-1] = reports.Template("B", "{.year}", "year")
    with pytest.raises(ValueError, match="field"):
        reports.save_templates(t)
    t.items[-1] = reports.Template("B", "{.year}", ".b")
    reports.save_templates(t)
    assert reports.load_templates().names == {"a": "{.b}", "b": "{.year}"}


def test_folder_numbering_templates_and_status():
    pid, period = _setup()
    fid = folders.folder_of_period(period)[0]
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    by = {s.title: s.id for s in stats}
    a, b, c = (
        by[t] for t in ("Deep ranking for search", "The neural retrieval", "Deep ranking again")
    )
    # By default: numbered as first cited, "[1]"; the papers to discuss: the period's.
    ctx = reports.folder_context(stats, period)
    r = reports.render(f"[@{keys[b]}] [@{keys[a]}]", ctx)
    assert r.text == "[1] [2]"
    st = reports.citation_status(ctx, r.cited)
    assert len(st.discuss) == 3 and len(st.missing) == 1 and st.colour == "negative"
    # A numbered tag (within the period) and a format: its papers first, as listed.
    star = annotations.starred_tag_id()
    annotations.tag_numbered(star, {a: 1, b: 2}, period)
    reports.save_numbering(fid, reports.Numbering(star, "**#{index}**"))
    stats = asyncio.run(pubview.load_stats(pid))
    ctx = reports.folder_context(stats, period)
    r = reports.render(f"[@{keys[b]}] [@{keys[c]}]", ctx)
    assert r.text == "**#2** **#3**"
    st = reports.citation_status(ctx, r.cited)
    assert [p.key for p in st.missing] == [keys[a]] and [p.key for p in st.outside] == [keys[c]]
    r = reports.render(f"[@{keys[a]}; @{keys[b]}]", ctx)
    assert reports.citation_status(ctx, r.cited).colour == "positive"
    # The period's years: a paper with the tag of another year, cited, is out of the range.
    folders.set_period(period, 2022, None)
    ctx = reports.folder_context(stats, period)
    st = reports.citation_status(ctx, reports.render(f"[@{keys[a]}; @{keys[b]}]", ctx).cited)
    assert [p.key for p in st.off] == [keys[a]] and st.colour == "warning"
    # The folder's templates over the general ones (by name), within one another.
    t = reports.load_templates()
    t.items.append(reports.Template("Starred", "{.short-venue}", "starred"))
    reports.save_templates(t)
    reports.save_folder_templates(
        fid,
        [
            reports.Template("", "{.index (.short-venue .year)}", "starred"),
            reports.Template("", "{.starred: .title}", "long"),
        ],
    )
    ctx = reports.folder_context(stats, period)
    out = reports.render(f"[@{keys[b]}]{{.long}}", ctx).text
    assert out == "2 (ECIR 2022): The neural retrieval"
    assert reports.load_templates().names["starred"] == "{.short-venue}"
    with pytest.raises(ValueError, match="cycle"):
        reports.save_folder_templates(fid, [reports.Template("", "{.starred}", "starred")])
    with pytest.raises(ValueError, match="name"):
        reports.save_folder_templates(fid, [reports.Template("", "{.year}", "")])


async def test_folder_notes_citation_status(user: User, monkeypatch, tmp_path):
    from sci_report_analyzer import pdfs

    pid, period = _setup()
    viewer = tmp_path / "pdfjs"
    (viewer / "web").mkdir(parents=True)
    (viewer / "web" / "viewer.html").write_text("<html></html>")
    monkeypatch.setattr(pdfs, "viewer_dir", lambda: viewer)
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    a = next(s.id for s in stats if s.title == "Deep ranking for search")
    star = annotations.starred_tag_id()
    annotations.toggle_tag(a, star, period)
    fid = folders.folder_of_period(period)[0]
    reports.save_numbering(fid, reports.Numbering(star, "#{index}"))
    pdfs.save(a, b"%PDF-1.4\n%%EOF\n", None)
    await user.open(f"/pdf/{a}?period={period}")
    await user.should_see(marker="citation-status")
    await user.should_see("0 of the 1 papers to discuss cited")
    [box] = user.find(marker="citation-status").elements
    [icon] = [e for e in box.descendants() if e.tag == "q-icon"]
    assert icon.props["color"] == "negative"
    user.find(marker="folder-note").elements.pop().value = f"Good: [@{keys[a]}]."
    await user.should_see("1 of the 1 papers to discuss cited")
    assert icon.props["color"] == "positive"
    await user.should_see("Good: #1.")  # (the preview)
    # A click: the folder's numbering and templates.
    user.find(marker="citation-status").click()
    await user.should_see(marker="folder-number-tag")
    user.find(marker="folder-number-format").clear().type("[{index}]")
    user.find(marker="folder-citations-save").click()
    await user.should_see("Good: [1].")
    assert reports.numbering(fid) == reports.Numbering(star, "[{index}]")


def test_python_style_templates_converted_back():
    """The migration back to classes (from the Python-style fields, for a while)."""
    import importlib.util
    from pathlib import Path

    import sci_report_analyzer.db as db

    path = next(Path(db.__file__).parent.glob("migrations/versions/d4b2f8a6c1e3_*.py"))
    spec = importlib.util.spec_from_file_location("m", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    text = (
        "See [@a]{notes tags}, [@b]{**#{index}** (out – {short-venue} {year})}, "
        "[@c; @d]{starred}, [@e]{{starred}: {notes}}; kept: [@f]{.notes}, "
        "[@g]{**#.index** (.short-venue .year)}, [not a citation]{x}, @h, [@i]."
    )
    assert m.convert_text(text) == (
        "See [@a]{.notes .tags}, [@b]{**#.index** (out – .short-venue .year)}, "
        "[@c; @d]{.starred}, [@e]{.starred: .notes}; kept: [@f]{.notes}, "
        "[@g]{**#.index** (.short-venue .year)}, [not a citation]{x}, @h, [@i]."
    )
    assert m.convert_template("{**#{index}** ({short-venue} {year})}") == (
        "{**#.index** (.short-venue .year)}"
    )
    assert m.convert_template("{ **#.index** (.short-venue .year)}") == (
        "{ **#.index** (.short-venue .year)}"
    )
    assert m.convert_template("") == ""
