"""Citations: keys, numbers, substitution, templates, a folder's numbering and status."""

import asyncio

import pytest
from helpers import PDF, add_source, make_person, pub, uncited
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


def test_keys_take_the_surname_whatever_the_name_order():
    from types import SimpleNamespace

    def key(author):
        s = SimpleNamespace(id=1, authors=[author], year=2020, title="Neural ranking")
        return reports.citation_keys([s])[1]

    assert key("Jane Doe") == key("Doe, Jane") == key("DOE Jane") == "doe2020neural"
    assert key("Hélène Névot") == "nevot2020neural"
    assert key("R.L. Kessler-Martin") == "kesslermartin2020neural"
    assert key("Zhang Wei (张伟)") == "wei2020neural"


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
    tag = annotations.save_list(None, "discuss")
    annotations.toggle_tag(a, tag, period)
    annotations.toggle_tag(b, tag, period)
    annotations.set_note(a, "Strong *results*.\n\nSecond paragraph.")
    stats = asyncio.run(pubview.load_stats(pid))
    lists = [reports.StarList(tag, "discuss")]
    papers = reports.listed_papers(stats, keys, lists, period)
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
    assert lines[-1] == "Other: **#3** and [@nobody]."  # (in no list, the same format: next)
    assert r.unknown == ["nobody"]
    assert r.cited["smith2022neural"] == 3
    assert uncited(ctx, r.cited) == []
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
    lists = [reports.StarList(tag, "discuss", "[{n}]")]
    papers = reports.listed_papers(stats, keys, lists, period)
    names = {tag: "discuss", other: "strong"}
    ctx = reports.Context(papers, stats, keys, names, {tag}, period, number_format="({n})")
    out = reports.render(f"[@{keys[a]}]{{.tags}}", ctx).text
    assert out.startswith("[2] **Deep ranking for search**") and out.endswith("#strong")
    assert "#discuss" not in out
    assert uncited(ctx, reports.render("", ctx).cited) == papers
    assert reports.bibliography(ctx).splitlines()[0].startswith("- [1] **The neural")
    # In no list, written otherwise: numbered apart, from 1, as first cited.
    assert reports.render(f"[@{keys[c]}]", ctx).text == "(1)"
    # How [@key] cites: as its list says, else as the papers cited only; a template given
    # instead.
    lists = [reports.StarList(tag, "discuss", "[{n}]", cite="{.number .year}")]
    papers = reports.listed_papers(stats, keys, lists, period)
    ctx = reports.Context(papers, stats, keys, names, {tag}, period, cite_default="{.title}")
    assert reports.render(f"[@smith2022neural; @{keys[c]}]", ctx).text == (
        "[1] 2022, Deep ranking again"
    )
    assert reports.render("[@smith2022neural]{.year}", ctx).text == "2022"
    # Without tags: the papers of the period's years.
    assert len(reports.papers_to_discuss(stats, keys, [], period, (2022, None))) == 1
    # Their numbers in the list (e.g. put from a pasted one); in the first list they are in.
    annotations.tag_numbered(tag, {a: 3, b: 7}, period)
    stats = asyncio.run(pubview.load_stats(pid))
    papers = reports.listed_papers(stats, keys, lists, period)
    assert [p.number for p in papers] == [3, 7]
    first = annotations.save_list(None, "first")
    annotations.toggle_tag(b, first, period)
    stats = asyncio.run(pubview.load_stats(pid))
    two = [reports.StarList(first, "first", "F{index}"), *lists]
    papers = reports.listed_papers(stats, keys, two, period)
    assert [(p.key, p.number, p.list_id) for p in papers] == [
        ("smith2022neural", 1, first),
        (keys[a], 3, tag),
    ]
    # The papers of other years are off-period.
    papers = reports.listed_papers(stats, keys, lists, period, (2022, None))
    assert [(p.key, p.off_period) for p in papers] == [(keys[a], True), ("smith2022neural", False)]


def test_report_templates():
    t = reports.load_templates()
    assert t.cite("k") == "[@k]"  # (as the folder cites)
    assert t.cite("k", "{.year}") == "[@k]{.year}"
    assert reports.check_template("{.year (}") and reports.check_template(".year")
    t.items.append(reports.Template("Year", "{.year}"))
    reports.save_templates(t)
    assert reports.load_templates().items[-1].attrs == "{.year}"
    t.items.append(reports.Template("Bad", "{(.year}"))
    with pytest.raises(ValueError):
        reports.save_templates(t)


