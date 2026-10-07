"""Settings → Shared settings: the user's name on their decisions, the group's repository,
and its pull (with the conflicts to resolve) and push (the changes to propose)."""

from __future__ import annotations

from nicegui import run, ui

from .. import shared, shared_git
from ..i18n import _, ngettext
from .dialogs import actions, transient_dialog


def shared_tab() -> None:
    cfg = shared_git.load_config()
    ui.label(_("Shared settings")).classes("text-lg")
    ui.label(
        _(
            "Share the matching settings and the venues' manual decisions (kinds, levels, "
            "variants, venue rules…) with a group, through a git repository: one file per "
            "venue, reviewed like code. A pull takes the group's changes and keeps yours; a "
            "push proposes yours. People, papers and imported JCR rows are never shared."
        )
    ).classes("text-sm text-grey")
    identity = (
        ui.input(_("Your name on your decisions"), value=cfg.identity)
        .props("dense")
        .classes("w-80")
        .tooltip(_("Saved with each decision you make (shared with the group): a short handle"))
        .mark("shared-identity")
    )
    url = (
        ui.input(
            _("Group repository"),
            value=cfg.url,
            placeholder="git@github.com:lab/venue-settings.git",
        )
        .props("dense")
        .classes("w-full")
        .tooltip(
            _(
                "A git URL (SSH or HTTPS, with your usual git credentials) or the path of a "
                "repository"
            )
        )
        .mark("shared-url")
    )
    with ui.row().classes("items-center gap-4"):
        branch = ui.input(_("Branch"), value=cfg.branch).props("dense").classes("w-40")
        push_mode = ui.radio(
            {
                "branch": _("Push on a branch of mine (a pull request)"),
                "direct": _("Push on the group's branch"),
            },
            value=cfg.push,
        ).props("inline dense")

    def save() -> shared_git.SharedConfig:
        c = shared_git.load_config()
        changed_repo = (url.value or "").strip() != c.url or (branch.value or "main") != c.branch
        c.identity = (identity.value or "").strip()
        c.url = (url.value or "").strip()
        c.branch = (branch.value or "").strip() or "main"
        c.push = push_mode.value
        if changed_repo:  # (another group: its first pull)
            c.base = c.pulled_at = None
        shared_git.save_config(c)
        status.refresh()
        return c

    @ui.refreshable
    def status() -> None:
        c = shared_git.load_config()
        if c.pulled_at:
            text = _("Last pull: {when} (commit {sha})").format(
                when=c.pulled_at.replace("T", " ")[:16], sha=(c.base or "—")[:8]
            )
        else:
            text = _("Never pulled from this repository.")
        ui.label(text).classes("text-sm text-grey").mark("shared-status")

    with ui.row().classes("items-center gap-2"):
        ui.button(
            _("Save"), on_click=lambda: (save(), ui.notify(_("Saved"), type="positive"))
        ).props("dense").mark("shared-save")
        ui.button(_("Pull"), icon="download", on_click=lambda: pull()).props("dense").mark(
            "shared-pull"
        )
        ui.button(_("Push"), icon="upload", on_click=lambda: push()).props("dense").mark(
            "shared-push"
        )
    status()

    async def pull() -> None:
        if not save().url:
            ui.notify(_("Set the group repository first"), type="warning")
            return
        try:
            p = await run.io_bound(shared_git.prepare_pull)
        except (shared_git.GitError, shared.FormatError) as err:
            _failed(err)
            return
        pull_dialog(p, status.refresh)

    async def push() -> None:
        c = save()
        if not c.url:
            ui.notify(_("Set the group repository first"), type="warning")
            return
        try:
            out = await run.io_bound(shared_git.prepare_push)
        except shared_git.OutOfDate as err:
            ui.notify(str(err), type="warning")
            return
        except (shared_git.GitError, shared.FormatError) as err:
            _failed(err)
            return
        if not out.keys:
            ui.notify(_("Nothing to push: the group has all your decisions"), type="info")
            return
        push_dialog(out, status.refresh)


def _failed(err: Exception) -> None:
    ui.notify(
        _("Shared settings: {error}").format(error=err),
        type="negative",
        multi_line=True,
        timeout=0,
        close_button=True,
    )


def _line(key: str, items: shared.Items, before, after, mark: str) -> None:
    with ui.row().classes("items-center no-wrap w-full gap-2 border-b py-1").mark(mark):
        ui.label(shared.describe(key, items)).classes("w-1/3 text-sm font-medium")
        ui.label(shared.show(before)).classes("w-1/4 text-sm text-grey break-all")
        ui.icon("arrow_forward", size="xs").classes("text-grey")
        ui.label(shared.show(after)).classes("grow text-sm break-all")
        if who := shared.author(after):
            ui.label(who).classes("text-xs text-grey whitespace-nowrap")


