"""A Markdown editor (for notes and reports): CodeMirror with Markdown highlighting, a
toolbar (bold, lists, links, LaTeX…), and a rendered preview (beside or instead)."""

from __future__ import annotations

import json
from collections.abc import Callable

from nicegui import ui

from ..i18n import N_, _
from .theme import NOTE_EXTRAS

# (icon, tooltip, kind, args): kind "wrap" (before, after, placeholder) or "line" (prefix).
TOOLS = [
    ("format_bold", N_("Bold (⌘/Ctrl-B)"), "wrap", ("**", "**", N_("bold"))),
    ("format_italic", N_("Italic (⌘/Ctrl-I)"), "wrap", ("*", "*", N_("italic"))),
    ("title", N_("Heading"), "line", ("### ",)),
    ("format_list_bulleted", N_("List"), "line", ("- ",)),
    ("format_list_numbered", N_("Numbered list"), "line", ("1. ",)),
    ("format_quote", N_("Quote"), "line", ("> ",)),
    ("code", N_("Code"), "wrap", ("`", "`", N_("code"))),
    ("link", N_("Link"), "wrap", ("[", "](https://)", N_("text"))),
    ("functions", N_("LaTeX ($…$ inline, $$…$$ display)"), "wrap", ("$", "$", "x^2")),
]

MODES = {"edit": N_("Edit"), "split": N_("Split"), "preview": N_("Preview")}


def quote(text: str, where: str = "") -> str:
    """``text`` as a Markdown quote (one paragraph: e.g. the lines selected in a PDF), followed
    by ``where`` it is from (e.g. its page), if any."""
    return "> " + " ".join(text.split()) + (f" ({where})" if where else "")


_SHARE = "; flex:1 1 0; min-height:6rem"  # (a stacked editor and preview, sharing the height)


