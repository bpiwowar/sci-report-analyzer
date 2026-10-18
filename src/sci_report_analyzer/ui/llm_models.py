"""Ollama's models: downloading a missing one (with its size, confirmed, and a progress
dialog that can be cancelled) wherever a model is chosen, and the installed ones, to download
or delete (only if nothing uses them). Ollama only: an OpenAI-compatible server has no
download, nor Ollama's cloud models."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nicegui import background_tasks, context, ui

from .. import llm
from ..i18n import _
from .dialogs import actions, close, close_then, confirm, transient_dialog


async def _call(fn: Callable[[], Awaitable | None] | None) -> None:
    if fn is not None and asyncio.iscoroutine(result := fn()):
        await result


def _with_size(name: str, size: int | None) -> str:
    return f"{name} ({llm.format_size(size)})" if size else name


async def pull_dialog(url: str, name: str) -> bool:
    """Downloads ``name`` to the Ollama server at ``url`` in a progress dialog (status, bar,
    bytes, Cancel); whether it succeeded (an error is said, a cancellation is not one)."""
    with transient_dialog(_("Downloading {model}").format(model=name), persistent=True) as (
        dlg,
        _card,
    ):
        status = ui.label(_("Starting…")).classes("text-sm").mark("llm-pull-status")
        bar = ui.linear_progress(value=0, show_value=False).props("instant-feedback")
        done = ui.label().classes("text-xs text-grey").mark("llm-pull-bytes")
        with ui.row().classes("w-full justify-end"):
            cancel = ui.button(_("Cancel")).props("flat").mark("llm-pull-cancel")

    def progress(text: str, completed: int, total: int) -> None:
        if dlg.is_deleted:
            return
        status.set_text(text or _("Downloading…"))
        if total:
            bar.set_value(min(1.0, completed / total))
            done.set_text(f"{llm.format_size(completed)} / {llm.format_size(total)}")

    task = asyncio.ensure_future(llm.pull_model(url, name, progress))
    cancel.on_click(task.cancel)
    try:
        await asyncio.wait({task})
    finally:  # (the page left)
        task.cancel()
        close(dlg)
    if task.cancelled():
        ui.notify(_("Download of {model} cancelled").format(model=name), type="warning")
        return False
    if error := task.exception():
        ui.notify(
            _("Cannot download {model}: {error}").format(model=name, error=error), type="negative"
        )
        return False
    ui.notify(_("{model} downloaded").format(model=name), type="positive")
    return True


async def offer_download(
    url: str, name: str, on_done: Callable[[], Awaitable | None] | None = None
) -> None:
    """Asks to download ``name`` (with its size, if known), then does it (see pull_dialog);
    ``on_done`` is called once downloaded."""
    size = await llm.manifest_size(name)
    message = (
        _("Download {model}?").format(model=_with_size(name, size))
        if size
        else _("Download {model}? Its size is not known.").format(model=name)
    )

    opener = context.slot

    async def pull() -> None:
        with opener:  # (the confirmation is gone)
            if await pull_dialog(url, name):
                await _call(on_done)

    with transient_dialog(_("Download a model")) as (dlg, _card):
        ui.label(message).mark("llm-pull-message")
        actions(
            dlg,
            _("Download"),
            lambda: close_then(dlg, lambda: background_tasks.create(pull())),
            icon="download",
            mark="llm-pull-confirm",
        )


def watch(
    select: ui.select,
    api: Callable[[], str],
    url: Callable[[], str],
    on_done: Callable[[], Awaitable | None] | None = None,
) -> None:
    """Offers to download the model chosen or typed in ``select`` if the Ollama server at
    ``url()`` has not got it (``api()``: where the model is used from; no offer for an
    OpenAI-compatible server nor a cloud model), then selects it; ``on_done``: e.g. reloads
    the models."""

    async def changed(e) -> None:
        name = e.value if isinstance(e.value, str) else ""
        if name == llm.NONE or not llm.can_pull(api(), name):
            return
        try:
            installed = await llm.installed_models(url() or llm.DEFAULT_URL)
        except Exception:
            return  # (the server is not running: nothing to offer)
        if llm.is_installed(name, installed):
            return

        async def downloaded() -> None:
            options = select.options
            if name not in options:
                options = {**options, name: name} if isinstance(options, dict) else [*options, name]
            select.set_options(options, value=name)
            await _call(on_done)

        await offer_download(url() or llm.DEFAULT_URL, name, downloaded)

    select.on_value_change(changed)


def manager(
    url: Callable[[], str],
    on_change: Callable[[], Awaitable | None] | None = None,
) -> Callable[[], Awaitable[None]]:
    """The models installed on the Ollama server at ``url()`` with their sizes, a field to
    download another (its size said before), and a button deleting each if no setting uses
    it; returns the coroutine function reloading them. ``on_change``: after a download or a
    deletion."""
    ui.label(
        _(
            "Downloads and deletions are for the Ollama server only (an OpenAI-compatible "
            "server manages its own models)."
        )
    ).classes("text-xs text-grey max-w-[48rem]").mark("llm-models-note")
    state: dict = {"models": {}, "error": ""}

    async def changed() -> None:
        await refresh()
        await _call(on_change)

    async def download() -> None:
        name = (field.value or "").strip()
        if not name:
            return
        if not llm.can_pull("ollama", name):
            ui.notify(_("A cloud model is not downloaded"), type="warning")
            return
        if llm.is_installed(name, state["models"]):
            ui.notify(_("{model} is already downloaded").format(model=name), type="warning")
            return
        await offer_download(url() or llm.DEFAULT_URL, name, changed)
        field.set_value("")

    def delete(name: str, size: int) -> None:
        if used := llm.model_users(name):  # (since the list was shown)
            ui.notify(_("Used by: {users}").format(users=", ".join(used)), type="warning")
            return

        async def go() -> None:
            try:
                await llm.delete_model(url() or llm.DEFAULT_URL, name)
            except Exception as e:
                ui.notify(
                    _("Cannot delete {model}: {error}").format(model=name, error=e),
                    type="negative",
                )
                return
            ui.notify(_("{model} deleted").format(model=name))
            await changed()

        confirm(
            _(
                "This deletes {model} from the Ollama server; it must be downloaded again to "
                "be used."
            ).format(model=_with_size(name, size)),
            _("Delete"),
            go,
            mark="llm-delete-confirm",
            title=_("Delete a model"),
        )

    with ui.row().classes("items-center gap-2"):
        field = (
            ui.input(_("Model to download"), placeholder="qwen3:8b")
            .classes("w-[24rem]")
            .on("keydown.enter", download)
            .mark("llm-pull-name")
        )
        ui.button(_("Download"), icon="download", on_click=download).props("no-caps").mark(
            "llm-pull-go"
        )

    @ui.refreshable
    def listing() -> None:
        if state["error"]:
            ui.label(state["error"]).classes("text-xs text-negative")
            return
        if not state["models"]:
            ui.label(_("No model installed")).classes("text-xs text-grey")
        for name, size in sorted(state["models"].items()):
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"llm-installed-{name}"):
                ui.label(name).classes("text-sm w-[20rem] break-all")
                ui.label(llm.format_size(size) if size else "").classes("text-xs text-grey w-20")
                button = (
                    ui.button(icon="delete", on_click=lambda n=name, s=size: delete(n, s))
                    .props("flat dense round size=sm color=negative")
                    .mark(f"llm-model-delete-{name}")
                )
                if used := llm.model_users(name):
                    button.disable()
                    button.tooltip(_("Used by: {users}").format(users=", ".join(used)))
                else:
                    button.tooltip(_("Delete this model from the server"))

    listing()

    async def refresh() -> None:
        try:
            state["models"] = await llm.installed_models(url() or llm.DEFAULT_URL)
            state["error"] = ""
        except Exception as e:
            state["error"] = _("Cannot reach Ollama: {error}").format(error=e)
        listing.refresh()

    ui.timer(0.1, refresh, once=True)
    return refresh
