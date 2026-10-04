"""Transient dialogs: built, opened, and deleted once closed.

A closed dialog is deleted, and with it the UI context of the handlers of its buttons: what
needs it (a notification, a refresh) comes before ``dlg.close()``; the OK button of
``actions`` does so (the dialog is closed once its handler is done). But a dialog opened from
it comes after: one opened within it is deleted with it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from nicegui import ui

from ..i18n import _


@contextmanager
def transient_dialog(
    title: str | None = None, *, width: str = "", persistent: bool = False
) -> Iterator[tuple[ui.dialog, ui.card]]:
    """A dialog (its card of classes ``width``, under a ``title``), opened at the end of the
    block and deleted once closed."""
    with ui.dialog() as dlg, ui.card().classes(width) as card:
        if persistent:
            dlg.props("persistent")
        if title is not None:
            ui.label(title).classes("text-lg font-medium")
        yield dlg, card
    dlg.on_value_change(lambda e: None if e.value or dlg.is_deleted else dlg.delete())
    dlg.open()


def close(dlg: ui.dialog) -> None:
    """Close (and so delete) a dialog, unless already gone (e.g. with its page)."""
    if not dlg.is_deleted:
        dlg.close()


def ok_handler(dlg: ui.dialog, on_ok: Callable[..., Any]) -> Callable[..., Any]:
    """``on_ok`` (given the event's arguments, if it takes them), then the dialog closed,
    unless it returns False (e.g. a field is missing)."""
    # (NiceGUI passes the event only to a handler with a required parameter)
    if inspect.iscoroutinefunction(on_ok):

        async def run_async(*args) -> None:
            if await on_ok(*args) is not False:
                close(dlg)

        async def run_async_event(e) -> None:
            await run_async(e)

        return run_async_event if _takes_args(on_ok) else run_async

    def run(*args) -> None:
        if on_ok(*args) is not False:
            close(dlg)

    def run_event(e) -> None:
        run(e)

    return run_event if _takes_args(on_ok) else run


def _takes_args(fn: Callable[..., Any]) -> bool:
    return any(
        p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        for p in inspect.signature(fn).parameters.values()
    )


def actions(
    dlg: ui.dialog,
    ok_label: str | None = None,
    on_ok: Callable[[], Any] | None = None,
    *,
    danger: bool = False,
    mark: str | None = None,
    icon: str | None = None,
    cancel: str | None = None,
) -> ui.button | None:
    """The row of a dialog's buttons: Cancel, and its OK one (``ok_handler``), returned."""
    with ui.row().classes("w-full justify-end"):
        ui.button(cancel or _("Cancel"), on_click=dlg.close).props("flat")
        if ok_label is None or on_ok is None:
            return None
        ok = ui.button(
            ok_label,
            icon=icon,
            on_click=ok_handler(dlg, on_ok),
            color="negative" if danger else "primary",
        )
        if mark:
            ok.mark(mark)
        return ok


def confirm(
    message: str,
    ok_label: str,
    on_ok: Callable[[], Any],
    *,
    danger: bool = True,
    mark: str | None = None,
    title: str | None = None,
    width: str = "max-w-lg",
) -> None:
    """Ask before doing ``on_ok`` (by default, a destructive action)."""
    with transient_dialog(title, width=width) as (dlg, _card):
        ui.label(message)
        actions(dlg, ok_label, on_ok, danger=danger, mark=mark)
