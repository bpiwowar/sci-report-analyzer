"""A backup of the folders in a git repository (Settings → Backup): for each person in a
folder, everything needed to rebuild what was done there, in a directory of its own, and a
commit each time (by hand, with the header's button, or every so often).

The repository's tree follows the folders' (each folder a directory, those within it below):
a folder's directory holds ``folder.json`` (the folder, its settings: categories, star
lists…) and ``notes.md`` (its own notes, if any); a person's (a directory within their
folder's, named after them) holds:

- ``notes.md``: their notes in the folder, as typed; ``report.md``: as generated (citations
  substituted, excerpts listed); ``excerpts.md``: their excerpts by category;
- ``period.json``: their period in the folder (years, tags), their papers' tags and notes
  there, the text of each section; ``person.json``: the person (names, career…) and what
  was set by hand on their papers (notes, tags, overrides);
- ``excerpts.json``: their excerpts (category, text, place, years, markers, tags…);
- ``documents.json`` and ``documents/``: their documents (the PDFs, annotated);
- ``papers.json`` and ``papers/``: the stored PDFs of the papers excerpted, tagged or noted
  there.

People not in a folder (a period of their own) are in ``NO_FOLDER``. Where each folder and
person is goes in ``INDEX`` (by id): a folder renamed or moved, a person renamed or moved to
another folder, their directory is moved in the repository at once (see install), in a
commit of its own (its history followed by git); what was deleted goes at the next backup
(still in the history).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from . import config
from .db.app_settings import get_setting, set_setting
from .db.models import (
    CareerEntry,
    Category,
    Excerpt,
    Folder,
    FolderSettings,
    Period,
    PeriodDocument,
    PeriodNote,
    PeriodTag,
    Person,
    Publication,
    PublicationPdf,
    SectionText,
    Tag,
)
from .db.session import session_scope
from .i18n import _

logger = logging.getLogger(__name__)

SETTING = "git_backup"
DEFAULT_DIR = "git-backup"  # (in the data directory)
DEFAULT_INTERVAL = 30  # minutes
INDEX = ".sci-report-analyzer.json"
NO_FOLDER = "(no folder)"
CHECK_EVERY = 60  # seconds: how often the automatic backup looks whether it is time
_NAME_MAX = 80
# The columns not saved: re-made from the PDFs (their text's lines), or too big for nothing.
_SKIP = {PeriodDocument: {"lines"}}


class BackupConfig(BaseModel):
    """Settings → Backup (``app_setting`` ``git_backup``)."""

    path: str | None = None  # (none: DEFAULT_DIR in the data directory; "": off)
    interval: int = DEFAULT_INTERVAL  # minutes between automatic backups (0: by hand only)
    last_at: str | None = None  # (the last backup: when, its commit, what went wrong)
    last_commit: str | None = None
    last_error: str | None = None

    @property
    def directory(self) -> Path | None:
        if self.path is None:
            return config.DATA_DIR / DEFAULT_DIR
        return Path(self.path).expanduser() if self.path.strip() else None


def load_config() -> BackupConfig:
    return BackupConfig.model_validate(get_setting(SETTING) or {})


def save_config(cfg: BackupConfig) -> None:
    set_setting(SETTING, cfg.model_dump())


class GitError(RuntimeError):
    """A git command failed (its message: git's)."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git in the backup's repository; its output."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            env={**_env(), **(env or {})},
            timeout=600,
            check=False,
        )
    except FileNotFoundError as err:
        raise GitError(_("git is not installed")) from err
    except subprocess.TimeoutExpired as err:
        raise GitError(_("git did not answer in time")) from err
    if res.returncode != 0:
        raise GitError(res.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return res.stdout.decode(errors="replace").strip()


def has_git() -> bool:
    return shutil.which("git") is not None


# ---- Where everything goes ------------------------------------------------------------------


def clean_name(name: str | None) -> str:
    """A name as a directory's: no path separators nor characters some systems refuse, no
    leading dot, not too long."""
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "-", name or "")
    name = " ".join(name.split()).strip(" .-")[:_NAME_MAX].strip(" .")
    return name or "untitled"


