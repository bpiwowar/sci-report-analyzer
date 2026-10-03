"""Import past years of the Scimago ranking: scimagojr.com blocks scripts, so the user
downloads a year's CSV in their browser and drops it here (kept, then merged on load)."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ..i18n import _
from ..ranking import datasets
from ..ranking.service import service


def import_year(text: str, name: str, year: int | None = None) -> int:
    """Import a Scimago CSV (its year: ``year``, else read from the file name); the year."""
    year = year or datasets.scimago_year_in(name)
    if year is None:
        raise ValueError(_("No year in the file name (e.g. “scimagojr 2021.csv”)"))
    n = datasets.import_scimago(text, year)
    service.invalidate(data=True, clear_cache=True)
    ui.notify(_("Scimago {year}: {n} journals imported").format(year=year, n=n), type="positive")
    return year


def uploader(year: int | None = None, on_done: Callable[[int], None] | None = None) -> None:
    """A drop zone for a Scimago CSV (of ``year``, else the year in its file name)."""

    async def upload(e) -> None:
        try:
            y = import_year(await e.file.text(), e.file.name, year)
        except ValueError as err:
            ui.notify(str(err), type="warning")
            return
        if on_done:
            on_done(y)

    ui.upload(
        on_upload=upload,
        auto_upload=True,
        label=_("Scimago {year} CSV").format(year=year) if year else _("Scimago CSV"),
    ).props('accept=".csv,.txt" flat bordered').classes("w-full").mark("scimago-upload")


def import_dialog(year: int, on_done: Callable[[int], None] | None = None) -> None:
    """Download the Scimago ranking of ``year`` (in the browser), then import it."""
    with ui.dialog() as dlg, ui.card().classes("w-[30rem]"):
        ui.label(_("Scimago {year}").format(year=year)).classes("text-lg")
        ui.label(
            _(
                "Scimago can't be downloaded by the app: open its {year} ranking, then "
                "drop the downloaded CSV below (kept: past years don't change)."
            ).format(year=year)
        ).classes("text-sm")
        ui.link(
            _("Download Scimago {year}").format(year=year),
            datasets.scimago_download_url(year),
            new_tab=True,
        ).mark("scimago-download")

        def done(y: int) -> None:
            dlg.close()
            if on_done:
                on_done(y)

        uploader(year, done)
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Close"), on_click=dlg.close).props("flat")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def missing_hint(year: int | None, on_done: Callable[[int], None] | None = None) -> None:
    """When the Scimago ranking of a paper's ``year`` is not loaded: a way to import it."""
    if not service.scimago_missing(year):
        return
    with ui.row().classes("items-center gap-1 text-sm text-grey").mark("scimago-missing"):
        ui.icon("history", size="xs")
        ui.label(_("Scimago {year} not loaded (quartile from another year)").format(year=year))
        ui.button(_("Import"), on_click=lambda: import_dialog(year, on_done)).props(
            "flat dense size=sm"
        ).mark("scimago-import")
