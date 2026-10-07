"""Report templates (Settings → Report templates): named texts a report (the notes of a
person within a folder) starts from, their headings and blocks (``[[excerpts]]``…) with,
if any, guidance in Obsidian comments (``%% … %%``, left out when shown or copied).

General (an AppSetting, as the citation templates): a folder's starting notes (its
skeleton, see reports.skeleton) are copied from one, and a report from one when it starts;
a template is also made from a written report, its structure only (``outline``)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .db.app_settings import get_setting, set_setting
from .i18n import _
from .reports import _BLOCK, _HEADING

KEY = "report_skeletons"  # an AppSetting (not "report_templates": the citation templates')
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


def load() -> list[ReportTemplate]:
    """The templates (never saved: the default ones)."""
    saved = get_setting(KEY)
    if saved is None:
        return default_templates()
    return [ReportTemplate(t.get("name") or "", t.get("text") or "") for t in saved["items"]]


def save(items: list[ReportTemplate]) -> None:
    """Refused (``ValueError``) when one has no name, or two the same."""
    for t in items:
        t.name = t.name.strip()
        t.text = t.text.strip() and t.text.rstrip() + "\n"
        if not t.name:
            raise ValueError(_("A report template needs a name"))
    names = [t.name for t in items]
    if len(set(names)) < len(names):
        raise ValueError(_("Two report templates have the same name"))
    set_setting(KEY, {"items": [asdict(t) for t in items]})


def text_of(name: str) -> str | None:
    return next((t.text for t in load() if t.name == name), None)


def add(name: str, text: str) -> None:
    """A template (that of the same name replaced, in its place)."""
    items = load()
    same = next((t for t in items if t.name == name.strip()), None)
    if same is not None:
        same.text = text
    else:
        items.append(ReportTemplate(name, text))
    save(items)


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