@dataclass
class Layout:
    """Where each folder (by id) and each period (a person in a folder, by id) goes:
    directories relative to the repository's."""

    folders: dict[int, str] = field(default_factory=dict)
    periods: dict[int, str] = field(default_factory=dict)

    def entries(self) -> dict[str, str]:
        """Every directory, by key (``folder/1``, ``period/3``)."""
        return {
            **{f"folder/{i}": p for i, p in self.folders.items()},
            **{f"period/{i}": p for i, p in self.periods.items()},
        }


def layout() -> Layout:
    """The directories of the folders (their tree) and of the people in them (named after
    them; a name used twice in a directory: numbered, the oldest first)."""
    with session_scope() as s:
        folders = list(s.execute(select(Folder.id, Folder.name, Folder.parent_id)))
        periods = list(
            s.execute(
                select(Period.id, Period.folder_id, Period.name, Person.name)
                .join(Person, Period.person_id == Person.id)
                .order_by(Period.id)
            )
        )
    children: dict[int | None, list[tuple[str, str, int]]] = {}
    for fid, name, parent in sorted(folders, key=lambda r: r[0]):
        children.setdefault(parent, []).append(("folder", name, fid))
    for pid, fid, name, person in periods:
        label = person if fid is not None else f"{person} — {name}"
        children.setdefault(fid if fid is not None else -1, []).append(("period", label, pid))
    out = Layout()
    known = {fid for fid, _n, _p in folders}

    def place(parent: int | None, base: str) -> None:
        taken: set[str] = set()
        for kind, name, key in children.get(parent, []):
            stem = clean_name(name)
            n, path = 1, stem
            while path.lower() in taken or (not base and path.lower() == NO_FOLDER.lower()):
                n += 1
                path = f"{stem} ({n})"
            taken.add(path.lower())
            rel = f"{base}/{path}" if base else path
            if kind == "folder":
                out.folders[key] = rel
                place(key, rel)
            else:
                out.periods[key] = rel

    place(None, "")
    for parent in [p for p in children if p not in known and p not in (None, -1)]:
        place(parent, "")  # (a folder whose parent is gone: at the top)
    place(-1, NO_FOLDER)
    return out


def _read_index(repo: Path) -> dict[str, str]:
    try:
        return dict(json.loads((repo / INDEX).read_text(encoding="utf-8"))["entries"])
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_index(repo: Path, entries: dict[str, str]) -> None:
    data = {"version": 1, "entries": dict(sorted(entries.items()))}
    (repo / INDEX).write_text(_json(data), "utf-8")


def _relocate(repo: Path, now: dict[str, str]) -> list[tuple[str, str]]:
    """Move the directories whose place changed (from INDEX) to their new place: each first
    out of the way (the deepest first), then where it goes (the shallowest first), so that
    a parent moved carries none of its moved children. Returns the moves (old, new)."""
    was = _read_index(repo)
    moves = [(k, was[k], now[k]) for k in now if k in was and was[k] != now[k]]
    moves = [(k, a, b) for k, a, b in moves if (repo / a).is_dir()]
    if not moves:
        return []
    tmp = repo / ".moving"
    tmp.mkdir(exist_ok=True)
    for i, (_k, a, _b) in sorted(enumerate(moves), key=lambda m: -m[1][1].count("/")):
        os.replace(repo / a, tmp / str(i))
    for i, (_k, _a, b) in sorted(enumerate(moves), key=lambda m: m[1][2].count("/")):
        dest = repo / b
        if dest.exists():  # (something left there: replaced, it is in the history)
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp / str(i), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    _prune_empty(repo)
    return [(a, b) for _k, a, b in moves]


