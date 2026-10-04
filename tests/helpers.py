import gzip
import json
from pathlib import Path

from sci_report_analyzer import sync
from sci_report_analyzer.db.models import Person
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.sources.base import FetchedPub, FetchedThesis, FetchResult

FIXTURES = Path(__file__).parent / "fixtures"
# A tiny PDF file.
PDF = b"%PDF-1.4\n% a tiny test file\n%%EOF\n"


def load_fixture(name: str):
    """A JSON file of ``tests/fixtures`` (gzipped if its name ends with .gz)."""
    path = FIXTURES / name
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return json.loads(path.read_text())


def make_person(name: str = "Jane Doe") -> int:
    with session_scope() as s:
        p = Person(name=name)
        s.add(p)
        s.flush()
        return p.id


def add_source(person_id: int, source: str, ext: str, pubs=(), theses=()) -> int:
    link_id = sync.add_link(person_id, source, ext)
    sync.finish_link(link_id, FetchResult(publications=list(pubs), theses=list(theses)))
    return link_id


def pub(
    key: str, title: str, year: int | None = 2020, venue: str | None = None, **kw
) -> FetchedPub:
    return FetchedPub(external_key=key, title=title, year=year, venue=venue, **kw)


def thesis(tid: str, role: str, title: str) -> FetchedThesis:
    return FetchedThesis(thesis_id=tid, role=role, title=title, student="A Student")


async def note_status(user, mark: str, text: str = "Saved") -> None:
    """Wait until a note editor's status is ``text`` (by default: saved what was typed, once
    typing pauses)."""
    import asyncio

    for _ in range(40):
        if user.find(marker=f"{mark}-status").elements.pop().text == text:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{mark}: not {text!r}")
