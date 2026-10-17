"""Local LLM (Ollama) settings, for the features that use one; and the embeddings of texts
(e.g. to classify the excerpts), from Ollama (``/api/embed``) or an OpenAI-compatible server
(``/v1/embeddings``: llama.cpp, vLLM, LM Studio…), with a default embedding model.

Only local models are used: the servers must be on this machine or the local network (see
is_local_url; another one is refused), and Ollama's *cloud* models (offloaded to ollama.com)
are never listed nor used. The Ollama server itself can refuse them with
``OLLAMA_NO_CLOUD=1``.

Environment variables take precedence over the stored settings: ``OLLAMA_HOST`` (the server
URL, as for the ollama CLI), ``SCI_REPORT_ANALYZER_OLLAMA_MODEL`` and
``SCI_REPORT_ANALYZER_EMBED_MODEL``, ``SCI_REPORT_ANALYZER_REWRITE_MODEL`` (rewriting the
excerpts: see rewrite_model; "none": not rewritten). A task (see TASKS) and a folder (its
reports' settings: see folder_models) can use other models.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx

from .db.app_settings import get_setting, set_setting
from .i18n import N_

DEFAULT_URL = "http://localhost:11434"
URL_ENV = "OLLAMA_HOST"
MODEL_ENV = "SCI_REPORT_ANALYZER_OLLAMA_MODEL"
EMBED_MODEL_ENV = "SCI_REPORT_ANALYZER_EMBED_MODEL"
REWRITE_MODEL_ENV = "SCI_REPORT_ANALYZER_REWRITE_MODEL"
NONE = "none"  # (a model: none, e.g. the excerpts not rewritten)
# The tasks using the LLM: each can have its own model (none: the default one).
TASKS = {"career": N_("Reading a person's career")}
# Whether a task's model thinks by default (none: the general setting); reading a text to
# extract facts gains little from it, and takes much longer.
TASK_THINK = {"career": False, "rewrite": False}
# The kind of model suggested for a task (and the embeddings), its size first, then examples.
SUGGESTED = {
    "default": N_(
        "Suggested: an instruction model of 4–14B parameters, multilingual (French), good at "
        "structured outputs, e.g. qwen3:8b, gemma3:12b, mistral-small3.2 (24B: more precise, "
        "slower)."
    ),
    "career": N_(
        "Suggested: a small instruction model (4–8B) is enough to copy facts from a text into "
        "JSON, and much faster: e.g. qwen3:8b (without thinking), gemma3:4b; 12–14B if it "
        "misses lines or mixes people (e.g. gemma3:12b, qwen3:14b)."
    ),
    "rewrite": N_(
        "Suggested: a small instruction model (4–8B), multilingual (French), e.g. qwen3:8b "
        "(without thinking), gemma3:4b."
    ),
    "embed": N_(
        "Suggested: a multilingual embedding model (French and English), small (0.3–0.6B) and "
        "fast, e.g. bge-m3, qwen3-embedding:0.6b, embeddinggemma; nomic-embed-text is mostly "
        "English."
    ),
}
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
# Host names of the local network (besides those without a dot, e.g. "gpu").
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".internal", ".home.arpa")


class NotLocal(ValueError):
    """A server not on this machine nor the local network (refused)."""


def is_cloud(model: str | dict) -> bool:
    """Whether a model (a name, or an /api/tags entry) runs on Ollama's cloud."""
    if isinstance(model, dict):
        if model.get("remote_host") or model.get("remote_model"):
            return True
        model = model.get("name", "")
    return bool(_CLOUD_TAG.search(model))


def is_local_url(url: str) -> bool:
    """Whether a server is on this machine or the local network: localhost, a private,
    loopback or link-local address, a host name without a dot or of the local network
    (``.local``, ``.lan``…)."""
    host = (urlsplit(_normalize_url(url)).hostname or "").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _local(url: str) -> str:
    """(the URL, normalised; raises NotLocal if not local)"""
    url = _normalize_url(url)
    if not is_local_url(url):
        raise NotLocal(f"Not a local server: {url}")
    return url


@dataclass
class OllamaSettings:
    enabled: bool = False
    url: str = DEFAULT_URL
    model: str = ""
    embed_api: str = "ollama"  # (see EMBED_APIS)
    # The OpenAI-compatible server (its base URL, e.g. http://localhost:8080/v1).
    embed_url: str = ""
    embed_model: str = ""  # (the default one)
    # The suggested categories (see classifier.py): a category's excerpts grouped in at most
    # cluster_max clusters, each of at least cluster_min excerpts.
    cluster_min: int = 5
    cluster_max: int = 10
    tasks: dict[str, str] = field(default_factory=dict)  # (a task's model, see TASKS)
    # Whether a reasoning model thinks before answering (slower; Ollama's "think"); a task's
    # own choice (see thinks).
    think: bool = False
    task_think: dict[str, bool] = field(default_factory=dict)
    # The model rewriting a new excerpt (and finding its years: see excerpt_rewrite; blank
    # or NONE: none).
    rewrite_model: str = ""


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
    """The settings in effect (the environment overriding the stored ones; no cloud model)."""
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
    if model := os.environ.get(REWRITE_MODEL_ENV):
        st.rewrite_model = model
    st.url = _normalize_url(st.url)
    if st.embed_api not in EMBED_APIS:
        st.embed_api = "ollama"
    st.model = "" if is_cloud(st.model) else st.model
    if is_cloud(st.rewrite_model):  # (refused: not the general model in its place either)
        st.rewrite_model = NONE
    if st.embed_api == "ollama" and is_cloud(st.embed_model):
        st.embed_model = ""
    st.cluster_min, st.cluster_max = max(1, int(st.cluster_min)), max(1, int(st.cluster_max))
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


