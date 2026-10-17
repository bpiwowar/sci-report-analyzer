"""The PDFs open beside a person's page (their documents, their papers), whatever its tab:
each in a frame on the right (its viewer's page, embedded: see pdf_viewer.viewer_frame), one
shown at a time; a strip at the bottom lists them, to show one, minimize them, or close one
(from the view only: nothing is deleted). What the page's notes and excerpts ask of a PDF goes
to the one shown (static/dock.js), and a link to a PDF opens it there."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from nicegui import context, ui

from .. import annotations, documents, persons
from ..i18n import _
from .documents_page import page_url_of
from .pdf_viewer import page_url, pdf_icon, scripts

WIDTH = "ui.dock.width"  # AppSetting: the width (px) of the PDFs beside a person's page
WIDTH_DEFAULT, WIDTH_MIN = 720, 320
KEY = re.compile(r"([dp])([0-9]{1,9})")  # (a document: d3; a paper: p12)


def _state_key(person_id: int) -> str:
    # AppSetting: the PDFs left open beside a person's page, and the one shown
    return f"ui.dock.{person_id}"


class PdfDock:
    """The PDFs open beside the page of a person: from the address (``pdfs``: their keys,
    ``pdf``: the one shown, none: minimized), else as left (``on_change``: to show them
    there)."""

    def __init__(
        self,
        person_id: int,
        period: Callable[[], int | None],
        query: dict[str, str],
        on_change: Callable[[], None],
    ) -> None:
        """``period``: that of the page (a paper's tags and notes, in its side column)."""
        self.person_id = person_id
        self._period = period
        self._on_change = on_change
        # (the PDF shown changed: its kind and id, or None)
        self.on_shown: list[Callable[[tuple[str, int] | None], None]] = []
        if "pdfs" in query:
            keys, shown = query["pdfs"].split(","), query.get("pdf")
        else:
            state = annotations.ui_state(_state_key(person_id))
            state = state if isinstance(state, dict) else {}
            keys, shown = state.get("open") or [], state.get("shown")
        self.labels: dict[str, tuple[str, str]] = {}  # (key -> name, its full title)
        self.keys = [k for k in dict.fromkeys(keys) if isinstance(k, str) and self._known(k)]
        self.shown = shown if shown in self.keys else None
        self.frames: dict[str, ui.element] = {}
        self.last: str | None = None  # (the PDF shown last)
        scripts({"dockMin": WIDTH_MIN}, "dock.js")
        width = max(WIDTH_MIN, int(annotations.ui_state(WIDTH, WIDTH_DEFAULT) or WIDTH_DEFAULT))
        with context.client.content:
            self.drawer = (
                ui.right_drawer(value=False, bordered=True)
                .props(f"width={width} behavior=desktop")
                .classes("p-0 gap-0")
                .mark("dock")
            )
            with self.drawer, ui.row().classes("w-full h-full no-wrap gap-0"):
                ui.element("div").classes(
                    "vr-dock-splitter shrink-0 h-full bg-grey-4 hover:bg-primary"
                ).style("width:5px; cursor:col-resize").tooltip(_("Drag to resize the PDFs"))
                self.area = ui.element("div").classes("grow h-full relative")
            self.footer = (
                ui.footer(value=bool(self.keys))
                .classes("vr-dock-strip items-center gap-1 py-0 px-2")
                .mark("dock-strip")
            )
        ui.on("vr-dock-open", lambda e: self.open(str(e.args.get("key")), e.args.get("page")))
        ui.on("vr-dock-width", lambda e: self._width(int(e.args)))
        self._update(changed=False)

    def _known(self, key: str) -> bool:
        """Whether ``key`` is a PDF of the person (its name, then, in ``labels``)."""
        m = KEY.fullmatch(key)
        if m is None:
            return False
        kind, i = m[1], int(m[2])
        if kind == "d":
            d = documents.info(i)
            if d is None or d.person_id != self.person_id or documents.file_of(i) is None:
                return False
            self.labels[key] = (d.name, d.name)
            return True
        found = persons.paper(i)
        if found is None or found[1] != self.person_id:
            return False
        title = found[0] or _("(untitled)")
        self.labels[key] = (title, title)
        return True

    @property
    def source(self) -> tuple[str, int] | None:
        """The PDF shown: ("doc", its id) or ("pub", its id); none, None."""
        if self.shown is None:
            return None
        return ("doc" if self.shown[0] == "d" else "pub", int(self.shown[1:]))

    def params(self) -> dict[str, str]:
        """For the address of the page."""
        if not self.keys:
            return {}
        return {"pdfs": ",".join(self.keys), **({"pdf": self.shown} if self.shown else {})}

    def open(self, key: str, page: int | None = None) -> None:
        """Show the PDF ``key`` (opened first, if need be), at ``page``."""
        if key not in self.keys:
            if not self._known(key):
                ui.notify(_("No such PDF"), type="warning")
                return
            self.keys.append(key)
        if key in self.frames and page:
            self.frames[key].client.run_javascript(
                f"document.querySelector('iframe[data-vr-dock=\"{key}\"]')"
                f"?.contentWindow?.vrPdf?.go({int(page)}, null)"
            )
        self.shown = key
        self._update(page=page)

    def toggle(self, key: str) -> None:
        """Its tab clicked: shown, or (shown already) minimized."""
        if key == self.shown:
            self.minimize()
        else:
            self.open(key)

    def minimize(self) -> None:
        self.shown = None
        self._update()

    def restore(self) -> None:
        """The PDF last shown (else the first one) shown again."""
        if self.keys:
            self.open(self.last if self.last in self.keys else self.keys[0])

    def close(self, key: str) -> None:
        """Out of the view (nothing deleted)."""
        if key in self.keys:
            self.keys.remove(key)
        if (frame := self.frames.pop(key, None)) is not None:
            frame.delete()
        if self.shown == key:
            self.shown = None
        self._update()

    def _frame(self, key: str, page: int | None) -> None:
        kind, i = key[0], int(key[1:])
        if kind == "d":
            url = page_url_of(i, embed=True) + (f"&page={int(page)}" if page else "")
        else:
            url = page_url(i, self._period(), page=page, embed=True)
        with self.area:
            self.frames[key] = (
                ui.element("iframe")
                .props(f'src="{url}" data-vr-dock="{key}"')
                .classes("absolute inset-0 w-full h-full")
                .style("border:0")
                .mark(f"dock-frame-{key}")
            )

    def _update(self, *, page: int | None = None, changed: bool = True) -> None:
        """The frames, the drawer, the strip, the address and the state as left, after a
        change."""
        if self.shown is not None:
            self.last = self.shown
            if self.shown not in self.frames:
                self._frame(self.shown, page)
        for key, frame in self.frames.items():
            frame.set_visibility(key == self.shown)
        self.drawer.value = self.shown is not None
        self.footer.value = bool(self.keys)
        self._strip()
        self.footer.client.run_javascript(f"vrDock.shown = {json.dumps(self.shown)}")
        if not changed:
            return
        annotations.save_ui_state(
            _state_key(self.person_id), {"open": self.keys, "shown": self.shown}
        )
        self._on_change()
        for f in self.on_shown:
            f(self.source)
        self.footer.client.run_javascript("setTimeout(() => window.vrFit?.(), 400)")  # (resized)

    def _strip(self) -> None:
        self.footer.clear()
        with self.footer:
            for key in self.keys:
                name, title = self.labels.get(key, (key, key))
                on = key == self.shown
                with (
                    ui.row()
                    .classes("items-center no-wrap gap-0 rounded px-1")
                    .classes("bg-white text-primary" if on else "")
                    .mark(f"dock-tab-{key}")
                ):
                    with (
                        ui.button(on_click=lambda k=key: self.toggle(k))
                        .props(f"flat dense no-caps color={'primary' if on else 'white'}")
                        .tooltip(title + " · " + (_("Click: minimize") if on else _("Click: show")))
                        .mark(f"dock-show-{key}")
                    ):
                        if key[0] == "d":
                            ui.icon("description", size="xs").classes("mr-1")
                        else:
                            pdf_icon().classes("mr-1")
                        ui.label(name).classes("ellipsis max-w-56 text-sm")
                    ui.button(icon="close", on_click=lambda k=key: self.close(k)).props(
                        f"flat dense round size=xs color={'primary' if on else 'white'}"
                    ).tooltip(_("Close it (from the view only)")).mark(f"dock-close-{key}")
            ui.space()
            if self.shown is not None:
                ui.button(icon="keyboard_double_arrow_right", on_click=self.minimize).props(
                    "flat dense round size=sm color=white"
                ).tooltip(_("Minimize the PDFs")).mark("dock-minimize")
            elif self.keys:
                ui.button(icon="keyboard_double_arrow_left", on_click=self.restore).props(
                    "flat dense round size=sm color=white"
                ).tooltip(_("Show the PDF again")).mark("dock-restore")

    def _width(self, width: int) -> None:
        width = max(WIDTH_MIN, width)
        annotations.save_ui_state(WIDTH, width)
        self.drawer.props(f"width={width}")
        self.footer.client.run_javascript("setTimeout(() => window.vrFit?.(), 400)")
