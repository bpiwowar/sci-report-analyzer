"""The old reports, moved into the folders' notes (once, when the app starts after the
migration asking for it: c8d1f5a3e7b2).

The report on a person within a folder (a Markdown text citing their papers, and the
papers to discuss with their notes) had its own view, now merged into the folder's notes:
each folder's notes get a last section, "Starred papers", with per person in the folder
the report's text and each of its papers (those with the report's tags, else the starred
ones: the built-in ★ tag), cited (``[@key]``, numbered by the folder's settings) and
described (title, venue, year…), then their notes (the paper's, and within the folder).

Appended only (the notes' text is kept as it is), and once per folder: the folders done are
recorded with their notes, in the same transaction.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from . import annotations, reports
from .db.models import AppSetting, Folder, Period, Report
from .db.session import session_scope
from .i18n import _

logger = logging.getLogger(__name__)

PENDING_KEY = "old_reports"  # an AppSetting: {"pending": bool, "done": [folder ids]}


def _state() -> dict:
    with session_scope() as s:
        row = s.get(AppSetting, PENDING_KEY)
        return dict(row.value or {}) if row else {}


def pending() -> bool:
    """Whether the old reports are still to be moved."""
    return bool(_state().get("pending"))


def _periods(folder_id: int) -> list[tuple[int, int, str, tuple[int | None, int | None]]]:
    """(period id, person id, name, years) of the folder's people, by name."""
    with session_scope() as s:
        rows = [
            (p.id, p.person_id, p.person.name, (p.start_year, p.end_year))
            for p in s.scalars(select(Period).where(Period.folder_id == folder_id))
        ]
    return sorted(rows, key=lambda r: (r[2].lower(), r[0]))


async def _person_section(
    period_id: int, person_id: int, name: str, years: tuple[int | None, int | None]
) -> str:
    """A person's report and its papers (""; nothing to keep)."""
    from .pubview import load_stats

    with session_scope() as s:
        r = s.get(Report, period_id)
        text = (r.text or "").strip() if r else ""
        tag_ids = [t for t in (r.tag_ids or []) if isinstance(t, int)] if r else []
        fmt = (r.number_format if r else None) or reports.NUMBER_FORMAT
    tag_ids = tag_ids or [t for t in [annotations.starred_tag_id()] if t]
    stats = await load_stats(person_id)
    keys = reports.citation_keys(stats)
    papers = reports.papers_to_discuss(stats, keys, tag_ids, period_id, years)
    if not text and not papers:
        return ""
    names = {t.id: t.name for t in annotations.all_tags()}
    ctx = reports.Context(papers, stats, keys, names, set(tag_ids), period_id, number_format=fmt)
    out = [f"### {name}"]
    if text:
        out.append(text)
    items = []
    for p in papers:
        entry = ctx.entry(p, notes=True, tags=True, indent=2)
        items.append(f"- [@{p.key}]: {entry[len(ctx.number(p)) + 1 :]}")
    if items:
        out.append("\n".join(items))
    return "\n\n".join(out)


async def migrate() -> int:
    """Append the old reports to their folders' notes, if asked by the migration and not
    done yet; returns the number of folders whose notes were appended to."""
    state = _state()
    if not state.get("pending"):
        return 0
    done = set(state.get("done") or [])
    with session_scope() as s:
        folder_ids = list(s.scalars(select(Folder.id).order_by(Folder.id)))
    changed = 0
    for folder_id in folder_ids:
        if folder_id in done:
            continue
        sections = [await _person_section(*p) for p in _periods(folder_id)]
        section = "\n\n".join(x for x in sections if x)
        done.add(folder_id)
        with session_scope() as s:  # (the notes and the folder done: together)
            if section and (f := s.get(Folder, folder_id)) is not None:
                notes = (f.notes or "").rstrip()
                f.notes = (notes + "\n\n" if notes else "") + f"## {_('Starred papers')}\n\n"
                f.notes += section + "\n"
                changed += 1
            s.merge(AppSetting(key=PENDING_KEY, value={"pending": True, "done": sorted(done)}))
    with session_scope() as s:
        s.merge(AppSetting(key=PENDING_KEY, value={"pending": False, "done": sorted(done)}))
    if changed:
        logger.info("The old reports appended to the notes of %d folder(s)", changed)
    return changed
