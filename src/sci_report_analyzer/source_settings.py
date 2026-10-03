"""Which publication sources the app uses (Settings → Sources).

A disabled source is not searched nor synced, and its records are left out of the merged
publications and the venues (they are kept: enabling it again brings them back).
"""

from __future__ import annotations

from sqlalchemy import and_

from .db.models import AppSetting, SourceLink
from .db.session import session_scope

KEY = "enabled_sources"
_disabled: frozenset[str] | None = None


def disabled() -> frozenset[str]:
    global _disabled
    if _disabled is None:
        with session_scope() as s:
            row = s.get(AppSetting, KEY)
            _disabled = frozenset((row.value or {}).get("disabled", [])) if row else frozenset()
    return _disabled


def enabled(source: str) -> bool:
    return source not in disabled()


def set_disabled(sources: set[str]) -> None:
    global _disabled
    with session_scope() as s:
        s.merge(AppSetting(key=KEY, value={"disabled": sorted(sources)}))
    _disabled = None


def reset_cache() -> None:
    global _disabled
    _disabled = None


def active_links():
    """SQL condition: validated links of enabled sources."""
    cond = SourceLink.status == "validated"
    return and_(cond, SourceLink.source.not_in(disabled())) if disabled() else cond
