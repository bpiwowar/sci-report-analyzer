"""Help page."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from .. import i18n
from ..i18n import _
from ..ranking.badge import (
    BASE_CATEGORIES,
    LEVEL_HELP,
    OTHER_COLOUR,
    UNRANKED_COLOUR,
    text_colour,
)
from .theme import frame, span

HELP_DIR = Path(__file__).parent.parent / "help"


def help_text(lang: str) -> tuple[str, str]:
    """The help in ``lang`` (English if it has none): before and after the levels' legend."""
    path = HELP_DIR / f"{lang}.md"
    if not path.exists():
        path = HELP_DIR / "en.md"
    before, after = path.read_text(encoding="utf-8").split("<!-- levels -->")
    return before, after


def register() -> None:
    @ui.page("/help")
    def help_page() -> None:
        with frame(_("Help")):
            before, after = help_text(i18n.language())
            ui.markdown(before, extras=["tables"])
            with ui.column().classes("gap-1"):
                for _key, label, colour in BASE_CATEGORIES:
                    level = label.removeprefix("CORE ")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        style = f"background:{colour};color:{text_colour(colour)}"
                        span(f'<span class="vr-chip" style="{style}">{label}</span>')
                        ui.label(LEVEL_HELP.get(level, "")).classes("text-sm")
                with ui.row().classes("items-center gap-2 no-wrap"):
                    span(
                        f'<span class="vr-chip" style="background:{OTHER_COLOUR}">'
                        f"{_('other')}</span>"
                    )
                    ui.label(
                        _(
                            "Matched a venue without a usable rank (e.g. a Scimago entry "
                            "without quartile, an OpenAlex source)."
                        )
                    ).classes("text-sm")
                with ui.row().classes("items-center gap-2 no-wrap"):
                    span(
                        f'<span class="vr-chip" style="background:{UNRANKED_COLOUR};'
                        f'color:#000">{_("not ranked")}</span>'
                    )
                    ui.label(_("No match in the ranking datasets.")).classes("text-sm")
            ui.markdown(after)