async def test_report_templates_settings(user: User):
    await user.open("/settings?tab=reports")
    await user.should_see(marker="report-template-0")
    user.find(marker="report-template-add").click()
    await user.should_see(marker="report-template-attrs-7")
    user.find(marker="report-template-attrs-7").elements.pop().value = "{.title}"
    user.find(marker="report-templates-save").click()
    assert reports.load_templates().items[-1].attrs == "{.title}"


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


def test_references_placed():
    """[]{.references}: the papers cited listed there (with all of them, those cited after
    too), rather than at the end; not in code."""
    pid, _ = _setup()
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    text = "# Report\n\nSee [@smith2022neural].\n\n[]{.references}\n\n# After\n\n[@doe2021deep]\n"
    out = reports.with_references(text, reports.note_context(stats, keys))
    lines = out.splitlines()
    assert lines[4].startswith("- [1] **The neural retrieval**")
    assert lines[5].startswith("- [2] **Deep ranking for search**")
    assert lines[-1] == "[2]" and "References" not in out
    # In the preview too; with their notes, their tags (options).
    r = reports.render("[@doe2021deep]\n\n[]{.references .tags}", reports.note_context(stats, keys))
    assert r.references
    assert r.text == "[1]\n\n- [1] **Deep ranking for search** · *SIGIR* · 2021 · CORE A"
    # In code: as it is (the list at the end).
    code = "[@doe2021deep]\n\n```\n[]{.references}\n```\n"
    out = reports.with_references(code, reports.note_context(stats, keys))
    assert "```\n[]{.references}\n```" in out and "## References" in out


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
    # A star list to cite (its papers, numbered in it, are to discuss), with a format.
    star = annotations.starred_tag_id()
    annotations.tag_numbered(star, {a: 1, b: 2}, period)
    annotations.save_list(
        None, "starred", number_format="**#{index}**", must_cite=True, list_id=star
    )
    reports.save_numbering(fid, reports.Numbering("**#{index}**"))
    stats = asyncio.run(pubview.load_stats(pid))
    ctx = reports.folder_context(stats, period)
    r = reports.render(f"[@{keys[b]}] [@{keys[c]}]", ctx)
    assert r.text == "**#2** **#3**"  # (the others: the same format, numbered after them)
    st = reports.citation_status(ctx, r.cited)
    assert [p.key for p in st.missing] == [keys[a]] and [p.key for p in st.outside] == [keys[c]]
    assert st.colour == "negative"
    # Copied: the listed papers, then the others (from 1, as first cited), each in its format.
    reports.save_numbering(fid, reports.Numbering("[{index}]"))
    ctx = reports.folder_context(stats, period)
    out = reports.with_references(f"[@{keys[c]}] [@{keys[b]}]", ctx)
    assert out.startswith("[1] **#2**\n")
    refs = out.split("References")[1].strip().splitlines()
    assert refs[0].startswith("- **#1** **Deep ranking for search**")
    assert refs[1].startswith("- **#2** **The neural retrieval**")
    assert refs[2].startswith("- [1] **Deep ranking again**")
    assert reports.render(f"[@{keys[c]}]{{.index}}", ctx).text == "1"
    # How [@key] cites: as its list says (its citing format), else as the folder says for the
    # papers cited only.
    annotations.save_list(
        None,
        "starred",
        number_format="**#{index}**",
        cite_template="{.number (.year)}",
        must_cite=True,
        list_id=star,
    )
    reports.save_numbering(fid, reports.Numbering("[{index}]", "{.number: .title}"))
    ctx = reports.folder_context(stats, period)
    r = reports.render(f"[@{keys[a]}; @{keys[b]}] [@{keys[c]}]", ctx)
    assert r.text == "**#1** (2021), **#2** (2022) [1]: Deep ranking again"
    assert reports.citation_status(ctx, r.cited).colour == "positive"
    # A list not to cite: its papers numbered, not to discuss (none: the period's years).
    annotations.save_list(
        None,
        "starred",
        number_format="**#{index}**",
        cite_template="{.number (.year)}",
        list_id=star,
    )
    ctx = reports.folder_context(stats, period)
    assert reports.render(f"[@{keys[b]}]", ctx).text == "**#2** (2022)"
    assert len(reports.citation_status(ctx, r.cited).discuss) == 3
    annotations.save_list(
        None, "starred", number_format="**#{index}**", must_cite=True, list_id=star
    )
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


