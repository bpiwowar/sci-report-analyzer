"""A group's shared settings in a git repository: pull (merge the group's changes into the
local settings) and push (propose local changes), with the ``git`` command.

The app keeps its own copy of the repository in the data directory (``repo_dir``), never
checked out: the group's files are read from its commits, and a push builds its commit from
a temporary index, on top of the group's latest one. Only the files of the shared settings
(``shared.MANAGED``) are written; the others (a README, a CI workflow…) are kept.

The last commit pulled is the *base* of the next pull's three-way merge: a decision changed
only here is kept, one changed only by the group is taken, one changed on both sides is a
conflict to resolve. A push either updates the group's branch (``direct``) or creates a
branch of its own to open a pull request from (``branch``; most git hosts print its link).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import authorship, config, shared
from .db.app_settings import get_setting, set_setting
from .i18n import _

REPO_DIR = "shared-settings"


class SharedConfig(BaseModel):
    """Settings → Shared settings (``app_setting`` ``shared``)."""

    identity: str = ""  # the name stamped on the decisions made here
    url: str = ""  # the group's repository (a URL, or a path)
    branch: str = "main"
    push: Literal["direct", "branch"] = "branch"
    base: str | None = None  # the commit last pulled
    pulled_at: str | None = None


def load_config() -> SharedConfig:
    return SharedConfig.model_validate(get_setting(authorship.SETTING) or {})


def save_config(cfg: SharedConfig) -> None:
    set_setting(authorship.SETTING, cfg.model_dump())
    authorship.set_identity(cfg.identity)


def repo_dir() -> Path:
    return config.DATA_DIR / REPO_DIR


class GitError(RuntimeError):
    """A git command failed (its message: git's)."""


class OutOfDate(GitError):
    """The group's settings changed since the last pull: pull first."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # (fail rather than wait for a password)
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env["LC_ALL"] = "C"
    return env


def git(*args: str, input: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    """Run git in the app's copy of the repository; its output."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_dir()), *args],
            input=input,
            capture_output=True,
            env={**_env(), **(env or {})},
            timeout=300,
            check=False,
        )
    except FileNotFoundError as err:
        raise GitError(_("git is not installed")) from err
    except subprocess.TimeoutExpired as err:
        raise GitError(_("git did not answer in time")) from err
    if res.returncode != 0:
        raise GitError(res.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return res.stdout


def _text(*args: str, env: dict[str, str] | None = None) -> str:
    return git(*args, env=env).decode().strip()


def ensure_repo(cfg: SharedConfig) -> None:
    """The app's copy of the repository, its ``origin`` the group's."""
    if not cfg.url.strip():
        raise GitError(_("no repository set"))
    path = repo_dir()
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        path.mkdir(parents=True, exist_ok=True)
        git("init", "-q")
    remotes = _text("remote").split()
    url = _url(cfg.url)
    if "origin" in remotes:
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)


def _url(url: str) -> str:
    """A path given as the repository: absolute (git runs in the app's copy)."""
    url = url.strip()
    if "://" not in url and not re.match(r"^[\w.-]+@[\w.-]+:", url):
        return str(Path(url).expanduser().resolve())
    return url


def fetch(cfg: SharedConfig) -> str | None:
    """The group's latest commit (none: the repository has no such branch yet)."""
    ensure_repo(cfg)
    ref = f"refs/remotes/origin/{cfg.branch}"
    try:
        git("fetch", "-q", "--no-tags", "origin", f"+refs/heads/{cfg.branch}:{ref}")
    except GitError as err:
        if "couldn't find remote ref" in str(err).lower():
            return None
        raise
    return _text("rev-parse", ref)


def has_commit(sha: str | None) -> bool:
    if not sha:
        return False
    try:
        git("cat-file", "-e", f"{sha}^{{commit}}")
    except GitError:
        return False
    return True


def _managed(path: str) -> bool:
    if path.startswith(shared.VENUES_DIR + "/"):
        return path.endswith(".json") and path.count("/") == 1
    return path in shared.MANAGED


def read_tree(sha: str | None) -> shared.Files:
    """The shared settings' files of a commit (none: no file)."""
    if not sha:
        return {}
    names = [p for p in git("ls-tree", "-r", "-z", "--name-only", sha).decode().split("\0") if p]
    paths = [p for p in names if _managed(p)]
    if not paths:
        return {}
    out = git("cat-file", "--batch", input="".join(f"{sha}:{p}\n" for p in paths).encode())
    files: shared.Files = {}
    pos = 0
    for p in paths:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        size = int(header[2])
        files[p] = out[end + 1 : end + 1 + size].decode()
        pos = end + 1 + size + 1
    return files


def _author_env(cfg: SharedConfig) -> dict[str, str]:
    """The commit's author: git's own settings, else the name in shared settings."""
    env = {}
    try:
        name = _text("config", "user.name")
    except GitError:
        name = ""
    try:
        email = _text("config", "user.email")
    except GitError:
        email = ""
    who = cfg.identity or "sci-report-analyzer"
    if not name:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = who
    if not email:
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = f"{who}@users.noreply"
    return env


