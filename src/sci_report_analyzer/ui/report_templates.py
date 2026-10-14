"""The report templates (see report_templates): edited in the settings, picked to fill a
folder's starting notes or a report, made from a written report."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import report_templates
from ..i18n import N_, _
from . import unsaved
from .dialogs import actions, close_then, confirm, ok_handler, transient_dialog

HELP = N_(
    "The texts a report can start from: its headings, the blocks `[[publications]]` (the "
    "summary of the publications), `[[excerpts]]` (the excerpts by category; "
    "`[[excerpts#id]]`: those of the category of that id, then left out of the others), "
    "`[[cv]]` (the person's career), `[[references]]` (the papers cited), and guidance as "
    "comments `%% … %%` (left out when shown or copied). A folder's starting notes are copied "
    "from one (its settings: Reports), and a report from one (its toolbar), which also makes "
    "one from a report: its headings and blocks."
)


def settings_section() -> None:
    """Settings → Report templates."""
    items = report_templates.load()
    ui.markdown(_(HELP)).classes("text-sm text-grey")

    @ui.refreshable
    def rows() -> None:
        for i, t in enumerate(items):
            with ui.column().classes("w-full gap-1").mark(f"report-skeleton-{i}"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.input(
                        _("Name"),
                        value=t.name,
                        on_change=lambda e, t=t: setattr(t, "name", e.value or ""),
                    ).props("dense outlined").classes("w-80").mark(f"report-skeleton-name-{i}")
                    ui.button(icon="delete", on_click=lambda i=i: remove(i)).props(
                        "flat round dense color=negative"
                    ).mark(f"report-skeleton-delete-{i}")
                ui.textarea(
                    value=t.text,
                    on_change=lambda e, t=t: setattr(t, "text", e.value or ""),
                ).props("dense outlined autogrow").classes("w-full font-mono text-sm").mark(
                    f"report-skeleton-text-{i}"
                )

    def remove(i: int) -> None:
        del items[i]
        rows.refresh()

    def add() -> None:
        items.append(report_templates.ReportTemplate(_("New template"), "## \n"))
        rows.refresh()

    def reset() -> None:
        items[:] = report_templates.default_templates()
        rows.refresh()

    def save() -> bool:
        try:
            report_templates.save(items)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return False
        rows.refresh()
        ui.notify(_("Saved"), type="positive")
        return True

    with ui.column().classes("w-full gap-4"):
        rows()
    save = unsaved.track(lambda: items, save)
    with ui.row().classes("mt-2"):
        ui.button(_("Add a template"), icon="add", on_click=add).props("flat").mark(
            "report-skeleton-add"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save).mark("report-skeletons-save")


def pick_menu(on_pick: Callable[[str], None], mark: str) -> None:
    """The items of a menu: a template each (its text given to ``on_pick``)."""
    items = report_templates.load()
    for i, t in enumerate(items):
        ui.menu_item(t.name, on_click=lambda t=t: on_pick(t.text)).mark(f"{mark}-{i}")
    if not items:
        ui.label(_("No report template (Settings → Report templates)")).classes(
            "text-sm text-grey px-4 py-2"
        )


def save_dialog(report: str) -> None:
    """Save the structure of a report (its headings and blocks) as a template, named."""
    text = report_templates.outline(report)
    if not text:
        ui.notify(_("No heading nor block to keep in a template"), type="warning")
        return
    with transient_dialog(_("Save as a report template"), width="w-[36rem]") as (dlg, _card):
        ui.label(
            _("Its headings and blocks, what is written left out (to edit in the settings):")
        ).classes("text-sm text-grey")
        ui.markdown(f"```\n{text}```").classes("w-full")
        names = [t.name for t in report_templates.load()]
        name = (
            ui.input(_("Name"), autocomplete=names)
            .classes("w-full")
            .mark("report-skeleton-save-name")
        )

        def store() -> None:
            report_templates.add(name.value, text)
            ui.notify(_("Saved as “{name}”").format(name=name.value.strip()), type="positive")

        def ok() -> bool | None:
            if not (name.value or "").strip():
                ui.notify(_("A report template needs a name"), type="negative")
                return False
            if name.value.strip() in names:  # (asked once this dialog is closed)
                close_then(
                    dlg,
                    lambda: confirm(
                        _("Replace the template “{name}”?").format(name=name.value.strip()),
                        _("Replace"),
                        store,
                        mark="report-skeleton-replace",
                    ),
                )
                return False
            store()
            return None

        name.on("keydown.enter", ok_handler(dlg, ok))
        actions(dlg, _("Save"), ok, mark="report-skeleton-save-ok")
