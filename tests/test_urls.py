import pytest
from helpers import make_person
from nicegui.testing import User

from sci_report_analyzer import annotations, folders
from sci_report_analyzer.urls import (
    folder_url,
    person_url,
    resolve_folder,
    resolve_person,
    slugify,
)

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def test_slugify() -> None:
    assert slugify("Émile Zoé-Lefèvre") == "emile-zoe-lefevre"
    assert slugify("  Hiring 2026 / CS!  ") == "hiring-2026-cs"
    assert slugify("张") == ""


def test_person_urls_within_nested_folders() -> None:
    a = make_person("Zoé Martin")
    top = folders.save_folder(None, "Hiring Committee")
    sub = folders.save_folder(None, "Session 2026", parent_id=top)
    period = folders.add_person(sub, a)
    assert person_url(a) == "/person/zoe-martin"
    assert person_url(a, period, tab="report") == (
        "/person/hiring-committee/session-2026/zoe-martin?tab=report"
    )
    assert resolve_person("hiring-committee/session-2026/zoe-martin") == (a, period)
    assert resolve_person("zoe-martin") == (a, None)
    # Not (or no longer) in that folder; an unknown one: the person alone.
    assert resolve_person("hiring-committee/zoe-martin") == (a, None)
    assert resolve_person("nowhere/zoe-martin") == (a, None)
    assert resolve_person("nobody") is None
    assert folder_url(sub) == "/?folder=hiring-committee/session-2026"
    assert resolve_folder("hiring-committee/session-2026") == sub
    assert resolve_folder("session-2026") is None
    # A period in no folder: given as a parameter.
    own = annotations.save_period(a, "HDR", 2015, 2024)
    assert person_url(a, own) == f"/person/zoe-martin?period={own}"


def test_collisions() -> None:
    a, b, c = make_person("Ann Lee"), make_person("Ann  Lée"), make_person("???")
    assert person_url(a) == "/person/ann-lee"
    assert person_url(b) == f"/person/ann-lee-{b}"
    assert person_url(c) == f"/person/{c}"
    assert resolve_person(f"ann-lee-{b}") == (b, None)
    # Sibling folders with the same name; the same name elsewhere: kept.
    x, y = folders.save_folder(None, "Jury"), folders.save_folder(None, "jury")
    z = folders.save_folder(None, "Jury", parent_id=x)
    assert folder_url(x) == "/?folder=jury"
    assert folder_url(y) == f"/?folder=jury-{y}"
    assert folder_url(z) == "/?folder=jury/jury"
    assert [resolve_folder(p) for p in ("jury", f"jury-{y}", "jury/jury")] == [x, y, z]


def test_former_numeric_urls() -> None:
    a, b = make_person("Ann Lee"), make_person("Bob Ray")
    fid = folders.save_folder(None, "Jury")
    period = folders.add_person(fid, a)
    assert resolve_person(str(a)) == (a, None)
    assert resolve_person(f"{a}/{period}") == (a, period)
    assert resolve_person(f"{b}/{period}") == (b, None)  # (not theirs)
    assert resolve_person("999") is None
    assert resolve_folder(str(fid)) == fid


async def test_pages_by_semantic_and_former_urls(user: User) -> None:
    a = make_person("Ann Lee")
    fid = folders.save_folder(None, "Hiring")
    period = folders.add_person(fid, a)
    await user.open("/person/hiring/ann-lee")
    await user.should_see(marker="folder-title")
    old = await user.http_client.get(f"/person/{a}/{period}?tab=report&q=x")
    assert old.headers["location"] == "/person/hiring/ann-lee?tab=report&q=x"
    old = await user.http_client.get(f"/?folder={fid}")
    assert old.headers["location"] == "/?folder=hiring"
    await user.open(f"/person/{a}/{period}?tab=report")  # (redirected)
    await user.should_see(marker="folder-title")
    await user.open("/person/nobody")
    await user.should_see("Unknown person")
    await user.open("/?folder=hiring")
    await user.should_see(marker="people-title", content="Hiring")
    await user.open(f"/?folder={fid}")  # (redirected)
    await user.should_see(marker="people-title", content="Hiring")
