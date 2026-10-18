"""The app's settings and UI state (AppSetting rows): a JSON value per key."""

from __future__ import annotations

from typing import Any

from .models import AppSetting
from .session import session_scope


def get_setting(key: str, default: Any = None) -> Any:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return row.value if row else default


def set_setting(key: str, value: Any) -> None:
    with session_scope() as s:
        s.merge(AppSetting(key=key, value=value))


def delete_setting(key: str) -> None:
    with session_scope() as s:
        if row := s.get(AppSetting, key):
            s.delete(row)


def get_unless_default(key: str, default: Any) -> Any:
    """A setting changed from its default, else None (a copy of ``default``, saved by a
    former version: dropped, the setting then following the default, a newer one too)."""
    value = get_setting(key)
    if value is not None and value == default:
        delete_setting(key)
        return None
    return value


def set_unless_default(key: str, value: Any, default: Any) -> None:
    """Save a setting; as its default: none saved (it follows the default)."""
    if value == default:
        delete_setting(key)
    else:
        set_setting(key, value)
