"""People added to a folder from its button in the tree (those known, searched; or a new
one), and a new person's documents in the folder, added before their page."""

import pytest
from helpers import PDF, make_person
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User

from sci_report_analyzer import documents, folders, sync
from sci_report_analyzer.sources import ADAPTERS

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def test_find_people() -> None:
    ann, bob = make_person("Ann Example"), make_person("Bob Sample")
    assert [p[0] for p in folders.find_people("ann")] == [ann]
    assert [p[0] for p in folders.find_people("")] == [ann, bob]
    assert folders.find_people("zzz") == []


async def test_add_known_people_to_a_folder(user: User) -> None:
    ann, bob, cid = make_person("Ann Example"), make_person("Bob Sample"), make_person("Cid Test")
    fid = folders.save_folder(None, "Hiring committee")
    other = folders.save_folder(None, "Prize jury")
    folders.add_person(fid, cid)
    await user.open(f"/?folder={fid}")
    # A button for each folder in the tree.
    await user.should_see(marker=f"folder-people-{other}")
    user.find(marker=f"folder-people-{fid}").click()
    await user.should_see(marker="folder-people-search")
    # Those in it already: said so, not to add.
    await user.should_see(marker=f"folder-people-in-{cid}")
    await user.should_not_see(marker=f"folder-people-add-{cid}")
    # Searched as typed.
    user.find(marker="folder-people-search").type("ann")
    await user.should_not_see(marker=f"folder-people-row-{bob}")
    user.find(marker="folder-people-start").elements.pop().value = 2021
    user.find(marker="folder-people-end").elements.pop().value = 2025
    user.find(marker="folder-people-tags").elements.pop().value = ["shortlisted"]
    user.find(marker=f"folder-people-add-{ann}").click()
    await user.should_see(marker=f"folder-people-in-{ann}")
    member = next(m for m in folders.folders() if m.id == fid).members
    found = next(m for m in member if m.person_id == ann)
    assert (found.start_year, found.end_year, found.tags) == (2021, 2025, ["shortlisted"])
    # A new person: the dialog adding one, in that folder.
    user.find(marker="folder-people-new").click()
    await user.should_see("Add a person to “Hiring committee”")


async def test_new_person_documents_step(user: User, monkeypatch) -> None:
    """After their sources, a new person's documents in the folder: added, renamed, removed;
    then their page."""

    async def fake_search(self, name, affiliation=None):
        return []

    monkeypatch.setattr(type(ADAPTERS["dblp"]), "search", fake_search)
    monkeypatch.setattr(sync, "_running", {})
    fid = folders.save_folder(None, "Hiring committee")
    await user.open(f"/?folder={fid}")
    user.find("add-person").click()
    await user.should_see("Full name")
    user.find("Full name").type("Ann Example")
    user.find("add-person-sources").elements.pop().value = ["dblp"]
    user.find("add-person-ok").click()
    await user.should_see(marker="add-person-later")
    user.find(marker="add-person-later").click()
    # Their documents, before their page.
    await user.should_see(marker="add-person-drop")
    await user.should_see(marker="add-person-no-doc")
    await user.should_see(marker="add-person-docs-skip")
    (period,) = [m.period_id for m in folders.folders()[0].members]
    upload = user.find(marker="add-person-upload").elements.pop()
    for name in ("Application.pdf", "CV.pdf"):
        await upload.handle_uploads(
            [SmallFileUpload(name=name, content_type="application/pdf", _data=PDF)]
        )
    await upload.handle_uploads(
        [SmallFileUpload(name="notes.txt", content_type="text/plain", _data=b"hello")]
    )
    await user.should_see("notes.txt: not a PDF file")
    docs = {
        d.name: d.id for d in documents.of_person(folders.folders()[0].members[0].person_id)[period]
    }
    assert set(docs) == {"Application", "CV"}
    await user.should_see(marker=f"add-person-doc-{docs['CV']}")
    await user.should_not_see(marker="add-person-docs-skip")
    name = user.find(marker=f"add-person-doc-name-{docs['Application']}")
    name.clear().type("Application letter").trigger("blur")
    user.find(marker=f"add-person-doc-remove-{docs['CV']}").click()
    await user.should_not_see(marker=f"add-person-doc-{docs['CV']}")
    pid = folders.folders()[0].members[0].person_id
    assert [d.name for d in documents.of_person(pid)[period]] == ["Application letter"]
    user.find(marker="add-person-docs-done").click()
    await user.should_see(marker="tab-sources")  # (their page, at their sources)
