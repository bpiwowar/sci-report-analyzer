"""Copying a report: as it is, or (Shift+click) through a copy filter (see copy_filters),
e.g. rich HTML for a form; the filters offered are those enabled in the settings."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict

from nicegui import ui

from .. import copy_filters
from ..i18n import N_, _
from .dialogs import ok_handler, transient_dialog

SHIFT_TIP = N_("Shift+click: through a copy filter (e.g. rich text for a form; see Settings)")


def copy_rich(html: str, plain: str) -> None:
    """Both on the clipboard: pasted formatted where it can be, else as text."""
    ui.run_javascript(
        "navigator.clipboard.write([new ClipboardItem({"
        f"'text/html': new Blob([{json.dumps(html)}], {{type: 'text/html'}}), "
        f"'text/plain': new Blob([{json.dumps(plain)}], {{type: 'text/plain'}})}})])"
    )


def apply_filter(name: str, text: str) -> None:
    f = copy_filters.FILTERS[name]
    copy_rich(*f.apply(text))
    ui.notify(_("Copied ({filter})").format(filter=_(f.label)))


def copy_button(
    text: Callable[[], str | None],
    tip: str,
    mark: str,
    done: str | None = None,
) -> ui.button:
    """A copy button: ``text`` (None: nothing to copy) as it is, else through a filter
    (shift-clicked); ``done``: what is said once copied."""

    def clicked(e) -> None:
        if (value := text()) is None:
            return
        if not (e.args or {}).get("shiftKey"):
            ui.clipboard.write(value)
            ui.notify(done or _("Copied"))
            return
        names = copy_filters.enabled()
        if not names:
            ui.notify(_("No copy filter enabled: see Settings → Copy filters"), type="warning")
        elif len(names) == 1:
            apply_filter(names[0], value)
        else:
            with transient_dialog(_("Copy through")) as (dlg, _card):
                for n in names:
                    ui.button(
                        _(copy_filters.FILTERS[n].label),
                        on_click=ok_handler(dlg, lambda n=n: apply_filter(n, value)),
                    ).props("flat no-caps").mark(f"copy-filter-{n}")

    button = ui.button(icon="content_copy").props("flat dense round size=sm")
    button.on("click", clicked, ["shiftKey"])
    return button.tooltip(f"{tip} · {_(SHIFT_TIP)}").mark(mark)


def filters_tab() -> None:
    """Settings: the copy filters offered by Shift+copy."""
    ui.label(_("Copy filters")).classes("text-lg")
    ui.label(
        _(
            "Shift+click on a copy button (the notes, the excerpts) copies through a filter: "
            "those enabled here (several: the one to use is asked)."
        )
    ).classes("text-sm text-grey")
    on = set(copy_filters.enabled())

    def toggled(name: str, value: bool) -> None:
        (on.add if value else on.discard)(name)
        copy_filters.set_enabled(list(on))

    for name, f in copy_filters.FILTERS.items():
        with ui.column().classes("gap-0"):
            ui.checkbox(
                _(f.label), value=name in on, on_change=lambda e, n=name: toggled(n, e.value)
            ).mark(f"copy-filter-on-{name}")
            ui.label(_(f.description)).classes("text-xs text-grey ml-10")
    tag_rules_section()


def tag_rules_section() -> None:
    """The tags turned into a text (e.g. #influence into a coloured icon) when copied."""
    ui.label(_("Tags turned into a text")).classes("text-base mt-4")
    ui.label(
        _(
            "When copied through a filter, a tag replaced on its line by a text before it "
            "(e.g. an icon) and one after, in a colour (or highlighted), in bold, the line's "
            "text too if checked; e.g. #influence, the tag of the “rayonnement” excerpts "
            "marked in their categories."
        )
    ).classes("text-sm text-grey")
    rules = [asdict(r) for r in copy_filters.tag_rules()]

    def save() -> None:
        copy_filters.set_tag_rules([copy_filters.TagRule(**r) for r in rules])

    @ui.refreshable
    def rows() -> None:
        for i, r in enumerate(rules):
            with ui.row().classes("items-center gap-2 no-wrap"):

                def setter(key: str, r=r):
                    return lambda e: (r.update({key: e.value}), save())

                ui.input(_("Tag"), value=r["tag"], on_change=setter("tag")).props(
                    'dense prefix="#"'
                ).classes("w-32").mark(f"tag-rule-tag-{i}")
                ui.input(_("Before"), value=r["before"], on_change=setter("before")).props(
                    "dense"
                ).classes("w-24").mark(f"tag-rule-before-{i}")
                ui.input(_("After"), value=r["after"], on_change=setter("after")).props(
                    "dense"
                ).classes("w-24").mark(f"tag-rule-after-{i}")
                ui.color_input(_("Colour"), value=r["color"], on_change=setter("color")).props(
                    "dense"
                ).classes("w-32").mark(f"tag-rule-color-{i}")
                ui.checkbox(_("Bold"), value=r["bold"], on_change=setter("bold")).mark(
                    f"tag-rule-bold-{i}"
                )
                ui.checkbox(
                    _("Highlight"), value=r["highlight"], on_change=setter("highlight")
                ).tooltip(_("The colour as background")).mark(f"tag-rule-highlight-{i}")
                ui.checkbox(_("The line too"), value=r["whole"], on_change=setter("whole")).mark(
                    f"tag-rule-whole-{i}"
                )

                def remove(i=i) -> None:
                    del rules[i]
                    save()
                    rows.refresh()

                ui.button(icon="delete", on_click=remove).props("flat dense round size=sm")

    def add() -> None:
        rules.append(asdict(copy_filters.TagRule("")))
        rows.refresh()

    rows()
    ui.button(_("Add a tag"), icon="add", on_click=add).props("flat no-caps").mark("tag-rule-add")