def _prune_empty(repo: Path) -> None:
    for d in sorted((p for p in repo.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if ".git" not in d.relative_to(repo).parts and not any(d.iterdir()):
            d.rmdir()


# ---- The repository -------------------------------------------------------------------------

_README = """# SciReport Analyzer: backup

Written by SciReport Analyzer (Settings → Backup), a commit at each backup. Each folder is a
directory (`folder.json`, `notes.md`), and each person in it a directory within it:
`notes.md` (as typed), `report.md` (as generated), `excerpts.md`, `excerpts.json`,
`period.json`, `person.json`, `documents/` and `papers/` (the PDFs, annotated).

Do not edit it by hand: the next backup writes it again.
"""


def ensure_repo(repo: Path) -> None:
    """The repository (made if needed), with its README."""
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        git(repo, "init", "-q")
    if not (repo / "README.md").exists():
        (repo / "README.md").write_text(_README, encoding="utf-8")
    if not (repo / ".gitattributes").exists():
        (repo / ".gitattributes").write_text("*.pdf binary\n", encoding="utf-8")
    if not (repo / ".gitignore").exists():
        (repo / ".gitignore").write_text(".DS_Store\n.moving/\n", encoding="utf-8")


def _author_env(repo: Path) -> dict[str, str]:
    """The commit's author: git's own settings, else the app."""
    env = {}
    for key, var in (("user.name", "NAME"), ("user.email", "EMAIL")):
        try:
            value = git(repo, "config", key)
        except GitError:
            value = ""
        if not value:
            v = "sci-report-analyzer" + ("@localhost" if var == "EMAIL" else "")
            env[f"GIT_AUTHOR_{var}"] = env[f"GIT_COMMITTER_{var}"] = v
    return env


def commit(repo: Path, message: str) -> str | None:
    """Commit everything (files gone: removed); its id, none when nothing changed."""
    git(repo, "add", "-A")
    if not git(repo, "status", "--porcelain"):
        return None
    git(repo, "commit", "-q", "-m", message, env=_author_env(repo))
    return git(repo, "rev-parse", "HEAD")


# ---- What is written -----------------------------------------------------------------------


def _value(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    return v


def row(obj: Any, *skip: str) -> dict[str, Any]:
    """A database row's columns (all of them, but ``skip`` and those of _SKIP)."""
    left_out = set(skip) | _SKIP.get(type(obj), set())
    return {
        a.key: _value(getattr(obj, a.key))
        for a in inspect(obj).mapper.column_attrs
        if a.key not in left_out
    }


_WIDTH = 100  # (a list or object of plain values on one line, if it fits)


def _json(data: Any) -> str:
    """JSON as stable as can be from a backup to the next (a change: a few lines changed,
    not the whole file): keys sorted, an item a line, a list or object of plain values
    (an excerpt's rectangles, a person's aliases…) on one line when short enough."""
    return _format(data, 0) + "\n"


def _plain(v: Any) -> bool:
    return not isinstance(v, dict | list) or not v


def _format(v: Any, depth: int) -> str:
    if _plain(v):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    pad, inner = "  " * depth, "  " * (depth + 1)
    if isinstance(v, dict):
        items = [
            f"{json.dumps(str(k), ensure_ascii=False)}: {_format(v[k], depth + 1)}"
            for k in sorted(v, key=str)
        ]
        opening, closing = "{", "}"
    else:
        items = [_format(x, depth + 1) for x in v]
        opening, closing = "[", "]"
    if all(_plain(x) for x in (v.values() if isinstance(v, dict) else v)):
        line = opening + ", ".join(items) + closing
        if len(pad) + len(line) <= _WIDTH and "\n" not in line:
            return line
    return opening + "\n" + ",\n".join(inner + i for i in items) + "\n" + pad + closing


def _paper(p: Publication | None) -> dict[str, Any] | None:
    """A paper, as found again: its id here, title, year and DOI."""
    if p is None:
        return None
    return {
        "id": p.id,
        "title": p.title,
        "year": p.year_override or p.year,
        "doi": p.doi_manual or p.doi,
    }


# A paper's fields set by hand (see Publication).
_MANUAL = (
    "note",
    "rank_override",
    "rank_note",
    "kind_override",
    "track_override",
    "doi_manual",
    "year_override",
    "author_pos_override",
    "merge_locked",
    "hidden",
    "venue_manual",
)


def _manual(p: Publication) -> bool:
    return bool(p.tags) or any(getattr(p, k) for k in _MANUAL)


def _category_paths(s: Session, settings_id: int | None) -> dict[int, str]:
    cats = {c.id: c for c in s.scalars(select(Category).where(Category.settings_id == settings_id))}

    def path(c: Category) -> str:
        parent = cats.get(c.parent_id) if c.parent_id else None
        return f"{path(parent)} / {c.name}" if parent else c.name

    return {i: path(c) for i, c in cats.items()}


@dataclass
class Files:
    """The files of a directory: text (by name), and files copied (by name: their source)."""

    texts: dict[str, str] = field(default_factory=dict)
    copies: dict[str, Path] = field(default_factory=dict)


def folder_files(folder_id: int) -> Files:
    from . import folders

    out = Files()
    with session_scope() as s:
        f = s.get(Folder, folder_id)
        if f is None:
            return out
        sid = folders.settings_id(s, folder_id)
        st = s.get(FolderSettings, sid)
        paths = _category_paths(s, sid)
        cats = list(
            s.scalars(select(Category).where(Category.settings_id == sid).order_by(Category.id))
        )
        tags = list(s.scalars(select(Tag).where(Tag.settings_id == sid).order_by(Tag.id)))
        out.texts["folder.json"] = _json(
            {
                "folder": row(f),
                "settings": row(st) if st else None,
                "categories": [{**row(c), "path": paths.get(c.id)} for c in cats],
                "star_lists": [row(t) for t in tags],
            }
        )
        if (f.notes or "").strip():
            out.texts["notes.md"] = _text(f.notes)
    return out


def _text(t: str | None) -> str:
    """A text as written: its line endings "\\n", ending with one."""
    t = (t or "").replace("\r\n", "\n").replace("\r", "\n")
    return t if not t or t.endswith("\n") else t + "\n"


def period_files(period_id: int, report: str | None) -> Files:
    """A person's directory in a folder (``report``: their notes as generated, if made)."""
    from . import categories, folders, pdfs

    out = Files()
    with session_scope() as s:
        p = s.get(Period, period_id)
        if p is None:
            return out
        person = p.person
        sid = folders.settings_id(s, p.folder_id) if p.folder_id else None
        paths = _category_paths(s, sid) if sid else {}
        pubs = {
            x.id: x
            for x in s.scalars(
                select(Publication)
                .where(Publication.person_id == person.id)
                .order_by(Publication.id)
            )
        }
        docs = list(
            s.scalars(
                select(PeriodDocument)
                .where(PeriodDocument.period_id == p.id)
                .order_by(PeriodDocument.id)
            )
        )
        doc_files = {}
        for d in docs:
            name = f"{d.id}-{clean_name(d.name)}.pdf"
            doc_files[d.id] = name
            src = pdfs.pdf_dir() / d.path
            if src.is_file():
                out.copies[f"documents/{name}"] = src
        out.texts["documents.json"] = _json(
            [{**row(d), "file": f"documents/{doc_files[d.id]}"} for d in docs]
        )
        excerpts = list(
            s.scalars(
                select(Excerpt)
                .where(Excerpt.period_id == p.id)
                .order_by(Excerpt.id)  # (moved: only its position changes)
            )
        )
        out.texts["excerpts.json"] = _json(
            [
                {
                    **row(e),
                    "category": paths.get(e.category_id),
                    "tags": sorted(t.name for t in e.tags),
                    "document": doc_files.get(e.document_id),
                    "paper": _paper(pubs.get(e.publication_id)),
                }
                for e in excerpts
            ]
        )
        tags = list(
            s.scalars(
                select(PeriodTag)
                .where(PeriodTag.period_id == p.id)
                .order_by(PeriodTag.publication_id, PeriodTag.tag_id)
            )
        )
        notes = list(
            s.scalars(
                select(PeriodNote)
                .where(PeriodNote.period_id == p.id)
                .order_by(PeriodNote.publication_id)
            )
        )
        texts = list(
            s.scalars(
                select(SectionText)
                .where(SectionText.period_id == p.id)
                .order_by(SectionText.category_id)
            )
        )
        out.texts["period.json"] = _json(
            {
                "period": row(p, "notes"),
                "folder": p.folder.name if p.folder else None,
                "paper_tags": [
                    {**row(t), "tag": t.tag.name, "paper": _paper(pubs.get(t.publication_id))}
                    for t in tags
                ],
                "paper_notes": [
                    {**row(n), "paper": _paper(pubs.get(n.publication_id))} for n in notes
                ],
                "section_texts": [{**row(t), "category": paths.get(t.category_id)} for t in texts],
            }
        )
        careers = list(
            s.scalars(
                select(CareerEntry)
                .where(CareerEntry.person_id == person.id)
                .order_by(CareerEntry.id)
            )
        )
        out.texts["person.json"] = _json(
            {
                "person": row(person),
                "career": [row(c) for c in careers],
                "papers": [
                    {
                        **_paper(x),
                        **{k: _value(getattr(x, k)) for k in _MANUAL},
                        "venue": x.venue.name if x.venue_manual and x.venue else None,
                        "tags": sorted(t.name for t in x.tags),
                    }
                    for x in pubs.values()
                    if _manual(x)
                ],
            }
        )
        used = {e.publication_id for e in excerpts} | {t.publication_id for t in tags}
        used |= {n.publication_id for n in notes}
        stored = {
            r.publication_id: r
            for r in s.scalars(
                select(PublicationPdf).where(PublicationPdf.publication_id.in_(used))
            )
        }
        listed = []
        for pid in sorted(stored):
            r, src = stored[pid], pdfs.pdf_dir() / stored[pid].path
            if not src.is_file():
                continue
            name = f"{pid}-{clean_name(pdfs.slug(pubs[pid].title) if pid in pubs else '')}.pdf"
            out.copies[f"papers/{name}"] = src
            listed.append({**row(r), "file": f"papers/{name}", "paper": _paper(pubs.get(pid))})
        out.texts["papers.json"] = _json(listed)
        out.texts["notes.md"] = _text(p.notes)
        folder_id = p.folder_id
    if folder_id is not None:
        try:
            out.texts["excerpts.md"] = _text(categories.markdown(folder_id, period_id))
        except Exception as err:  # (its excerpts are in excerpts.json anyway)
            logger.warning("Backup: the excerpts of period %s as Markdown: %s", period_id, err)
    if report is not None:
        out.texts["report.md"] = _text(report)
    return out


async def reports_of(period_ids: list[int]) -> dict[int, str]:
    """The notes of each period as generated (see reports.render), those with notes."""
    from . import pubview, reports

    with session_scope() as s:
        rows = list(
            s.execute(
                select(Period.id, Period.person_id, Period.notes).where(Period.id.in_(period_ids))
            )
        )
    stats: dict[int, list] = {}
    out = {}
    for pid, person_id, notes in rows:
        if not (notes or "").strip():
            continue
        try:
            if person_id not in stats:
                stats[person_id] = await pubview.load_stats(person_id)
            ctx = reports.folder_context(stats[person_id], pid)
            out[pid] = reports.render(notes, ctx).text
        except Exception as err:  # (the notes as typed are kept anyway)
            logger.warning("Backup: the report of period %s: %s", pid, err)
        await asyncio.sleep(0)
    return out


def _write(repo: Path, directory: str, files: Files) -> set[str]:
    """Write a directory's files (those unchanged left alone); their paths (in the
    repository's)."""
    written = set()
    for name, text in files.texts.items():
        dest = repo / directory / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode()
        if not dest.is_file() or dest.read_bytes() != data:
            dest.write_bytes(data)
        written.add(f"{directory}/{name}")
    for name, src in files.copies.items():
        dest = repo / directory / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if (
            not dest.is_file()
            or dest.stat().st_size != src.stat().st_size
            or dest.read_bytes() != src.read_bytes()
        ):
            shutil.copyfile(src, dest)
        written.add(f"{directory}/{name}")
    return written


# The repository's own files (kept by a backup).
_OWN = {"README.md", ".gitattributes", ".gitignore", INDEX}


def _sweep(repo: Path, written: set[str]) -> None:
    """The files not written by this backup (of folders, people, documents gone): removed
    (still in the history), then the directories left empty."""
    for f in repo.rglob("*"):
        rel = f.relative_to(repo).as_posix()
        if rel.split("/")[0] == ".git" or rel in _OWN or not f.is_file():
            continue
        if rel not in written:
            f.unlink()
    _prune_empty(repo)


# ---- Backing up -----------------------------------------------------------------------------

_lock = threading.Lock()  # (one at a time: a backup, a move)


@dataclass
class Result:
    commit: str | None  # (none: nothing changed)
    changed: int = 0  # files


def _write_all(repo: Path, reports: dict[int, str]) -> Result:
    with _lock:
        ensure_repo(repo)
        lay = layout()
        entries = lay.entries()
        moved = _relocate(repo, entries)
        written: set[str] = set()
        for fid, path in lay.folders.items():
            written |= _write(repo, path, folder_files(fid))
        for pid, path in lay.periods.items():
            written |= _write(repo, path, period_files(pid, reports.get(pid)))
        _sweep(repo, written)
        _write_index(repo, entries)
        git(repo, "add", "-A")
        changed = len(git(repo, "diff", "--cached", "--name-only").splitlines())
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        message = _("Backup of {when}").format(when=when)
        if moved:
            message += "\n\n" + "\n".join(f"{a} → {b}" for a, b in moved)
        return Result(commit(repo, message), changed)


async def backup(cfg: BackupConfig | None = None) -> Result:
    """Back up every folder and person now (``GitError``: it could not be done; off, no
    directory set: ``GitError`` too)."""
    from nicegui import run

    cfg = cfg or load_config()
    repo = cfg.directory
    if repo is None:
        raise GitError(_("Backups are off: no directory set (Settings → Backup)"))
    with session_scope() as s:
        ids = list(s.scalars(select(Period.id)))
    try:
        reports = await reports_of(ids)
        res = await run.io_bound(_write_all, repo, reports)
    except (GitError, OSError) as err:
        _done(error=str(err))
        raise GitError(str(err)) from err
    _done(commit=res.commit)
    return res


def _done(*, commit: str | None = None, error: str | None = None) -> None:
    cfg = load_config()
    cfg.last_at = datetime.now(UTC).isoformat(timespec="seconds")
    cfg.last_error = error
    if commit:
        cfg.last_commit = commit
    save_config(cfg)


def relocate_now() -> list[tuple[str, str]]:
    """The directories of the folders and people renamed or moved: moved now, in a commit
    of their own (nothing if no backup was made yet, or off)."""
    cfg = load_config()
    repo = cfg.directory
    if repo is None or not (repo / INDEX).is_file():
        return []
    with _lock:
        entries = layout().entries()
        moved = _relocate(repo, entries)
        if not moved:
            return []
        known = _read_index(repo)
        _write_index(repo, {k: entries.get(k, v) for k, v in known.items()})
        commit(repo, _("Moved") + "\n\n" + "\n".join(f"{a} → {b}" for a, b in moved))
    return moved


_threads: list[threading.Thread] = []


def paths_changed() -> None:
    """A folder or person renamed or moved: their directories moved in the background."""
    repo = load_config().directory
    if repo is None or not (repo / INDEX).is_file():
        return  # (no backup made yet: nothing to move)

    def work() -> None:
        try:
            relocate_now()
        except Exception as err:
            logger.warning("Backup: moving the renamed directories: %s", err)

    t = threading.Thread(target=work, name="git-backup-move", daemon=True)
    _threads.append(t)
    t.start()


def wait() -> None:
    """The moves started: done (for tests)."""
    while _threads:
        _threads.pop().join()


_WATCHED = {Folder: ("name", "parent_id"), Period: ("folder_id", "name"), Person: ("name",)}


def _after_flush(session: Session, _ctx) -> None:
    for obj in session.dirty:
        attrs = _WATCHED.get(type(obj))
        if attrs and any(inspect(obj).attrs[a].history.has_changes() for a in attrs):
            session.info["git_backup_moved"] = True
            return


def _after_commit(session: Session) -> None:
    if session.info.pop("git_backup_moved", False):
        paths_changed()


def install() -> None:
    """Watch the renames and moves (see paths_changed)."""
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)
        event.listen(Session, "after_commit", _after_commit)


def uninstall() -> None:
    if event.contains(Session, "after_flush", _after_flush):
        event.remove(Session, "after_flush", _after_flush)
        event.remove(Session, "after_commit", _after_commit)


def changed_since(when: str | None) -> bool:
    """Whether the database (or the PDFs) changed since a backup (none: never made)."""
    if not when:
        return True
    from .db import backup as db_backup
    from .db import session

    since = datetime.fromisoformat(when).timestamp()
    if session._engine is None or (db := db_backup.db_file(session._engine)) is None:
        return True
    files = [db, db.with_name(db.name + "-wal")]
    return any(f.is_file() and f.stat().st_mtime > since for f in files)


def due(cfg: BackupConfig, now: datetime | None = None) -> bool:
    """Whether the automatic backup is due: on, its interval past since the last one, and
    something changed since."""
    if cfg.directory is None or cfg.interval <= 0:
        return False
    now = now or datetime.now(UTC)
    if cfg.last_at:
        elapsed = (now - datetime.fromisoformat(cfg.last_at)).total_seconds()
        if elapsed < cfg.interval * 60:
            return False
    return changed_since(cfg.last_at)


async def keep_backing_up() -> None:
    """The automatic backups (see due), as long as the app runs."""
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            cfg = load_config()
            if due(cfg) and has_git():
                res = await backup(cfg)
                logger.info("Backup: %s", res.commit or "nothing changed")
        except Exception as err:
            logger.warning("Backup failed: %s", err)