async def test_folder_notes_citation_status(user: User, fake_viewer):
    from sci_report_analyzer import pdfs

    pid, period = _setup()
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    a = next(s.id for s in stats if s.title == "Deep ranking for search")
    star = annotations.starred_tag_id()
    annotations.toggle_tag(a, star, period)
    fid = folders.folder_of_period(period)[0]
    annotations.save_list(None, "starred", number_format="#{index}", must_cite=True, list_id=star)
    reports.save_numbering(fid, reports.Numbering("#{index}"))
    pdfs.save(a, PDF, None)
    await user.open(f"/pdf/{a}?period={period}")
    # The hint on the editor's "Papers": coloured by whether the papers to discuss are cited.
    await user.should_see(marker="folder-note-mode")
    [mode] = user.find(marker="folder-note-mode").elements

    def side() -> dict:
        return dict(zip(mode._values, mode.props["options"], strict=True))["side"]

    async def hinted(colour: str, text: str) -> None:  # (once the papers are loaded)
        for _i in range(50):
            if side().get("textColor") == colour:
                break
            await asyncio.sleep(0.05)
        assert side()["textColor"] == colour and text in side()["attrs"]["title"]

    await hinted("negative", "0 of the 1 papers to discuss cited")
    user.find(marker="folder-note").elements.pop().value = f"Good: [@{keys[a]}]."
    await user.should_see("Good: #1.")  # (the preview)
    await hinted("positive", "1 of the 1 papers to discuss cited")
    # The folder's numbering and templates: in its settings (their own tab).
    await user.open(f"/?folder={fid}")
    user.find(marker="folder-edit").click()
    await user.should_see(marker="folder-tab-notes")
    user.find(marker="folder-nested-influence").click()  # (the folder's tab: not its notes')
    user.find(marker="folder-tab-notes").click()
    await user.should_see(marker="folder-same-formats")  # (both "#{index}")
    user.find(marker="folder-number-format").clear().type("[{index}]")
    user.find(marker="folder-list-format-0").clear().type("§{index}")
    await user.should_not_see(marker="folder-same-formats")
    await user.should_see("§2")  # (the examples: a list's 2nd paper, cited and listed)
    user.find(marker="folder-list-template-0").clear().type("{.number (.year)}")
    await user.should_see("§2 (2026)")  # (its citing format)
    user.find(marker="folder-cite").clear().type("{.number: .title}")
    await user.should_see("[1]: Neural ranking at scale")  # (that of the papers cited only)
    # Another list (not to cite), first: the ★ button's.
    user.find(marker="folder-list-add").click()
    await user.should_see(marker="folder-list-name-1")
    user.find(marker="folder-list-name-1").type("maybe")
    user.find(marker="folder-list-up-1").click()
    user.find(marker="folder-save").click()
    await user.should_not_see(marker="folder-save")
    await asyncio.sleep(0.3)  # (then back to the folder: before leaving it)
    assert reports.numbering(fid) == reports.Numbering("[{index}]", "{.number: .title}")
    assert [(x.name, x.number_format, x.cite, x.must_cite) for x in reports.star_lists(period)] == [
        ("maybe", "**#{index}**", "", False),
        ("starred", "§{index}", "{.number (.year)}", True),
    ]
    assert annotations.first_list(period).name == "maybe"
    assert reports.nested_influence(fid)
    await user.open(f"/pdf/{a}?period={period}")
    await user.should_see(marker="folder-note-mode")
    await asyncio.sleep(0.5)  # (the papers loaded)
    user.find(marker="folder-note").elements.pop().value = f"Good: [@{keys[a]}]."
    await user.should_see("Good: §1 (2021).")  # (the new formats)
    # Beside the papers (in place of the preview): those to discuss, whether cited.
    user.find(marker="folder-note-mode").elements.pop().value = "side"
    await user.should_see("1 of the 1 papers to discuss cited", marker="folder-papers-count")
    await user.should_see(marker=f"folder-papers-cite-{keys[a]}")
    user.find(marker="folder-note").elements.pop().value = "Nothing yet."
    await user.should_see("0 of the 1 papers to discuss cited", marker="folder-papers-count")
    await user.should_see(marker="folder-papers-cite-missing")
    # A search: the person's other papers too.
    b = next(k for s, k in ((s, keys[s.id]) for s in stats) if s.title == "The neural retrieval")
    user.find(marker="folder-papers-search").type("neural")
    await user.should_see(marker=f"folder-papers-cite-{b}")
    await user.should_not_see(marker=f"folder-papers-cite-{keys[a]}")