def write_commit(
    cfg: SharedConfig, parent: str | None, files: shared.Files, message: str
) -> str | None:
    """A commit of ``parent`` whose shared settings are ``files`` (its other files kept);
    none when nothing changes."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if parent:
            git("read-tree", parent, env=env)
        else:
            git("read-tree", "--empty", env=env)
        git("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", *shared.MANAGED, env=env)
        paths = sorted(files)
        if paths:
            sources = []
            for i, p in enumerate(paths):
                src = Path(tmp) / f"{i}.json"
                src.write_text(files[p], encoding="utf-8")
                sources.append(str(src))
            shas = git("hash-object", "-w", "--stdin-paths", input="\n".join(sources).encode())
            info = "".join(
                f"100644 {sha}\t{p}\n" for sha, p in zip(shas.decode().split(), paths, strict=True)
            )
            git("update-index", "--add", "--index-info", input=info.encode(), env=env)
        tree = _text("write-tree", env=env)
    if parent and tree == _text("rev-parse", f"{parent}^{{tree}}"):
        return None
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args += ["-p", parent]
    return _text(*args, env=_author_env(cfg))


def push_commit(sha: str, branch: str, *, force: bool = False) -> list[str]:
    """Push a commit as ``branch``; the links the git host printed (to open a pull
    request). ``OutOfDate``: the branch moved on."""
    try:
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir()),
                "push",
                "--porcelain",
                "origin",
                f"{'+' if force else ''}{sha}:refs/heads/{branch}",
            ],
            capture_output=True,
            env=_env(),
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise GitError(_("git did not answer in time")) from err
    out = (res.stdout + res.stderr).decode(errors="replace")
    if res.returncode != 0:
        if "rejected" in out or "non-fast-forward" in out or "fetch first" in out:
            raise OutOfDate(_("the group's settings changed meanwhile: pull them first"))
        raise GitError(out.strip())
    return re.findall(r"https?://\S+", "\n".join(ln for ln in out.splitlines() if "remote:" in ln))


# ---- pull ------------------------------------------------------------------------------------


@dataclass
class Pull:
    """A pull prepared: what it changes here, its conflicts to resolve."""

    head: str | None
    base: shared.Items
    local: shared.Items
    remote: shared.Items
    merged: shared.Items
    conflicts: list[shared.Conflict]
    first: bool  # no base: the first pull

    def incoming(self, take_group: set[str] = frozenset()) -> list[str]:
        """The keys whose local value the pull changes."""
        return shared.changes(self.local, shared.resolved(self.merged, self.conflicts, take_group))

    def outgoing(self) -> list[str]:
        """The local changes the group lacks (to push after)."""
        return shared.changes(self.remote, self.merged)


def prepare_pull() -> Pull:
    cfg = load_config()
    head = fetch(cfg)
    remote = shared.items_from_files(read_tree(head))
    first = not has_commit(cfg.base)
    base = {} if first else shared.items_from_files(read_tree(cfg.base))
    shared.link_venues(remote)
    taken = {k.split("/")[1] for k in (*remote, *base) if k.startswith("venue/")}
    local = shared.local_items(taken)
    merged, conflicts = shared.merge3(base, local, remote)
    return Pull(head, base, local, remote, merged, conflicts, first)


def finish_pull(p: Pull, take_group: set[str] = frozenset()) -> dict[str, int]:
    """Apply a pull (the conflicts of ``take_group`` given the group's value); the next
    pull is against its commit."""
    counts = shared.apply(shared.resolved(p.merged, p.conflicts, take_group), p.local)
    cfg = load_config()
    cfg.base = p.head
    cfg.pulled_at = datetime.now(UTC).isoformat(timespec="seconds")
    save_config(cfg)
    return counts


# ---- push ------------------------------------------------------------------------------------


@dataclass
class Outgoing:
    """The local changes the group lacks, to choose from for a push."""

    head: str | None
    base: shared.Items
    local: shared.Items
    keys: list[str] = field(default_factory=list)


def prepare_push() -> Outgoing:
    """``OutOfDate`` when the group's settings changed since the last pull."""
    cfg = load_config()
    head = fetch(cfg)
    if head != cfg.base and head is not None:
        raise OutOfDate(_("the group's settings changed since your last pull: pull them first"))
    base = shared.items_from_files(read_tree(head))
    taken = {k.split("/")[1] for k in base if k.startswith("venue/")}
    local = shared.local_items(taken)
    return Outgoing(head, base, local, shared.changes(base, local))


@dataclass
class Pushed:
    sha: str | None  # none: nothing to push
    branch: str
    links: list[str]
    direct: bool


def push(o: Outgoing, keys: set[str], message: str) -> Pushed:
    """Push the local changes of ``keys``: on the group's branch (``direct``), else on a
    branch of their own (for a pull request)."""
    cfg = load_config()
    files = shared.files_from_items(shared.chosen(o.base, o.local, keys))
    sha = write_commit(cfg, o.head, files, message.strip() or _("Shared settings"))
    direct = cfg.push == "direct" or o.head is None
    if direct:
        branch = cfg.branch
    else:
        who = re.sub(r"[^a-z0-9]+", "-", cfg.identity.lower()).strip("-") or "member"
        branch = f"settings/{who}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    if sha is None:
        return Pushed(None, branch, [], direct)
    links = push_commit(sha, branch)
    if direct:  # (the group has it now: the next pull is against it)
        cfg.base = sha
        save_config(cfg)
    return Pushed(sha, branch, links, direct)
