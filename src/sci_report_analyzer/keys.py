"""Contact email and API keys for sources (environment variables take precedence over stored
settings)."""

from __future__ import annotations

import os

from .db.models import AppSetting
from .db.session import session_scope

KEYS = {
    "email": (
        "SCI_REPORT_ANALYZER_EMAIL",
        "Your email (required: identifies you to OpenAlex, Crossref, Unpaywall…)",
    ),
    "openalex": ("OPENALEX_API_KEY", "OpenAlex API key (free, https://openalex.org/settings/api)"),
    "semanticscholar": ("S2_API_KEY", "Semantic Scholar API key (optional)"),
}
_SETTING = "api_keys"


def stored_keys() -> dict[str, str]:
    with session_scope() as s:
        row = s.get(AppSetting, _SETTING)
        return dict(row.value) if row else {}


def save_keys(values: dict[str, str]) -> None:
    with session_scope() as s:
        s.merge(AppSetting(key=_SETTING, value={k: v for k, v in values.items() if v}))


def get_key(name: str) -> str | None:
    env, _ = KEYS[name]
    if v := os.environ.get(env):
        return v
    try:
        return stored_keys().get(name) or None
    except Exception:  # DB not initialised (e.g. in unit tests)
        return None