class MarkdownEditor:
    def __init__(
        self,
        value: str = "",
        *,
        on_change: Callable[[str], None] | None = None,
        render: Callable[[str], str] | None = None,
        mode: str = "edit",
        mark: str | None = None,
        height: str = "10rem",
        keymap: dict | None = None,
        toolbar: Callable[[], None] | None = None,
        stacked: bool = False,
        fill: bool = False,
    ) -> None:
        """``render``: the Markdown shown in the preview (e.g. with citations substituted);
        ``toolbar``: more buttons, at the toolbar's right; ``stacked``: the preview below the
        editor (else beside it); ``fill``: the height left in its (flex) column, rather than
        ``height``."""
        self._render = render or (lambda t: t)
        # (set by the note editors saving it: unsaved text typed, and a text saved elsewhere)
        self.is_dirty: Callable[[], bool] = lambda: False
        self.adopt: Callable[[str], None] = lambda text: setattr(self, "value", text)
        self._on_change = on_change
        if fill:
            # (stacked: the editor and the preview share the height; else both as high as it)
            height = "0" if stacked else "100%"
        with ui.column().classes("w-full gap-1" + (" grow min-h-0" if fill else "")) as self.box:
            with ui.row().classes("w-full items-center gap-0"):
                for icon, tip, kind, args in TOOLS:
                    ui.button(
                        icon=icon,
                        on_click=lambda kind=kind, args=args: (
                            self.wrap(*args[:2], _(args[2]))
                            if kind == "wrap"
                            else self.prefix_lines(*args)
                        ),
                    ).props("flat dense round size=sm").tooltip(_(tip))
                ui.space()
                if toolbar:
                    toolbar()
                self.mode = (
                    ui.toggle(
                        {k: _(v) for k, v in MODES.items()},
                        value=mode,
                        on_change=lambda: self._layout(),
                    )
                    .props("dense flat no-caps size=sm")
                    .tooltip(_("Edit, edit beside the preview, or the preview only"))
                )
                if mark:
                    self.mode.mark(f"{mark}-mode")
            panes = ui.column().classes("w-full gap-2") if stacked else ui.row()
            with panes.classes(
                "w-full no-wrap gap-2 items-stretch" + (" grow min-h-0" if fill else "")
            ):
                keys = {
                    "Mod-b": lambda: self.wrap("**", "**", _("bold")),
                    "Mod-i": lambda: self.wrap("*", "*", _("italic")),
                }
                self.editor = (
                    ui.codemirror(
                        value or "",
                        language="Markdown",
                        line_wrapping=True,
                        indent="  ",
                        keymap={**keys, **(keymap or {})},
                        on_change=lambda e: self._changed(e.value),
                    )
                    .classes("w-full border rounded")
                    .style(
                        f"height:{height}; font-size:0.875rem"
                        + (_SHARE if fill and stacked else "")
                    )
                )
                if mark:
                    self.editor.mark(mark)
                self.preview = (
                    ui.markdown(extras=NOTE_EXTRAS)
                    .classes("w-full vr-note overflow-auto border rounded px-3")
                    .style(f"height:{height}" + (_SHARE if fill and stacked else ""))
                )
        self._layout()
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        """The preview and the editor follow each other's scrolling (at the same proportion);
        the scroll caused by following is ignored, else they would chase each other."""
        self.editor.client.run_javascript(
            "(() => { let n = 0; const t = setInterval(() => {"
            f" const v = getElement({self.editor.id})?.editor,"
            f" p = getHtmlElement({self.preview.id});"
            " if (++n > 50) clearInterval(t); if (!v || !p) return; clearInterval(t);"
            " const e = v.scrollDOM; let busy = false;"
            " const frac = (x) => x.scrollTop / Math.max(1, x.scrollHeight - x.clientHeight);"
            " const follow = (from, to) => { if (busy) return; busy = true;"
            " to.scrollTop = frac(from) * (to.scrollHeight - to.clientHeight);"
            " requestAnimationFrame(() => { busy = false; }); };"
            " e.addEventListener('scroll', () => follow(e, p));"
            " p.addEventListener('scroll', () => follow(p, e));"
            " }, 100); })()"
        )

    @property
    def value(self) -> str:
        return self.editor.value or ""

    @value.setter
    def value(self, text: str) -> None:
        self.editor.value = text

    def _changed(self, text: str) -> None:
        if self.mode.value != "edit":
            self.refresh_preview()
        if self._on_change:
            self._on_change(text or "")

    def refresh_preview(self) -> None:
        self.preview.content = self._render(self.value)

    def _layout(self) -> None:
        mode = self.mode.value or "edit"
        self.editor.set_visibility(mode != "preview")
        self.preview.set_visibility(mode != "edit")
        if mode != "edit":
            self.refresh_preview()

    # ---- Editing at the cursor (in the browser) ----

    def _js(self, body: str) -> None:
        self.editor.client.run_javascript(
            f"(() => {{ const v = getElement({self.editor.id})?.editor; if (!v) return; "
            f"{body}; v.focus(); }})()"
        )

    def insert(self, text: str) -> None:
        """Insert ``text`` at the cursor (replacing the selection)."""
        if self.mode.value == "preview":
            self.mode.value = "split"
        self._js(f"v.dispatch(v.state.replaceSelection({json.dumps(text)}))")

    def insert_block(self, text: str) -> None:
        """Insert ``text`` as a paragraph of its own at the cursor (replacing the selection;
        within a line: after it; at the start: at the end, as in an editor never clicked)."""
        if self.mode.value == "preview":
            self.mode.value = "split"
        self._js(
            "const d = v.state.doc, s = v.state.selection.main; "
            "let [from, to] = s.head === 0 && d.length ? [d.length, d.length] : [s.from, s.to]; "
            "const line = d.lineAt(from); "
            "if (from === to && from > line.from) from = to = line.to; "
            "const before = d.sliceString(0, from), after = d.sliceString(to); "
            "const pre = !before || before.endsWith('\\n\\n') ? '' "
            ": before.endsWith('\\n') ? '\\n' : '\\n\\n'; "
            "const post = after.startsWith('\\n\\n') ? '' : after.startsWith('\\n') ? '\\n' "
            ": after ? '\\n\\n' : '\\n'; "
            f"const t = pre + {json.dumps(text)} + post; "
            "v.dispatch({changes: {from, to, insert: t}, selection: {anchor: from + t.length}, "
            "scrollIntoView: true})"
        )

    def wrap(self, before: str, after: str, placeholder: str = "") -> None:
        """Put the selection (else ``placeholder``, selected) between ``before`` and ``after``."""
        b, a, ph = (json.dumps(x) for x in (before, after, placeholder))
        self._js(
            "const s = v.state.selection.main; "
            f"const t = v.state.sliceDoc(s.from, s.to) || {ph}; "
            f"v.dispatch({{changes: {{from: s.from, to: s.to, insert: {b} + t + {a}}}, "
            f"selection: {{anchor: s.from + {b}.length, head: s.from + {b}.length + t.length}}}})"
        )

    def prefix_lines(self, prefix: str) -> None:
        """Start each line of the selection with ``prefix``."""
        self._js(
            "const s = v.state.selection.main, d = v.state.doc; const changes = []; "
            "for (let l = d.lineAt(s.from).number; l <= d.lineAt(s.to).number; l++) "
            f"changes.push({{from: d.line(l).from, insert: {json.dumps(prefix)}}}); "
            "v.dispatch({changes})"
        )

    def focus(self) -> None:
        self._js("")
