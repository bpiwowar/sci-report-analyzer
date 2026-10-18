"""Report templates: named texts a report (the notes of a person within a folder) starts
from, their headings and blocks (``[[excerpts]]``…) with, if any, guidance in Obsidian
comments (``%% … %%``, left out when shown or copied).

Those of a folder's settings (with their categories, which ``[[excerpts#id]]`` names; edited
with them, Parameters → Folder settings): a report is started from one; a template is also
made from a written report, its structure only (``outline``)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from . import folders
from .db.session import session_scope
from .i18n import _
from .reports import _BLOCK, _HEADING

_FENCE = re.compile(r"^[ \t]*(```|~~~)")


@dataclass
class ReportTemplate:
    name: str
    text: str  # (Markdown: headings, blocks, guidance as %% comments %%)


def default_templates() -> list[ReportTemplate]:
    """The template shipped (its headings and guidance in the interface's language)."""
    return [
        ReportTemplate(
            _("Evaluation report"),
            "\n\n".join(
                [
                    f"## {_('Summary')}",
                    "%% " + _("The person in a few lines: their profile, their strengths.") + " %%",
                    f"## {_('Publications')}",
                    "[[publications]]",
                    "%% " + _("The papers to discuss, cited ([@key]): what they bring.") + " %%",
                    f"## {_('Activities')}",
                    "[[excerpts]]",
                    f"## {_('Assessment')}",
                    "%% " + _("The overall opinion, and the points to raise.") + " %%",
                    f"## {_('References')}",
                    "[[references]]",
                ]
            )
            + "\n",
        )
    ]


def load(folder_id: int | None) -> list[ReportTemplate]:
    """The templates of a folder's settings (a folders.SettingsRef: those; never saved: the
    default ones)."""
    saved = None
    if folder_id:
        with session_scope() as s:
            if (x := folders.used_settings(s, folder_id)) is not None:
                saved = x.report_templates
                if saved == _dump(default_templates()):
                    x.report_templates = saved = None  # (a copy of the defaults: those)
    if saved is None:
        return default_templates()
    return [ReportTemplate(t.get("name") or "", t.get("text") or "") for t in saved]


def save(folder_id: int, items: list[ReportTemplate]) -> None:
    """The templates of a folder's settings (those sharing them too). Refused
    (``ValueError``) when one has no name, or two the same."""
    for t in items:
        t.name = t.name.strip()
        t.text = t.text.strip() and t.text.rstrip() + "\n"
        if not t.name:
            raise ValueError(_("A report template needs a name"))
    names = [t.name for t in items]
    if len(set(names)) < len(names):
        raise ValueError(_("Two report templates have the same name"))
    with session_scope() as s:
        if (x := folders.used_settings(s, folder_id)) is not None:
            x.report_templates = None if items == default_templates() else _dump(items)


def _dump(items: list[ReportTemplate]) -> list[dict]:
    return [asdict(t) for t in items]


def text_of(folder_id: int, name: str) -> str | None:
    return next((t.text for t in load(folder_id) if t.name == name), None)


def add(folder_id: int, name: str, text: str) -> None:
    """A template of a folder's settings (that of the same name replaced, in its place)."""
    items = load(folder_id)
    same = next((t for t in items if t.name == name.strip()), None)
    if same is not None:
        same.text = text
    else:
        items.append(ReportTemplate(name, text))
    save(folder_id, items)


def outline(report: str) -> str:
    """The structure of a written report, as a template: its headings and its blocks
    (``[[publications]]``, ``[[excerpts#key]]``…, alone on their line), in
    order; what is written (text, citations, comments, code) left out."""
    kept: list[str] = []
    fenced = False
    for line in report.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (_HEADING.match(line) or _BLOCK.fullmatch(line)):
            kept.append(line.strip())
    return "\n\n".join(kept) + "\n" if kept else ""
