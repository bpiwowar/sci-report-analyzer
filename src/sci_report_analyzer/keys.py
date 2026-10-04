"""Contact email and API keys for sources (environment variables take precedence over stored
settings)."""

from __future__ import annotations

import os

from .db.app_settings import get_setting, set_setting
from .i18n import N_, _


class _Keys(dict):
    """{name: (environment variable, label)}: its labels translated when listed."""

    def items(self):
        return [(k, (env, _(label))) for k, (env, label) in super().items()]


KEYS = _Keys(
    {
        "email": (
            "SCI_REPORT_ANALYZER_EMAIL",
            N_("Your email (required: identifies you to OpenAlex, Crossref, Unpaywall…)"),
        ),
        "openalex": (
            "OPENALEX_API_KEY",
            N_("OpenAlex API key (free, https://openalex.org/settings/api)"),
        ),
        "semanticscholar": ("S2_API_KEY", N_("Semantic Scholar API key (optional)")),
    }
)
_SETTING = "api_keys"


def stored_keys() -> dict[str, str]:
    return dict(get_setting(_SETTING, {}))


def save_keys(values: dict[str, str]) -> None:
    set_setting(_SETTING, {k: v for k, v in values.items() if v})


def get_key(name: str) -> str | None:
    env, _label = KEYS[name]
    if v := os.environ.get(env):
        return v
    try:
        return stored_keys().get(name) or None
    except Exception:  # DB not initialised (e.g. in unit tests)
        return None
