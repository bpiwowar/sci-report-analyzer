from sci_report_analyzer import sync
from sci_report_analyzer.db.models import Person
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.sources.base import FetchedPub, FetchedThesis, FetchResult


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


async def note_saved(user, mark: str) -> None:
    """Wait until a note editor has saved what was typed (once typing pauses)."""
    import asyncio

    for _ in range(40):
        if user.find(marker=f"{mark}-status").elements.pop().text == "Saved":
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{mark}: not saved")


# ---- venue decisions by venue text, folder settings, citations (the app's are by id) ----------


def set_venue(raw: str, **values) -> int:
    """Set a venue's fields (venues.update_venue), the venue of that text (created if
    needed)."""
    from sci_report_analyzer import venues

    with session_scope() as s:
        vid = venues.ensure_venue(s, raw).id
    venues.update_venue(vid, **values)
    return vid


def set_correction(raw: str, text: str | None) -> None:
    """Match the venue of that text as ``text`` (None clears)."""
    set_venue(raw, match_text=text or None)


def set_level(raw: str, type_: str | None, rank: str | None) -> None:
    set_venue(raw, level_type=type_ if rank else None, level_rank=rank or None)


def set_kind(raw: str, kind: str | None) -> None:
    set_venue(raw, kind=kind)


def own_settings(folder_id: int) -> bool:
    """Whether a folder uses settings of its own (else: its parent's)."""
    from sci_report_analyzer.db.models import FolderSettingsUse

    with session_scope() as s:
        return s.get(FolderSettingsUse, folder_id) is not None


def uncited(ctx, cited) -> list:
    """The papers to discuss not cited."""
    return [p for p in ctx.papers if not cited.get(p.key)]


def toggle_star(period_id: int, pub_id: int) -> bool:
    from sci_report_analyzer import annotations

    return annotations.toggle_tag(pub_id, annotations.starred_tag_id(), period_id)
