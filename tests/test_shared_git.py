"""Shared settings in a git repository: pull, push, conflicts (a local bare repository as
the group's)."""

import json
import subprocess

import pytest
from helpers import add_source, make_person, pub, set_venue
from sqlalchemy import select

from sci_report_analyzer import authorship, shared, shared_git
from sci_report_analyzer.db.models import Venue
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking.service import load_settings, save_settings


def run(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def group(tmp_path, monkeypatch):
    """The group's repository (bare), and the app's copy of it in a directory of its own."""
    remote = tmp_path / "group.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    monkeypatch.setattr(shared_git, "repo_dir", lambda: tmp_path / "app-copy")
    shared_git.save_config(shared_git.SharedConfig(identity="jdoe", url=str(remote), push="direct"))
    yield remote
    authorship.forget_identity()


def clone(remote, where) -> str:
    """A member's working copy (to look at or change the group's files)."""
    run(remote.parent, "clone", "-q", str(remote), str(where))
    run(where, "config", "user.name", "Alice")
    run(where, "config", "user.email", "alice@example.org")
    return str(where)


def _venue(raw="Conference on Gitted Venues"):
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "P1", 2020, raw)])
    return set_venue(raw, kind="intl_conference", short_name="CGV")


def test_first_push_then_pull_is_empty(group, tmp_path):
    _venue()
    pull = shared_git.prepare_pull()
    assert pull.head is None and not pull.conflicts and pull.first
    shared_git.finish_pull(pull)
    out = shared_git.prepare_push()
    assert "venue/cgv/kind" in out.keys
    pushed = shared_git.push(out, set(out.keys), "CGV is international")
    assert pushed.direct and pushed.branch == "main" and pushed.sha
    work = clone(group, tmp_path / "w")
    cgv = json.loads((tmp_path / "w/venues/cgv.json").read_text())
    assert cgv["kind"] == "intl_conference" and cgv["decisions"]["kind"]["by"] == "jdoe"
    assert run(work, "log", "-1", "--format=%s") == "CGV is international"
    # Nothing new on either side.
    assert shared_git.prepare_push().keys == []
    pull = shared_git.prepare_pull()
    assert pull.incoming() == [] and not pull.conflicts


def test_pull_takes_the_group_changes_and_keeps_local_ones(group, tmp_path):
    vid = _venue()
    shared_git.finish_pull(shared_git.prepare_pull())
    out = shared_git.prepare_push()
    shared_git.push(out, set(out.keys), "first")
    # Alice edits the group's files (a README of the repository is kept).
    work = clone(group, tmp_path / "w")
    path = tmp_path / "w/venues/cgv.json"
    cgv = json.loads(path.read_text())
    cgv["levels"] = [{"type": "conference", "rank": "A", "from": 2018, "by": "alice"}]
    path.write_text(shared.dumps(cgv))
    (tmp_path / "w/README.md").write_text("Our settings\n")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "CGV is A since 2018")
    run(work, "push", "-q")
    # Meanwhile here: the website set.
    from sci_report_analyzer import venues

    venues.update_venue(vid, url="https://cgv.org")
    pull = shared_git.prepare_pull()
    assert pull.incoming() == ["venue/cgv/levels"] and not pull.conflicts
    assert "venue/cgv/url" in pull.outgoing()
    shared_git.finish_pull(pull)
    with session_scope() as s:
        v = s.get(Venue, vid)
        assert v.levels[0]["rank"] == "A" and v.levels[0]["by"] == "alice"
        assert v.url == "https://cgv.org"
    out = shared_git.prepare_push()
    assert out.keys == ["venue/cgv/url"]
    shared_git.push(out, set(out.keys), "website")
    run(work, "pull", "-q")
    assert (tmp_path / "w/README.md").exists()
    assert json.loads(path.read_text())["url"] == "https://cgv.org"


def test_conflict_and_push_out_of_date(group, tmp_path):
    vid = _venue()
    shared_git.finish_pull(shared_git.prepare_pull())
    out = shared_git.prepare_push()
    shared_git.push(out, set(out.keys), "first")
    work = clone(group, tmp_path / "w")
    path = tmp_path / "w/venues/cgv.json"
    cgv = json.loads(path.read_text())
    cgv["kind"] = "natl_conference"
    path.write_text(shared.dumps(cgv))
    run(work, "commit", "-q", "-am", "national")
    run(work, "push", "-q")
    from sci_report_analyzer import venues

    venues.update_venue(vid, kind="intl_workshop")
    with pytest.raises(shared_git.OutOfDate):
        shared_git.prepare_push()
    pull = shared_git.prepare_pull()
    assert [c.key for c in pull.conflicts] == ["venue/cgv/kind"]
    shared_git.finish_pull(pull, {"venue/cgv/kind"})  # (the group's taken)
    with session_scope() as s:
        assert s.get(Venue, vid).kind == "natl_conference"
    assert shared_git.prepare_push().keys == []


