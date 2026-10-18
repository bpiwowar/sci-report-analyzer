"""The PDFs open beside a person's page (their documents, their papers), whatever its tab:
each in a frame on the right, or the left (its viewer's page, embedded: see
pdf_viewer.viewer_frame), one shown at a time; a strip at the bottom lists them, to show one,
minimize them, or close one (from the view only: nothing is deleted), but the person's
documents within the folder, always there, and its + (or PDF files dropped onto it) to add
some. What the page's notes and excerpts ask of a PDF goes
to the one shown (static/dock.js), and a link to a PDF opens it there."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from nicegui import context, ui

from .. import annotations, documents, pdfs, persons
from ..i18n import _
from .documents_page import page_url_of, rename_dialog
from .pdf_viewer import page_url, pdf_icon, scripts

WIDTH = "ui.dock.width"  # AppSetting: the width (px) of the PDFs beside a person's page
WIDTH_DEFAULT, WIDTH_MIN = 720, 320
SIDE = "ui.dock.side"  # AppSetting: where the PDFs are, "right" (the default) or "left"
KEY = re.compile(r"([dp])([0-9]{1,9})")  # (a document: d3; a paper: p12)


# The strip, PDF files dragged over it: outlined.
ui.add_css(".vr-dock-drop{outline:2px dashed white;outline-offset:-4px}", shared=True)


def _state_key(person_id: int) -> str:
    # AppSetting: the PDFs left open beside a person's page, and the one shown
    return f"ui.dock.{person_id}"


class PdfDock:
    """The PDFs open beside the page of a person: from the address (``pdfs``: their keys,
    ``pdf``: the one shown, none: minimized), else as left (``on_change``: to show them
    there; ``exact``: none without ``pdfs``, see nav.popped)."""

    def __init__(
        self,
        person_id: int,
        period: Callable[[], int | None],
        query: dict[str, str],
        on_change: Callable[[], None],
        *,
        exact: bool = False,
    ) -> None:
        """``period``: that of the page (a paper's tags and notes, in its side column)."""
        self.person_id = person_id
        self._period = period
        self._on_change = on_change
        # (the PDF shown changed: its kind and id, or None)
        self.on_shown: list[Callable[[tuple[str, int] | None], None]] = []
        if "pdfs" in query or exact:
            keys, shown = [k for k in query.get("pdfs", "").split(",") if k], query.get("pdf")
        else:
            state = annotations.ui_state(_state_key(person_id))
            state = state if isinstance(state, dict) else {}
            keys, shown = state.get("open") or [], state.get("shown")
        self.labels: dict[str, tuple[str, str]] = {}  # (key -> name, its full title)
        self.keys = [k for k in dict.fromkeys(keys) if isinstance(k, str) and self._known(k)]
        self.shown = shown if shown in self.keys else None
        self.frames: dict[str, ui.element] = {}
        self.docs: list[str] = []  # (the person's documents within the folder: always listed)
        self.folder: int | None = None  # (the period, within a folder, documents are added to)
        self.on_added: Callable[[], None] | None = None  # (a document added: the menu's listing)
        self.last: str | None = None  # (the PDF shown last)
        self.aside: str | None = None  # (the PDF hidden for a tab of the full width)
        self.left = annotations.ui_state(SIDE, "right") == "left"
        scripts({"dockMin": WIDTH_MIN, "dockLeft": self.left}, "dock.js")
        width = max(WIDTH_MIN, int(annotations.ui_state(WIDTH, WIDTH_DEFAULT) or WIDTH_DEFAULT))
        with context.client.content:
            self.drawer = (
                (ui.left_drawer if self.left else ui.right_drawer)(value=False, bordered=True)
                .props(f"width={width} behavior=desktop")
                .classes("p-0 gap-0")
                .mark("dock")
            )
            row = "flex-row-reverse" if self.left else ""  # (the splitter on the page's side)
            with self.drawer, ui.row().classes(f"w-full h-full no-wrap gap-0 {row}"):
                ui.element("div").classes(
                    "vr-dock-splitter shrink-0 h-full bg-grey-4 hover:bg-primary"
                ).style("width:5px; cursor:col-resize").tooltip(_("Drag to resize the PDFs"))
                self.area = ui.element("div").classes("grow h-full relative")
            self.footer = (
                ui.footer(value=bool(self.keys))
                .classes("vr-dock-strip items-center gap-1 py-0 px-2")
                .mark("dock-strip")
            )
            # (the file picker of its +, and PDF files dropped onto it)
            self.picker = (
                ui.upload(multiple=True, auto_upload=True, on_upload=self._upload)
                .props('accept=".pdf,application/pdf"')
                .classes("hidden")
                .mark("dock-upload")
            )
        # (files only: not what is dragged within the page)
        files = "e.dataTransfer.types.includes('Files')"
        self.footer.on(
            "dragover",
            js_handler=f"(e) => {{ if ({files}) {{ e.preventDefault(); "
            "e.currentTarget.classList.add('vr-dock-drop'); } }",
        )
        self.footer.on(
            "dragleave", js_handler="(e) => e.currentTarget.classList.remove('vr-dock-drop')"
        )
        self.footer.on(
            "drop",
            js_handler=f"(e) => {{ e.currentTarget.classList.remove('vr-dock-drop'); "
            f"if ({files}) {{ e.preventDefault(); getElement({self.picker.id})"
            ".$refs.qRef.addFiles(e.dataTransfer.files); } }",
        )
        ui.on("vr-dock-open", lambda e: self.open(str(e.args.get("key")), e.args.get("page")))
        ui.on("vr-dock-width", lambda e: self._width(int(e.args)))
        # (a document renamed from its title above its PDF: its tab too)
        ui.on("vr-dock-renamed", lambda e: self._renamed(str(e.args["key"]), str(e.args["name"])))
        self._update(changed=False)

    def set_documents(self, docs: list[documents.DocView]) -> None:
        """The person's documents within the folder of the page: always in the strip."""
        self.docs = [f"d{d.id}" for d in docs]
        for d in docs:
            self.labels[f"d{d.id}"] = (d.name, d.name)
        for key in [k for k in self.keys if k[0] == "d" and k not in self.docs]:
            if documents.info(int(key[1:])) is None:  # (deleted meanwhile)
                self.close(key)
        self.footer.value = self._strip_shown()
        self._strip()

    def set_folder(self, period_id: int | None) -> None:
        """The period (within a folder) of the page: its documents added from the strip; none,
        not in a folder."""
        self.folder = period_id
        self.footer.value = self._strip_shown()
        self._strip()

    def _strip_shown(self) -> bool:
        return bool(self.keys or self.docs or self.folder is not None)

    async def _upload(self, e) -> None:
        """A PDF added to the person's documents within the folder, and shown."""
        if self.folder is None:
            ui.notify(_("Not in a folder: the documents are added within one"), type="warning")
            return
        try:
            doc_id = documents.add(self.folder, e.file.name, await e.file.read())
        except pdfs.PdfError as err:
            ui.notify(f"{e.file.name}: {err}", type="warning")
            return
        self.set_documents(documents.of_person(self.person_id).get(self.folder, []))
        if self.on_added is not None:
            self.on_added()
        self.open(f"d{doc_id}")

    def move(self) -> None:
        """The PDFs on the other side of the page (it is built again)."""
        annotations.save_ui_state(SIDE, "right" if self.left else "left")
        ui.navigate.reload()

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
        shown = self.shown or self.aside  # (set aside: shown again, the page reloaded)
        return {"pdfs": ",".join(self.keys), **({"pdf": shown} if shown else {})}

    def open(self, key: str, page: int | None = None) -> None:
        """Show the PDF ``key`` (opened first, if need be), at ``page``."""
        self.aside = None
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
        """The PDF last shown (else the first one, or the first document) shown again."""
        if self.keys or self.docs:
            self.open(self.last if self.last in self.keys else (self.keys or self.docs)[0])

    def set_aside(self, aside: bool) -> None:
        """Hidden for a tab of the full width (the timeline), the PDF shown; shown again
        after (unless another was opened meanwhile, or none is)."""
        if aside:
            if self.shown is not None:
                self.aside = self.shown
                self.minimize()
        elif self.aside is not None:
            key, self.aside = self.aside, None
            if self.shown is None and key in self.keys:
                self.open(key)

    def close(self, key: str) -> None:
        """Out of the view (nothing deleted)."""
        if self.aside == key:
            self.aside = None
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
        self.footer.value = self._strip_shown()
        self._strip()
        self.footer.client.run_javascript(
            f"vrDock.shown = {json.dumps(self.shown)}; "  # (the page narrower: see panel.py)
            f"document.body.classList.toggle('vr-docked', {json.dumps(self.shown is not None)})"
        )
        if not changed:
            return
        annotations.save_ui_state(
            _state_key(self.person_id), {"open": self.keys, "shown": self.shown or self.aside}
        )
        self._on_change()
        for f in self.on_shown:
            f(self.source)
        self.footer.client.run_javascript("setTimeout(() => window.vrFit?.(), 400)")  # (resized)

    def _strip(self) -> None:
        self.footer.clear()
        with self.footer:
            for key in self.docs + [k for k in self.keys if k not in self.docs]:
                name, title = self.labels.get(key, (key, key))
                on = key == self.shown
                with (
                    ui.row()
                    .classes("items-center no-wrap gap-0 rounded px-1")
                    .classes("bg-white text-primary" if on else "")
                    .mark(f"dock-tab-{key}")
                ):
                    tip = _("Click: minimize") if on else _("Click: show")
                    if key[0] == "d":
                        tip += " · " + _("Double-click: rename")
                    with (
                        ui.button(on_click=lambda k=key: self.toggle(k))
                        .props(f"flat dense no-caps color={'primary' if on else 'white'}")
                        .tooltip(title + " · " + tip)
                        .mark(f"dock-show-{key}")
                    ) as button:
                        if key[0] == "d":
                            button.on("dblclick", lambda k=key: self.rename(k))
                        if key[0] == "d":
                            ui.icon("description", size="xs").classes("mr-1")
                        else:
                            pdf_icon().classes("mr-1")
                        ui.label(name).classes("ellipsis max-w-56 text-sm")
                    if key not in self.docs:  # (a document of the folder: always there)
                        ui.button(icon="close", on_click=lambda k=key: self.close(k)).props(
                            f"flat dense round size=xs color={'primary' if on else 'white'}"
                        ).tooltip(_("Close it (from the view only)")).mark(f"dock-close-{key}")
            if self.folder is not None:
                ui.button(icon="add", on_click=lambda: self.picker.run_method("pickFiles")).props(
                    "flat dense round size=sm color=white"
                ).tooltip(
                    _("Add a document (PDF) within the folder; or drop PDFs on this bar")
                ).mark("dock-add")
            ui.space()
            ui.button(icon="swap_horiz", on_click=self.move).props(
                "flat dense round size=sm color=white"
            ).tooltip(
                _("Move the PDFs to the right") if self.left else _("Move the PDFs to the left")
            ).mark("dock-move")
            arrows = ("left", "right") if self.left else ("right", "left")
            if self.shown is not None:
                ui.button(icon=f"keyboard_double_arrow_{arrows[0]}", on_click=self.minimize).props(
                    "flat dense round size=sm color=white"
                ).tooltip(_("Minimize the PDFs")).mark("dock-minimize")
            elif self.keys or self.docs:
                ui.button(icon=f"keyboard_double_arrow_{arrows[1]}", on_click=self.restore).props(
                    "flat dense round size=sm color=white"
                ).tooltip(_("Show the PDF again")).mark("dock-restore")

    def rename(self, key: str) -> None:
        """A document's (its tab double-clicked: the two clicks undone)."""
        rename_dialog(
            int(key[1:]), self.labels.get(key, (key, key))[0], lambda n: self._renamed(key, n)
        )

    def _renamed(self, key: str, name: str) -> None:
        if key in self.labels:
            self.labels[key] = (name, name)
            self._strip()

    def _width(self, width: int) -> None:
        width = max(WIDTH_MIN, width)
        annotations.save_ui_state(WIDTH, width)
        self.drawer.props(f"width={width}")
        self.footer.client.run_javascript("setTimeout(() => window.vrFit?.(), 400)")
