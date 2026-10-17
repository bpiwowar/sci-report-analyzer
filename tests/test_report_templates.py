"""Report templates: the texts a report starts from (those of a folder's settings), a template
made from a written report (its headings and blocks), picked for a folder's starting notes or
a report."""

import asyncio

import pytest
from helpers import make_person, note_status
from nicegui.testing import User

from sci_report_analyzer import folders, report_templates, reports
from sci_report_analyzer.report_templates import ReportTemplate, outline

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

REPORT = """# Jane Doe

A strong candidate: see [@doe2021deep]{.notes}.

## Publications

[]{.publications}

Mostly #strong papers; [@doe2022neural] is the best.
%% to check: the venue %%

## Activities
   [[excerpts#teaching]]

```
# not a heading
[]{.excerpts}
```

### Teaching #not-a-heading-tag
Courses []{.excerpts} inline (not a block).

#hashtag line
[]{.references}
[[excerpts]] inline
"""


def test_outline_keeps_headings_and_blocks():
    assert outline(REPORT) == (
        "# Jane Doe\n\n## Publications\n\n[]{.publications}\n\n## Activities\n\n"
        "[[excerpts#teaching]]\n\n### Teaching #not-a-heading-tag\n\n"
        "[]{.references}\n"
    )
    assert outline("Just text, [@key] and %% a comment %%.") == ""
    assert outline(outline(REPORT)) == outline(REPORT)  # (a template's outline: itself)


def test_templates_saved_and_defaults():
    fid = folders.save_folder(None, "Hiring 2026")
    [default] = report_templates.load(fid)  # (never saved: the default one)
    assert "[[publications]]" in default.text and "[[excerpts]]" in default.text
    assert outline(default.text).count("[[") == 3  # (its guidance: comments only)
    report_templates.add(fid, "Prize", "## Why\n")
    assert [t.name for t in report_templates.load(fid)] == [default.name, "Prize"]
    report_templates.add(fid, "Prize ", "## Why not\n\n")  # (the same name: replaced, in place)
    assert report_templates.load(fid)[1] == ReportTemplate("Prize", "## Why not\n")
    assert report_templates.text_of(fid, "Prize") == "## Why not\n"
    with pytest.raises(ValueError):
        report_templates.save(fid, [ReportTemplate("A", ""), ReportTemplate("A ", "")])
    with pytest.raises(ValueError):
        report_templates.save(fid, [ReportTemplate(" ", "## x")])
    report_templates.save(fid, [])  # (all removed: none, not the default)
    assert report_templates.load(fid) == []


def test_templates_of_each_settings():
    """Those of a folder's settings (with their categories): a copy of the settings has
    them, other settings their own."""
    fid = folders.save_folder(None, "Hiring 2026")
    report_templates.add(fid, "Prize", "## Why\n")
    default = folders.SettingsRef(folders.default_settings())
    assert report_templates.text_of(default, "Prize") == "## Why\n"
    copy = folders.SettingsRef(folders.create_settings("Prizes", default))
    empty = folders.SettingsRef(folders.create_settings("Other"))
    assert report_templates.text_of(copy, "Prize") == "## Why\n"
    assert report_templates.text_of(empty, "Prize") is None  # (the default ones)
    report_templates.add(copy, "Prize", "## Why not\n")
    assert report_templates.text_of(fid, "Prize") == "## Why\n"
    folders.use_settings(fid, copy)
    assert report_templates.text_of(fid, "Prize") == "## Why not\n"