def pull_dialog(p: shared_git.Pull, done) -> None:
    """The group's changes to take, and the conflicts to resolve."""
    take: set[str] = {c.key for c in p.conflicts} if p.first else set()
    names = {**p.local, **p.remote}
    with transient_dialog(_("Pull the group's settings"), width="w-full max-w-4xl") as (dlg, _c):

        @ui.refreshable
        def body() -> None:
            incoming = [k for k in p.incoming(take) if k not in {c.key for c in p.conflicts}]
            if not incoming and not p.conflicts:
                ui.label(_("Nothing new: you have the group's settings.")).classes("text-positive")
            if incoming:
                ui.label(
                    ngettext(
                        "{n} change from the group", "{n} changes from the group", len(incoming)
                    ).format(n=len(incoming))
                ).classes("font-medium")
                with ui.scroll_area().classes("w-full max-h-72"):
                    for k in incoming:
                        new = shared.resolved(p.merged, p.conflicts, take).get(k)
                        _line(k, names, p.local.get(k), new, f"shared-in-{k}")
            if p.conflicts:
                ui.label(
                    ngettext(
                        "{n} conflict: changed here and by the group",
                        "{n} conflicts: changed here and by the group",
                        len(p.conflicts),
                    ).format(n=len(p.conflicts))
                ).classes("font-medium mt-2")
                with ui.row():
                    ui.button(_("Take all the group's"), on_click=lambda: every(True)).props(
                        "dense flat"
                    )
                    ui.button(_("Keep all mine"), on_click=lambda: every(False)).props("dense flat")
                with ui.scroll_area().classes("w-full max-h-72"):
                    for c in p.conflicts:
                        with ui.row().classes("items-center no-wrap w-full gap-2 border-b py-1"):
                            ui.label(shared.describe(c.key, names)).classes("w-1/3 text-sm")
                            ui.toggle(
                                {
                                    False: _("mine: {value}").format(value=shared.show(c.local)),
                                    True: _("group's: {value}").format(value=shared.show(c.remote))
                                    + (f" ({who})" if (who := shared.author(c.remote)) else ""),
                                },
                                value=c.key in take,
                                on_change=lambda e, k=c.key: choose(k, e.value),
                            ).props("dense no-caps size=sm").mark(f"shared-conflict-{c.key}")
            if outgoing := p.outgoing():
                ui.label(
                    ngettext(
                        "{n} decision of yours the group lacks: push it after",
                        "{n} decisions of yours the group lacks: push them after",
                        len(outgoing),
                    ).format(n=len(outgoing))
                ).classes("text-sm text-grey mt-2")

        def choose(key: str, group: bool) -> None:
            (take.add if group else take.discard)(key)
            body.refresh()

        def every(group: bool) -> None:
            take.clear()
            if group:
                take.update(c.key for c in p.conflicts)
            body.refresh()

        body()

        def apply() -> None:
            counts = shared_git.finish_pull(p, take)
            ui.notify(
                _("Pulled: {venues} venues changed").format(venues=counts["venues"])
                + (" · " + _("matching settings changed") if counts["settings"] else ""),
                type="positive",
            )
            done()

        actions(dlg, _("Apply"), apply, mark="shared-pull-apply")


def push_dialog(out: shared_git.Outgoing, done) -> None:
    """The local changes to propose (all by default), and the commit's message."""
    names = {**out.base, **out.local}
    page = ui.context.client.content
    picked = {k: True for k in out.keys}
    with transient_dialog(_("Push your decisions"), width="w-full max-w-4xl") as (dlg, _c):
        ui.label(
            ngettext(
                "{n} decision the group lacks", "{n} decisions the group lacks", len(out.keys)
            ).format(n=len(out.keys))
        ).classes("font-medium")
        with ui.scroll_area().classes("w-full max-h-96"):
            for k in out.keys:
                with ui.row().classes("items-center no-wrap w-full gap-2"):
                    ui.checkbox(
                        value=True, on_change=lambda e, k=k: picked.__setitem__(k, e.value)
                    ).props("dense").mark(f"shared-out-{k}")
                    _line(k, names, out.base.get(k), out.local.get(k), f"shared-out-line-{k}")
        message = (
            ui.textarea(_("What and why (the commit's message)"))
            .props("dense outlined autogrow")
            .classes("w-full")
            .mark("shared-message")
        )

        async def send() -> None:
            keys = {k for k, on in picked.items() if on}
            if not keys:
                ui.notify(_("Choose at least one decision"), type="warning")
                return
            try:
                res = await run.io_bound(shared_git.push, out, keys, message.value or "")
            except shared_git.OutOfDate as err:
                ui.notify(str(err), type="warning")
                return
            except shared_git.GitError as err:
                _failed(err)
                return
            dlg.close()
            done()
            with page:  # (not in this dialog: deleted once closed)
                pushed_dialog(res)

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Push"), icon="upload", on_click=send).mark("shared-push-send")


def pushed_dialog(res: shared_git.Pushed) -> None:
    with transient_dialog(_("Pushed"), width="max-w-xl") as (dlg, _c):
        if res.sha is None:
            ui.label(_("Nothing changed in the group's files."))
        elif res.direct:
            ui.label(
                _("Your decisions are on the group's branch {branch}.").format(branch=res.branch)
            )
        else:
            ui.label(
                _(
                    "Your decisions are on the branch {branch}: open a pull request from it "
                    "for the group to review."
                ).format(branch=res.branch)
            )
            for link in res.links:
                ui.link(link, link, new_tab=True).classes("text-sm break-all")
        actions(dlg, cancel=_("Close"))
