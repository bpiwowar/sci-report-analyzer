"""Which publication sources the app uses (Settings → Sources).

A disabled source is not searched nor synced, and its records are left out of the merged
publications and the venues (they are kept: enabling it again brings them back).
"""

from __future__ import annotations

from sqlalchemy import and_, select

from .db.app_settings import get_setting, set_setting
from .db.models import Folder, Period, SourceLink
from .db.session import session_scope

KEY = "enabled_sources"
_disabled: frozenset[str] | None = None


def disabled() -> frozenset[str]:
    global _disabled
    if _disabled is None:
        _disabled = frozenset((get_setting(KEY) or {}).get("disabled", []))
    return _disabled


def enabled(source: str) -> bool:
    return source not in disabled()


def set_disabled(sources: set[str]) -> None:
    global _disabled
    set_setting(KEY, {"disabled": sorted(sources)})
    _disabled = None


def reset_cache() -> None:
    global _disabled
    _disabled = None


def active_links():
    """SQL condition: validated links of enabled sources."""
    cond = SourceLink.status == "validated"
    return and_(cond, SourceLink.source.not_in(disabled())) if disabled() else cond


# ---- the primary source ---------------------------------------------------------------------
# A paper the person's profile on it doesn't list is not counted (e.g. HAL, where CNRS
# researchers must deposit their papers); the other sources still help with its venue.

PRIMARY_KEY = "primary_source"
NO_PRIMARY = "none"  # a folder without a primary source (whatever the default)


def default_primary() -> str | None:
    return (get_setting(PRIMARY_KEY) or {}).get("source")


def set_default_primary(source: str | None) -> None:
    set_setting(PRIMARY_KEY, {"source": source})


def folder_primary(folder: Folder | None) -> str | None:
    """The primary source of a folder: its own, else the default (None: none)."""
    source = folder.primary_source if folder is not None else None
    if source is None:
        return default_primary()
    return None if source == NO_PRIMARY else source


def primary_for(person_id: int, period_id: int | None = None) -> str | None:
    """The primary source for a person's papers (within a period: that of its folder), if
    enabled and the person has a validated profile there (else nothing would count)."""
    with session_scope() as s:
        period = s.get(Period, period_id) if period_id else None
        source = folder_primary(period.folder if period else None)
        if source is None or not enabled(source):
            return None
        linked = s.scalar(
            select(SourceLink.id).where(
                SourceLink.person_id == person_id,
                SourceLink.source == source,
                SourceLink.status == "validated",
            )
        )
        return source if linked is not None else None