def model(folder_id: int | None = None, task: str | None = None) -> str:
    """The LLM to use for a task (see TASKS), in a folder: the folder's, else the task's,
    else the default one (blank: none, the LLM being off or no model chosen)."""
    st = settings()
    if not st.enabled:
        return ""
    chosen = folder_models(folder_id)[0] or st.tasks.get(task or "", "") or st.model
    return "" if is_cloud(chosen) else chosen


def rewrite_model() -> str:
    """The LLM rewriting the new excerpts: the one chosen, else the general one (blank: none,
    the LLM being off, or "none" chosen)."""
    st = settings()
    chosen = st.rewrite_model.strip() or st.model
    return chosen if st.enabled and chosen and chosen.lower() != NONE else ""


def embed_model(folder_id: int | None = None) -> str:
    """The embedding model to use (in a folder; blank: none, the LLM being off or no model
    chosen)."""
    st = settings()
    if not st.enabled:
        return ""
    chosen = folder_models(folder_id)[1] or st.embed_model
    return "" if st.embed_api == "ollama" and is_cloud(chosen) else chosen


def thinks(task: str | None = None) -> bool:
    """Whether the model thinks before answering for a task: its own choice, else its
    default (TASK_THINK), else the general one."""
    st = settings()
    if task and task in st.task_think:
        return bool(st.task_think[task])
    return TASK_THINK.get(task or "", st.think)


async def chat_json(
    messages: list[dict[str, str]],
    schema: dict,
    folder_id: int | None = None,
    task: str | None = None,
    progress: Callable[[str, int], None] | None = None,
    timeout: float = 600.0,
    name: str | None = None,
) -> dict:
    """The LLM's answer to ``messages`` (Ollama's /api/chat) for a task, as JSON following
    ``schema`` (a structured output: constrained decoding; raises if no LLM, the server is
    not local or unreachable). Streamed: ``progress`` is called with the phase ("reading" the
    text, "thinking", "writing" the answer) and the characters written in it. ``name``: this
    model instead (a local one)."""
    name = model(folder_id, task) if name is None else ("" if is_cloud(name) else name)
    if not name:
        raise RuntimeError("No LLM (Settings → Local LLM)")
    st = settings()
    body = {
        "model": name,
        "messages": messages,
        "format": schema,
        "stream": True,
        "think": thinks(task),
        "options": {"temperature": 0},
    }
    if progress:
        progress("reading", 0)
    content, thought = [], 0
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream("POST", f"{_local(st.url)}/api/chat", json=body) as r,
    ):
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if error := chunk.get("error"):
                raise RuntimeError(error)
            message = chunk.get("message") or {}
            if t := message.get("thinking"):
                thought += len(t)
                if progress:
                    progress("thinking", thought)
            if c := message.get("content"):
                content.append(c)
                if progress:
                    progress("writing", sum(map(len, content)))
    return json.loads("".join(content))


async def embed(
    texts: list[str], folder_id: int | None = None, timeout: float = 120.0
) -> list[list[float]]:
    """The embeddings of ``texts`` with the folder's embedding model (raises if none, the
    server is not local or unreachable)."""
    st, name = settings(), embed_model(folder_id)
    if not name:
        raise RuntimeError("No embedding model (Settings → Local LLM)")
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=timeout) as client:
        if st.embed_api == "openai":
            r = await client.post(
                f"{_local(st.embed_url)}/embeddings", json={"model": name, "input": texts}
            )
            r.raise_for_status()
            rows = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in rows]
        r = await client.post(f"{_local(st.url)}/api/embed", json={"model": name, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]


async def list_models(url: str, timeout: float = 5.0) -> list[str]:
    """The models available on the Ollama server at ``url``, without the cloud ones (raises
    if not local, or unreachable)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_local(url)}/api/tags")
        r.raise_for_status()
    models = r.json().get("models", [])
    return sorted(m["name"] for m in models if not is_cloud(m))


async def list_embed_models(api: str, url: str, timeout: float = 5.0) -> list[str]:
    """The models of the embeddings' server: Ollama's (at ``url``: all of them, it does not
    tell the embedding ones), or an OpenAI-compatible one's (``/models``; raises if not
    local, or unreachable)."""
    if api != "openai":
        return await list_models(url, timeout=timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_local(url)}/models")
        r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
