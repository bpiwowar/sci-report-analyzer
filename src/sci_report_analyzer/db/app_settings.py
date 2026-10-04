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