async def test_templates_edited_with_the_settings(user: User):
    """Edited with their settings, next to their categories (the former tab: a link to
    them)."""
    folders.save_folder(None, "Hiring 2026")
    sid = folders.default_settings()
    await user.open("/settings?tab=skeletons")  # (moved: the folder settings)
    await user.should_see(marker="folder-settings-help", content="report templates")
    user.find(marker=f"settings-report-{sid}").click()
    await user.should_see(marker="settings-panel-report")
    await user.should_see(marker="settings-edit-categories")
    await user.should_see(marker="report-skeleton-0")
    user.find(marker="report-skeleton-add").click()
    await user.should_see(marker="report-skeleton-name-1")
    user.find(marker="report-skeleton-name-1").clear().type("Prize")
    user.find(marker="report-skeleton-text-1").clear().type("## Why\n[]{.excerpts}")
    # (the new one: at once among those the starting notes are copied from)
    user.find(marker="folder-skeleton-template").click()
    user.find(marker="folder-skeleton-template-1").click()
    user.find(marker="settings-save").click()
    await user.should_not_see(marker="settings-save")
    ref = folders.SettingsRef(sid)
    assert report_templates.load(ref)[1] == ReportTemplate("Prize", "## Why\n[]{.excerpts}\n")
    assert reports.skeleton(ref) == "## Why\n[]{.excerpts}\n"
    # Two of the same name: refused, said.
    user.find(marker=f"settings-report-{sid}").click()
    await user.should_see(marker="report-skeleton-name-1")
    user.find(marker="report-skeleton-name-1").clear().type(report_templates.load(ref)[0].name)
    user.find(marker="settings-save").click()
    await user.should_see("Two report templates have the same name")
    await user.should_see(marker="settings-save")


async def test_report_from_and_to_a_template(user: User):
    pid = make_person("Jane Doe")
    folder = folders.save_folder(None, "Hiring 2026")
    period = folders.add_person(folder, pid)
    report_templates.add(folder, "Prize", "## Why\n\n[]{.excerpts}\n")
    await user.open(f"/person/{pid}?tab=report")
    await user.should_see(marker="folder-note")
    # Empty notes: started from a template at once.
    user.find(marker="folder-note-templates").click()
    user.find(marker="folder-note-template-1").click()
    await note_status(user, "folder-note")
    assert folders.notes_of(period) == "## Why\n\n[]{.excerpts}"
    # Written: replaced once confirmed.
    user.find(marker="folder-note").elements.pop().value = "## Why\n\nShe is the best.\n"
    await note_status(user, "folder-note")
    user.find(marker="folder-note-templates").click()
    user.find(marker="folder-note-template-0").click()
    await user.should_see(marker="folder-note-template-replace")
    user.find(marker="folder-note-template-replace").click()
    await note_status(user, "folder-note")
    assert folders.notes_of(period).startswith("## Summary")
    # Its structure saved as a template (replacing one of that name, once confirmed).
    user.find(marker="folder-note").elements.pop().value = "## Why\n\nBest.\n\n## More\nText."
    await note_status(user, "folder-note")
    user.find(marker="folder-note-templates").click()
    user.find(marker="folder-note-save-template").click()
    await user.should_see(marker="report-skeleton-save-name")
    user.find(marker="report-skeleton-save-name").type("Prize")
    user.find(marker="report-skeleton-save-ok").click()
    await user.should_see(marker="report-skeleton-replace")
    user.find(marker="report-skeleton-replace").click()
    await asyncio.sleep(0.1)
    assert report_templates.text_of(folder, "Prize") == "## Why\n\n## More\n"


async def test_folder_starting_notes_from_a_template(user: User):
    fid = folders.save_folder(None, "Hiring 2026")
    report_templates.add(fid, "Prize", "## Why\n")
    await user.open(f"/?folder={fid}")
    user.find(marker="folder-edit").click()
    await user.should_see(marker="folder-tab-notes")
    user.find(marker="folder-tab-notes").click()
    user.find(marker="folder-skeleton-template").click()
    user.find(marker="folder-skeleton-template-1").click()
    assert user.find(marker="folder-skeleton").elements.pop().value == "## Why\n"
    user.find(marker="folder-save").click()
    await user.should_not_see(marker="folder-save")
    await asyncio.sleep(0.3)
    assert reports.skeleton(fid) == "## Why\n"
