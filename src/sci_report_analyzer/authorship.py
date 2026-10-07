"""Who made a manual decision, and when.

Every decision set by hand on a venue (``Venue.DECIDED``, its levels) or a variant assigned
by hand is stamped with the user's name in shared settings (``identity``) and the day, on
flush: whatever screen made it. Decisions taken from a group's settings keep theirs
(``keeping``). The rules of the matching settings are stamped when saved
(``stamp_rules``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .db.models import AppSetting, Venue, VenueKey

SETTING = "shared"  # app_setting key of the shared settings (``shared.SharedConfig``)
META = ("by", "on")  # (with ``note``: not part of a decision's value)

_keeping: ContextVar[bool] = ContextVar("authorship_keeping", default=False)
_identity: dict[str, str | None] = {}  # (cached: read on the first flush)


def set_identity(name: str | None) -> None:
    """The user's name in shared settings changed."""
    _identity["name"] = (name or "").strip() or None


def forget_identity() -> None:
    _identity.clear()


def _name(session: Session) -> str | None:
    if "name" not in _identity:
        with session.no_autoflush:
            row = session.get(AppSetting, SETTING)
        set_identity((row.value or {}).get("identity") if row else None)
    return _identity["name"]


def today() -> str:
    return date.today().isoformat()


def stamp(name: str | None) -> dict[str, str]:
    """A decision's author and day."""
    return {"on": today(), **({"by": name} if name else {})}


def without_meta(value: Any) -> Any:
    """A decision's value without who made it nor when (to compare two)."""
    if isinstance(value, dict):
        return {k: without_meta(v) for k, v in value.items() if k not in META}
    if isinstance(value, list):
        return [without_meta(v) for v in value]
    return value


@contextmanager
def keeping() -> Iterator[None]:
    """Decisions saved meanwhile keep their authors (taken from shared settings)."""
    token = _keeping.set(True)
    try:
        yield
    finally:
        _keeping.reset(token)


def _manual(v: Venue, field: str) -> bool:
    if field == "kind":
        return bool(v.kind_manual and v.kind)
    if field == "short_name":
        return bool(v.short_manual)
    if field == "joint":
        j = v.joint or {}
        return bool(j.get("manual") or j.get("use"))
    return getattr(v, field) not in (None, "", [], {})


def _changed(state, field: str) -> bool:
    return state.attrs[field].history.has_changes()


def _stamp_venue(v: Venue, name: str | None, new: bool) -> None:
    state = inspect(v)
    decisions = dict(v.decisions or {})
    for f in Venue.DECIDED:
        flags = {"kind": "kind_manual", "short_name": "short_manual"}.get(f)
        if not (new or _changed(state, f) or (flags and _changed(state, flags))):
            continue
        if _manual(v, f):
            if new or not _same(state, f):
                decisions[f] = {**decisions.get(f, {}), **stamp(name)}
        else:
            decisions.pop(f, None)
    if new or _changed(state, "levels"):
        before = state.attrs.levels.history.deleted
        old = [without_meta(lv) for lv in (before[0] if before else None) or []]
        if v.levels:
            v.levels = [
                lv if without_meta(lv) in old and lv.get("on") else {**lv, **stamp(name)}
                for lv in v.levels
            ]
    if decisions != (v.decisions or {}):
        v.decisions = decisions or None


def _same(state, field: str) -> bool:
    """Whether a changed field has its former value again (e.g. saved unchanged)."""
    h = state.attrs[field].history
    return bool(h.deleted) and h.added == h.deleted


def _stamp_key(k: VenueKey, name: str | None, new: bool) -> None:
    state = inspect(k)
    if not k.manual:
        k.decided_by = k.decided_on = None
    elif new or any(_changed(state, f) for f in ("manual", "venue_id", "track")):
        k.decided_by, k.decided_on = name, today()


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _context, _instances) -> None:
    if _keeping.get():
        return
    touched = [(o, True) for o in session.new] + [(o, False) for o in session.dirty]
    if not any(isinstance(o, Venue | VenueKey) for o, _new in touched):
        return
    name = _name(session)
    for o, new in touched:
        if isinstance(o, Venue):
            _stamp_venue(o, name, new)
        elif isinstance(o, VenueKey):
            _stamp_key(o, name, new)


def stamp_rules(old: list[Any], new: list[Any], defaults: dict[str, Any]) -> list[Any]:
    """Rules (pydantic models with ``id``, ``by``, ``on``) as saved: those added or changed
    since ``old`` stamped (back to their default: not any more), the others as they were."""
    if _keeping.get():
        return new
    name = _identity.get("name")
    before = {r.id: without_meta(r.model_dump()) for r in old}
    out = []
    for r in new:
        value = without_meta(r.model_dump())
        if (d := defaults.get(r.id)) is not None and value == without_meta(d.model_dump()):
            r = r.model_copy(update={"by": None, "on": None})
        elif before.get(r.id) != value:
            r = r.model_copy(update={"by": name, "on": today()})
        out.append(r)
    return out
