"""Local LLM (Ollama) settings, for the features that will use one; and the embeddings of
texts (e.g. to classify the excerpts), from Ollama (``/api/embed``) or an OpenAI-compatible
server (``/v1/embeddings``: llama.cpp, vLLM, LM Studio…), with a default embedding model.

Environment variables take precedence over the stored settings: ``OLLAMA_HOST`` (the server
URL, as for the ollama CLI), ``SCI_REPORT_ANALYZER_OLLAMA_MODEL`` and
``SCI_REPORT_ANALYZER_EMBED_MODEL``. A folder can use other models (its reports' settings:
see folder_models).

Ollama can also run *cloud* models (offloaded to ollama.com): with ``local_only`` (the default)
they are never listed nor used. The server itself can refuse them with ``OLLAMA_NO_CLOUD=1``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass

import httpx

from .db.app_settings import get_setting, set_setting
from .i18n import N_

DEFAULT_URL = "http://localhost:11434"
URL_ENV = "OLLAMA_HOST"
MODEL_ENV = "SCI_REPORT_ANALYZER_OLLAMA_MODEL"
EMBED_MODEL_ENV = "SCI_REPORT_ANALYZER_EMBED_MODEL"
# Where the embeddings come from: the Ollama server, or an OpenAI-compatible one.
EMBED_APIS = {
    "ollama": N_("Ollama (/api/embed)"),
    "openai": N_("OpenAI-compatible (/v1/embeddings)"),
}
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
    embed_api: str = "ollama"  # (see EMBED_APIS)
    # The OpenAI-compatible server (its base URL, e.g. http://localhost:8080/v1) and key.
    embed_url: str = ""
    embed_key: str = ""
    embed_model: str = ""  # (the default one)


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
    if model := os.environ.get(EMBED_MODEL_ENV):
        st.embed_model = model
    st.url = _normalize_url(st.url)
    if st.embed_api not in EMBED_APIS:
        st.embed_api = "ollama"
    if st.local_only:
        st.model = "" if is_cloud(st.model) else st.model
        if st.embed_api == "ollama" and is_cloud(st.embed_model):
            st.embed_model = ""
    return st


def folder_models(folder_id: int | None) -> tuple[str, str]:
    """The models a folder uses instead of the default ones (its reports' settings, shared:
    see reports._citations): the LLM and the embedding model (blank: the default)."""
    from .reports import _citations

    c = _citations(folder_id)
    return c.get("llm_model") or "", c.get("embed_model") or ""


def save_folder_models(folder_id: int, model: str, embed_model: str) -> None:
    from .reports import _save_citations

    _save_citations(
        folder_id, llm_model=model.strip() or None, embed_model=embed_model.strip() or None
    )


def model(folder_id: int | None = None) -> str:
    """The LLM to use (in a folder; blank: none, the LLM being off or no model chosen)."""
    st = settings()
    if not st.enabled:
        return ""
    chosen = folder_models(folder_id)[0] or st.model
    return "" if st.local_only and is_cloud(chosen) else chosen


def embed_model(folder_id: int | None = None) -> str:
    """The embedding model to use (in a folder; blank: none, the LLM being off or no model
    chosen)."""
    st = settings()
    if not st.enabled:
        return ""
    chosen = folder_models(folder_id)[1] or st.embed_model
    if st.local_only and st.embed_api == "ollama" and is_cloud(chosen):
        return ""
    return chosen


async def chat_json(
    messages: list[dict[str, str]],
    schema: dict,
    folder_id: int | None = None,
    timeout: float = 600.0,
) -> dict:
    """The LLM's answer to ``messages`` (Ollama's /api/chat), as JSON following ``schema``
    (a structured output; raises if no LLM, or the server is unreachable)."""
    name = model(folder_id)
    if not name:
        raise RuntimeError("No LLM (Settings → Local LLM)")
    body = {
        "model": name,
        "messages": messages,
        "format": schema,
        "stream": False,
        "options": {"temperature": 0},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{settings().url}/api/chat", json=body)
        r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


async def embed(
    texts: list[str], folder_id: int | None = None, timeout: float = 120.0
) -> list[list[float]]:
    """The embeddings of ``texts`` with the folder's embedding model (raises if none, or the
    server is unreachable)."""
    st, name = settings(), embed_model(folder_id)
    if not name:
        raise RuntimeError("No embedding model (Settings → Local LLM)")
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=timeout) as client:
        if st.embed_api == "openai":
            headers = {"Authorization": f"Bearer {st.embed_key}"} if st.embed_key else {}
            r = await client.post(
                f"{_normalize_url(st.embed_url)}/embeddings",
                json={"model": name, "input": texts},
                headers=headers,
            )
            r.raise_for_status()
            rows = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in rows]
        r = await client.post(f"{st.url}/api/embed", json={"model": name, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]


async def list_models(url: str, local_only: bool = True, timeout: float = 5.0) -> list[str]:
    """The models available on the Ollama server at ``url``, without the cloud ones when
    ``local_only`` (raises if unreachable)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_normalize_url(url)}/api/tags")
        r.raise_for_status()
    models = r.json().get("models", [])
    return sorted(m["name"] for m in models if not (local_only and is_cloud(m)))


async def list_embed_models(
    api: str, url: str, key: str = "", local_only: bool = True, timeout: float = 5.0
) -> list[str]:
    """The models of the embeddings' server: Ollama's (at ``url``: all of them, it does not
    tell the embedding ones), or an OpenAI-compatible one's (``/models``; raises if
    unreachable)."""
    if api != "openai":
        return await list_models(url, local_only=local_only, timeout=timeout)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_normalize_url(url)}/models", headers=headers)
        r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
