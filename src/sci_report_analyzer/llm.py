"""Local LLM (Ollama) settings, for the features that will use one.

Environment variables take precedence over the stored settings: ``OLLAMA_HOST`` (the server
URL, as for the ollama CLI) and ``SCI_REPORT_ANALYZER_OLLAMA_MODEL``.

Ollama can also run *cloud* models (offloaded to ollama.com): with ``local_only`` (the default)
they are never listed nor used. The server itself can refuse them with ``OLLAMA_NO_CLOUD=1``.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

import httpx

from .db.app_settings import get_setting, set_setting

DEFAULT_URL = "http://localhost:11434"
URL_ENV = "OLLAMA_HOST"
MODEL_ENV = "SCI_REPORT_ANALYZER_OLLAMA_MODEL"
_SETTING = "ollama"
# The server-side switch turning off Ollama's cloud features (https://docs.ollama.com/faq).
NO_CLOUD_ENV = "OLLAMA_NO_CLOUD"
# Cloud models are tagged "…-cloud" or ":cloud" (e.g. gpt-oss:120b-cloud).
_CLOUD_TAG = re.compile(r"[-:]cloud$")


def is_cloud(model: str | dict) -> bool:
    """Whether a model (a name, or an /api/tags entry) runs on Ollama's cloud."""
    if isinstance(model, dict):
        if model.get("remote_host") or model.get("remote_model"):
            return True
        model = model.get("name", "")
    return bool(_CLOUD_TAG.search(model))


@dataclass
class OllamaSettings:
    enabled: bool = False
    url: str = DEFAULT_URL
    model: str = ""
    local_only: bool = True


def stored_settings() -> OllamaSettings:
    v = get_setting(_SETTING, {}) or {}
    fields = OllamaSettings.__dataclass_fields__
    return OllamaSettings(**{k: x for k, x in v.items() if k in fields})


def save_settings(st: OllamaSettings) -> None:
    set_setting(_SETTING, asdict(st))


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/") or DEFAULT_URL
    # OLLAMA_HOST may be a bare host[:port]
    return url if "://" in url else f"http://{url}"


def settings() -> OllamaSettings:
    """The settings in effect (the environment overriding the stored ones)."""
    try:
        st = stored_settings()
    except Exception:  # DB not initialised (e.g. in unit tests)
        st = OllamaSettings()
    if url := os.environ.get(URL_ENV):
        st.url = url
    if model := os.environ.get(MODEL_ENV):
        st.model = model
    st.url = _normalize_url(st.url)
    if st.local_only and is_cloud(st.model):
        st.model = ""
    return st


async def list_models(url: str, local_only: bool = True, timeout: float = 5.0) -> list[str]:
    """The models available on the Ollama server at ``url``, without the cloud ones when
    ``local_only`` (raises if unreachable)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_normalize_url(url)}/api/tags")
        r.raise_for_status()
    models = r.json().get("models", [])
    return sorted(m["name"] for m in models if not (local_only and is_cloud(m)))
