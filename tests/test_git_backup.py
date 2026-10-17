"""The backup of the folders in a git repository (git_backup): what is written, a commit
each time, the directories of what was renamed or moved moved at once."""

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from helpers import PDF, make_person
from nicegui.testing import User

from sci_report_analyzer import categories, documents, folders, git_backup, persons

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def run(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "backup"
    git_backup.save_config(git_backup.BackupConfig(path=str(path), interval=0))
    git_backup.install()
    yield path
    git_backup.wait()
    git_backup.uninstall()


def _seed() -> tuple[int, int, int, int]:
    """A folder within another, a person in it with notes, a document and an excerpt:
    (outer folder, inner folder, person, period)."""
    outer = folders.save_folder(None, "Hiring 2026")
    inner = folders.save_folder(None, "Lecturers", parent_id=outer)
    pid = make_person("Ann Example")
    period = folders.add_person(inner, pid, 2020, 2024)
    folders.set_notes(period, "Strong candidate.")
    doc = documents.add(period, "Application.pdf", PDF)
    cat = categories.add(inner, "Research")
    categories.add_excerpt(cat, period, "Led a project.", 1, [], document_id=doc, start=2021)
    return outer, inner, pid, period


async def test_backup_writes_and_commits(repo) -> None:
    _outer, _inner, _pid, _period = _seed()
    res = await git_backup.backup()
    assert res.commit and res.changed
    person = repo / "Hiring 2026" / "Lecturers" / "Ann Example"
    assert (person / "notes.md").read_text() == "Strong candidate.\n"
    assert (person / "report.md").read_text().startswith("Strong candidate.")
    assert "Led a project." in (person / "excerpts.md").read_text()
    (excerpt,) = json.loads((person / "excerpts.json").read_text())
    assert excerpt["category"] == "Research" and excerpt["start_year"] == 2021
    (doc,) = json.loads((person / "documents.json").read_text())
    assert (person / doc["file"]).read_bytes() == PDF and "lines" not in doc
    assert json.loads((person / "period.json").read_text())["period"]["start_year"] == 2020
    assert json.loads((person / "person.json").read_text())["person"]["name"] == "Ann Example"
    folder = json.loads((repo / "Hiring 2026" / "Lecturers" / "folder.json").read_text())
    assert [c["path"] for c in folder["categories"]] == ["Research"]
    assert run(repo, "rev-parse", "HEAD") == res.commit
    assert run(repo, "status", "--porcelain") == ""
    cfg = git_backup.load_config()
    assert cfg.last_commit == res.commit and cfg.last_at and not cfg.last_error
    # Nothing changed: no commit.
    assert (await git_backup.backup()).commit is None


async def test_renamed_and_moved_at_once(repo) -> None:
    _outer, inner, pid, _period = _seed()
    await git_backup.backup()
    folders.rename(inner, "Assistant professors")
    git_backup.wait()
    moved = repo / "Hiring 2026" / "Assistant professors" / "Ann Example"
    assert (moved / "notes.md").is_file()
    assert not (repo / "Hiring 2026" / "Lecturers").exists()
    assert run(repo, "log", "-1", "--format=%s") == "Moved"
    assert run(repo, "status", "--porcelain") == ""
    # (a rename for git: its history followed)
    log = run(repo, "log", "--follow", "--format=%s", "--", f"{moved.relative_to(repo)}/notes.md")
    assert len(log.splitlines()) == 2

    folders.set_parent(inner, None)  # (to the top)
    git_backup.wait()
    assert (repo / "Assistant professors" / "Ann Example" / "notes.md").is_file()
    other = folders.save_folder(None, "Promotions")
    folders.move_person(pid, inner, other)
    git_backup.wait()
    # (moved at once; the new folder's own files at the next backup)
    assert (repo / "Promotions" / "Ann Example" / "notes.md").is_file()
    assert not (repo / "Promotions" / "folder.json").exists()
    persons.update(pid, name="Ann B. Example", affiliation=None, aliases=[], notes=None)
    git_backup.wait()
    await git_backup.backup()
    assert (repo / "Promotions" / "Ann B. Example" / "notes.md").is_file()
    assert not (repo / "Assistant professors" / "Ann Example").exists()
    assert (repo / "Hiring 2026" / "folder.json").is_file()
    assert run(repo, "status", "--porcelain") == ""


async def test_deleted_removed_kept_in_history(repo) -> None:
    _outer, inner, _pid, _period = _seed()
    first = (await git_backup.backup()).commit
    folders.delete_folder(inner)
    await git_backup.backup()
    assert not (repo / "Hiring 2026" / "Lecturers").exists()
    assert (repo / "Hiring 2026" / "folder.json").is_file()
    path = "Hiring 2026/Lecturers/Ann Example/notes.md"
    assert run(repo, "show", f"{first}:{path}") == "Strong candidate."


def test_layout_names() -> None:
    assert git_backup.clean_name('a/b: "c"?') == "a-b- -c"
    assert git_backup.clean_name("..") == "untitled"
    a = folders.save_folder(None, "Same")
    b = folders.save_folder(None, "Same")
    p1 = make_person("Bob Sample")
    p2 = make_person("Bob Sample")
    x = folders.add_person(a, p1, None, None)
    y = folders.add_person(a, p2, None, None)
    lay = git_backup.layout()
    assert lay.folders == {a: "Same", b: "Same (2)"}
    assert lay.periods == {x: "Same/Bob Sample", y: "Same/Bob Sample (2)"}


def test_due(repo) -> None:
    cfg = git_backup.BackupConfig(path=str(repo), interval=30)
    assert git_backup.due(cfg)  # (never made)
    now = datetime.now(UTC)
    cfg.last_at = (now - timedelta(minutes=10)).isoformat()
    assert not git_backup.due(cfg, now)
    cfg.last_at = (now - timedelta(minutes=40)).isoformat()
    make_person()  # (a change)
    assert git_backup.due(cfg, now + timedelta(minutes=1))
    cfg.interval = 0
    assert not git_backup.due(cfg, now)
    assert not git_backup.due(git_backup.BackupConfig(path=""), now)


async def test_off_said(user: User, tmp_path) -> None:
    git_backup.save_config(git_backup.BackupConfig(path=""))
    await user.open("/settings?tab=backup")
    await user.should_see("Backups are off: set a directory.", marker="backup-status")


async def test_settings_and_button(user: User, tmp_path) -> None:
    _seed()
    path = tmp_path / "bk"
    git_backup.save_config(git_backup.BackupConfig(path=str(path)))
    await user.open("/settings?tab=backup")
    await user.should_see(marker="backup-path")
    user.find(marker="backup-interval").clear().type("15")
    user.find(marker="backup-save").click()
    await user.should_see("Backed up every 15 minutes", marker="backup-status")
    assert git_backup.load_config().interval == 15
    user.find(marker="backup-now").click()  # (the header's)
    for _i in range(200):
        if git_backup.load_config().last_at:
            break
        await asyncio.sleep(0.05)
    await user.should_see("Backed up:")
    assert (path / "Hiring 2026" / "Lecturers" / "Ann Example" / "notes.md").is_file()
    user.find(marker="backup-run").click()  # (the tab's: its status shown again)
    await user.should_see("Backed up: nothing changed since the last backup")
    await user.should_see("Last backup:", marker="backup-status")
    git_backup.wait()
    git_backup.uninstall()