def test_push_on_a_branch_for_a_pull_request(group, tmp_path):
    _venue()
    shared_git.finish_pull(shared_git.prepare_pull())
    out = shared_git.prepare_push()
    shared_git.push(out, set(out.keys), "first")  # (an empty group: on its branch)
    cfg = shared_git.load_config()
    cfg.push = "branch"
    shared_git.save_config(cfg)
    s = load_settings()
    s.min_score = 0.9
    save_settings(s)
    out = shared_git.prepare_push()
    assert out.keys == ["settings/min_score"]
    pushed = shared_git.push(out, {"settings/min_score"}, "stricter matching")
    assert not pushed.direct and pushed.branch.startswith("settings/jdoe-")
    branches = run(group, "branch", "--list")
    assert pushed.branch in branches
    # (the base is unchanged until the request is merged and pulled)
    assert shared_git.prepare_push().keys == ["settings/min_score"]


def test_pull_links_a_new_member_venues(group, tmp_path):
    """A member's first pull: the group's venues get attached to their own venues."""
    work = tmp_path / "w"
    run(tmp_path, "init", "-q", "-b", "main", str(work))
    run(work, "config", "user.name", "Alice")
    run(work, "config", "user.email", "alice@example.org")
    (work / "venues").mkdir()
    (work / "settings.json").write_text(shared.dumps({"format": shared.FORMAT, "version": 1}))
    (work / "venues/stv.json").write_text(
        shared.dumps(
            {
                "id": "stv",
                "name": "Symposium on Team Venues",
                "kind": "natl_conference",
                "variants": [{"raw": "Symposium on Team Venues", "source": "dblp"}],
            }
        )
    )
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "init")
    run(work, "push", "-q", str(group), "main")
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "P1", 2020, "Symposium on Team Venues")])
    pull = shared_git.prepare_pull()
    assert not pull.conflicts
    shared_git.finish_pull(pull)
    with session_scope() as s:
        v = s.scalar(select(Venue).where(Venue.share_id == "stv"))
        assert v.kind == "natl_conference" and v.kind_manual
        assert len(list(s.scalars(select(Venue).where(Venue.name.like("%Team Venues%"))))) == 1


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_shared_settings_screen(user, group, tmp_path):
    from sci_report_analyzer import venues

    vid = _venue()
    await user.open("/settings?tab=shared")
    await user.should_see(marker="shared-url")
    user.find("shared-pull").click()
    await user.should_see(marker="shared-pull-apply", retries=40)
    user.find("shared-pull-apply").click()
    await user.should_see("Pulled", retries=40)
    user.find("shared-push").click()
    await user.should_see(marker="shared-push-send", retries=40)
    user.find("shared-message").type("our first decisions")
    user.find("shared-push-send").click()
    await user.should_see("Pushed", retries=40)
    work = clone(group, tmp_path / "w")
    assert json.loads((tmp_path / "w/venues/cgv.json").read_text())["short_name"] == "CGV"
    assert run(work, "log", "-1", "--format=%s") == "our first decisions"
    # A colleague's change, and one of mine: both shown, nothing in conflict.
    path = tmp_path / "w/venues/cgv.json"
    cgv = json.loads(path.read_text())
    cgv["url"] = "https://cgv.example.org"
    path.write_text(shared.dumps(cgv))
    run(work, "commit", "-q", "-am", "website")
    run(work, "push", "-q")
    venues.update_venue(vid, kind="natl_conference")
    await user.open("/settings?tab=shared")
    user.find("shared-pull").click()
    await user.should_see("1 change from the group", retries=40)
    await user.should_see("push it after")
    user.find("shared-pull-apply").click()
    await user.should_see("Pulled", retries=40)
    with session_scope() as s:
        v = s.get(Venue, vid)
        assert v.url == "https://cgv.example.org" and v.kind == "natl_conference"
