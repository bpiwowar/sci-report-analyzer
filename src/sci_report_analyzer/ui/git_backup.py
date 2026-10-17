"""The backup in a git repository (see git_backup): the header's button (back up now) and
Settings → Backup (where, how often, the last one)."""

from __future__ import annotations

from datetime import datetime

from nicegui import ui

from .. import config, git_backup
from ..i18n import _
from .theme import int_or_none


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def _off_reason(cfg: git_backup.BackupConfig) -> str | None:
    """Why a backup cannot be made (none: it can)."""
    if not git_backup.has_git():
        return _("git is not installed: backups cannot be made")
    if cfg.directory is None:
        return _("Backups are off: no directory set (Settings → Backup)")
    return None


async def back_up_now(after=None) -> None:
    """Back up now, saying how it went (``after``: called once done)."""
    cfg = git_backup.load_config()
    if why := _off_reason(cfg):
        ui.notify(why, type="warning")
        return
    note = ui.notification(_("Backing up…"), spinner=True, timeout=None)
    try:
        res = await git_backup.backup(cfg)
    except git_backup.GitError as err:
        note.dismiss()
        ui.notify(_("The backup failed: {error}").format(error=err), type="negative")
    else:
        note.dismiss()
        if res.commit:
            ui.notify(
                _("Backed up: {n} files changed (commit {sha})").format(
                    n=res.changed, sha=res.commit[:8]
                ),
                type="positive",
            )
        else:
            ui.notify(_("Backed up: nothing changed since the last backup"), type="info")
    if after is not None:
        after()


def backup_button(compact: bool = False) -> None:
    """In the header: back up every folder now (its tooltip: where, the last backup; off:
    said so)."""
    cfg = git_backup.load_config()
    why = _off_reason(cfg)
    if why:
        tip = why
    else:
        tip = _("Back up the folders now (git repository: {path})").format(path=cfg.directory)
        if cfg.last_at:
            tip += "\n" + _("Last backup: {when}").format(when=_when(cfg.last_at))
    ui.button(icon="backup", on_click=back_up_now).props(
        "flat round color=white dense" + (" size=sm" if compact else "")
    ).classes("opacity-60" if why else "").tooltip(tip).mark("backup-now")


def backup_tab() -> None:
    """Settings → Backup."""
    cfg = git_backup.load_config()
    ui.label(_("Backup")).classes("text-lg")
    ui.label(
        _(
            "Every folder and the people in it, in a git repository: their notes (as typed "
            "and as generated), excerpts, categories, documents and PDFs, each person in a "
            "directory of their own, a commit at each backup (the history kept). A folder "
            "renamed or moved, a person renamed or moved: their directory is moved at once. "
            "Back up by hand with the button in the header, or every so often."
        )
    ).classes("text-sm text-grey")
    if not git_backup.has_git():
        ui.label(_("git is not installed: backups cannot be made")).classes("text-negative").mark(
            "backup-no-git"
        )
    path = (
        ui.input(
            _("Directory of the repository (full path)"),
            value=str(cfg.directory) if cfg.directory else "",
            placeholder=str(config.DATA_DIR / git_backup.DEFAULT_DIR),
        )
        .props("dense clearable")
        .classes("w-full")
        .tooltip(_("Created if needed. Empty: no backup."))
        .mark("backup-path")
    )
    interval = (
        ui.number(_("Every (minutes)"), value=cfg.interval, min=0, step=5, format="%d")
        .props("dense")
        .classes("w-40")
        .tooltip(_("Automatic backups, when something changed; 0: by hand only"))
        .mark("backup-interval")
    )

    def save() -> git_backup.BackupConfig:
        c = git_backup.load_config()
        c.path = (path.value or "").strip()
        c.interval = max(0, int_or_none(interval.value) or 0)
        git_backup.save_config(c)
        status.refresh()
        return c

    @ui.refreshable
    def status() -> None:
        c = git_backup.load_config()
        lines = []
        if c.directory is None:
            lines.append(_("Backups are off: set a directory."))
        elif c.interval <= 0:
            lines.append(_("Automatic backups are off (by hand only): set an interval."))
        else:
            lines.append(
                _("Backed up every {n} minutes, when something changed.").format(n=c.interval)
            )
        if c.last_at:
            lines.append(
                _("Last backup: {when}").format(when=_when(c.last_at))
                + (f" (commit {c.last_commit[:8]})" if c.last_commit else "")
            )
        else:
            lines.append(_("Never backed up."))
        for line in lines:
            ui.label(line).classes("text-sm text-grey").mark("backup-status")
        if c.last_error:
            ui.label(_("The last backup failed: {error}").format(error=c.last_error)).classes(
                "text-sm text-negative"
            ).mark("backup-error")
        if c.directory is not None:
            ui.label(_("Its history: git -C “{path}” log --stat").format(path=c.directory)).classes(
                "text-xs text-grey font-mono"
            )

    async def run() -> None:
        save()
        await back_up_now(status.refresh)

    with ui.row().classes("items-center gap-2"):
        ui.button(
            _("Save"), on_click=lambda: (save(), ui.notify(_("Saved"), type="positive"))
        ).props("dense").mark("backup-save")
        ui.button(_("Back up now"), icon="backup", on_click=run).props("dense").mark("backup-run")
    status()
