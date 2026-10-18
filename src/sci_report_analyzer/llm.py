"""Local LLM settings, for the features that use one: its answers (see chat_json) from Ollama
(``/api/chat``) or an OpenAI-compatible server (``/v1/chat/completions``: llama.cpp, vLLM,
LM Studio…); and the embeddings of texts (e.g. to classify the excerpts), from Ollama
(``/api/embed``) or an OpenAI-compatible server (``/v1/embeddings``), with a default
embedding model. An OpenAI-compatible server may need an API key (sent to both).

Only local models are used: the servers must be on this machine or the local network (see
is_local_url; another one is refused), and Ollama's *cloud* models (offloaded to ollama.com)
are never listed nor used. The Ollama server itself can refuse them with
``OLLAMA_NO_CLOUD=1``.

Environment variables take precedence over the stored settings: ``OLLAMA_HOST`` (the server
URL, as for the ollama CLI), ``SCI_REPORT_ANALYZER_OLLAMA_MODEL`` and
``SCI_REPORT_ANALYZER_EMBED_MODEL``, ``SCI_REPORT_ANALYZER_REWRITE_MODEL`` (rewriting the
excerpts: see rewrite_model; "none": not rewritten), ``SCI_REPORT_ANALYZER_LLM_API_KEY``
(the OpenAI-compatible servers' API key). A task (see TASKS) and a folder (its
reports' settings: see folder_models) can use other models.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx

from .db.app_settings import delete_setting, get_setting, set_setting
from .i18n import N_, _

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434"
URL_ENV = "OLLAMA_HOST"
MODEL_ENV = "SCI_REPORT_ANALYZER_OLLAMA_MODEL"
EMBED_MODEL_ENV = "SCI_REPORT_ANALYZER_EMBED_MODEL"
REWRITE_MODEL_ENV = "SCI_REPORT_ANALYZER_REWRITE_MODEL"
API_KEY_ENV = "SCI_REPORT_ANALYZER_LLM_API_KEY"
REGISTRY = "https://registry.ollama.ai"
NONE = "none"  # (a model: none, e.g. the excerpts not rewritten)
# The tasks using the LLM: each can have its own model (none: the default one).
TASKS = {
    "career": N_("Reading a person's career"),
    "reflist": N_("Reading a list of references"),
}
# Whether a task's model thinks by default (none: the general setting); reading a text to
# extract facts gains little from it, and takes much longer.
TASK_THINK = {"career": False, "rewrite": False, "reflist": False}
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
    "reflist": N_(
        "Suggested: a small instruction model (4–8B) is enough to copy the parts of the "
        "references into JSON, e.g. qwen3:8b (without thinking), gemma3:4b; 12–14B if it "
        "merges or splits references (e.g. gemma3:12b, qwen3:14b)."
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
# Where the LLM's answers come from: the Ollama server, or an OpenAI-compatible one.
CHAT_APIS = {
    "ollama": N_("Ollama (/api/chat)"),
    "openai": N_("OpenAI-compatible (/v1/chat/completions)"),
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
    chat_api: str = "ollama"  # (see CHAT_APIS)
    # The OpenAI-compatible server answering (its base URL, e.g. http://localhost:8080/v1).
    chat_url: str = ""
    # The OpenAI-compatible servers' API key (chat and embeddings; blank: none sent).
    api_key: str = ""


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
    if key := os.environ.get(API_KEY_ENV):
        st.api_key = key
    st.url = _normalize_url(st.url)
    if st.embed_api not in EMBED_APIS:
        st.embed_api = "ollama"
    if st.chat_api not in CHAT_APIS:
        st.chat_api = "ollama"
    if st.chat_api == "ollama":  # (cloud models: Ollama's only)
        st.model = "" if is_cloud(st.model) else st.model
        if is_cloud(st.rewrite_model):  # (refused: not the general model in its place either)
            st.rewrite_model = NONE
    if st.embed_api == "ollama" and is_cloud(st.embed_model):
        st.embed_model = ""
    st.cluster_min, st.cluster_max = max(1, int(st.cluster_min)), max(1, int(st.cluster_max))
    return st


def chat_server(st: OllamaSettings) -> str:
    """The URL of the server answering (see CHAT_APIS): Ollama's, or the OpenAI-compatible
    one's (with /v1)."""
    return st.chat_url if st.chat_api == "openai" else st.url


def embed_server(st: OllamaSettings) -> str:
    """The URL of the embeddings' server (see EMBED_APIS)."""
    return st.embed_url if st.embed_api == "openai" else st.url


def _cloud(st: OllamaSettings, name: str) -> bool:
    """(a model refused: Ollama's cloud one, the answers from Ollama)"""
    return st.chat_api == "ollama" and is_cloud(name)


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


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
    return "" if _cloud(st, chosen) else chosen


def prompt(task: str, default: str) -> str:
    """The instructions of a task (those saved, else ``default``)."""
    try:
        return get_setting(f"{task}_prompt") or default
    except Exception:  # (no database: e.g. in unit tests)
        return default


def save_prompt(task: str, default: str, text: str) -> None:
    """A task's instructions (blank or ``default``: following the default)."""
    if not text.strip() or text.strip() == default.strip():
        delete_setting(f"{task}_prompt")
    else:
        set_setting(f"{task}_prompt", text)


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
    think: bool | None = None,
) -> dict:
    """The LLM's answer to ``messages`` (Ollama's /api/chat, or an OpenAI-compatible
    server's /chat/completions: see CHAT_APIS) for a task, as JSON following ``schema`` (a
    structured output: constrained decoding; raises if no LLM, the server is not local or
    unreachable). Streamed: ``progress`` is called with the phase ("reading" the text,
    "thinking", "writing" the answer) and the characters written in it. ``name``: this model
    instead (a local one); ``think``: whether it thinks (none: see thinks)."""
    st = settings()
    name = model(folder_id, task) if name is None else ("" if _cloud(st, name) else name)
    if not name:
        raise RuntimeError("No LLM (Settings → Local LLM)")
    if progress:
        progress("reading", 0)
    content, thought = [], 0

    def received(thinking: str | None, text: str | None) -> None:
        nonlocal thought
        if thinking:
            thought += len(thinking)
            if progress:
                progress("thinking", thought)
        if text:
            content.append(text)
            if progress:
                progress("writing", sum(map(len, content)))

    think = thinks(task) if think is None else think
    async with httpx.AsyncClient(timeout=timeout) as client:
        if st.chat_api == "openai":
            await _openai_chat(client, st, name, messages, schema, think, received)
        else:
            await _ollama_chat(client, st, name, messages, schema, think, received)
    return json.loads(_THINK_BLOCK.sub("", "".join(content), count=1))


# The models already warmed up (see warm_up): (API, server, model).
_warmed: set[tuple[str, str, str]] = set()


def is_warm(name: str) -> bool:
    st = settings()
    return (st.chat_api, chat_server(st), name) in _warmed


async def warm_up(name: str, timeout: float = 600.0) -> bool:
    """Loads the model ``name`` (a local one) by a tiny request (one token at most), once per
    model and server (until the process ends), so that the next answer is timed without its
    loading. True if it was sent now; an error is left to the next request (not warmed)."""
    st = settings()
    key = (st.chat_api, chat_server(st), name)
    if key in _warmed or _cloud(st, name):
        return False
    messages = [{"role": "user", "content": "ok"}]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if st.chat_api == "openai":
                body = {"model": name, "messages": messages, "max_tokens": 1}
                url = f"{_local(st.chat_url)}/chat/completions"
                r = await client.post(url, json=body, headers=_headers(st.api_key))
            else:
                body = {
                    "model": name,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": 1},
                }
                r = await client.post(f"{_local(st.url)}/api/chat", json=body)
            r.raise_for_status()
    except Exception as e:  # (the answer asked for will say it)
        logger.info("No warm-up of %s: %s", name, e)
        return False
    _warmed.add(key)
    return True


async def can_think(name: str, timeout: float = 5.0) -> bool | None:
    """Whether the model ``name`` can think (Ollama's capabilities); none if unknown (an
    OpenAI-compatible server does not say, nor an unreachable one)."""
    st = settings()
    if st.chat_api == "openai" or _cloud(st, name):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{_local(st.url)}/api/show", json={"model": name})
            r.raise_for_status()
            caps = r.json().get("capabilities")
    except Exception:
        return None
    return "thinking" in caps if isinstance(caps, list) else None


# The reasoning some servers leave in the answer itself (e.g. qwen3's, without a parser).
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>", re.DOTALL)


async def _ollama_chat(
    client: httpx.AsyncClient,
    st: OllamaSettings,
    name: str,
    messages: list[dict[str, str]],
    schema: dict,
    think: bool,
    received: Callable[[str | None, str | None], None],
) -> None:
    """(Ollama's /api/chat, streamed: one JSON object per line)"""
    body = {
        "model": name,
        "messages": messages,
        "format": schema,
        "stream": True,
        "think": think,
        "options": {"temperature": 0},
    }
    async with client.stream("POST", f"{_local(st.url)}/api/chat", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if error := chunk.get("error"):
                raise RuntimeError(error)
            message = chunk.get("message") or {}
            received(message.get("thinking"), message.get("content"))


async def _openai_chat(
    client: httpx.AsyncClient,
    st: OllamaSettings,
    name: str,
    messages: list[dict[str, str]],
    schema: dict,
    think: bool,
    received: Callable[[str | None, str | None], None],
) -> None:
    """(an OpenAI-compatible server's /chat/completions, streamed: server-sent events; the
    reasoning in "reasoning_content" or "reasoning", as llama.cpp and vLLM send it; thinking
    turned on or off by the chat template, for the models that have the switch, e.g. qwen3)"""
    body = {
        "model": name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        },
        "stream": True,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    url = f"{_local(st.chat_url)}/chat/completions"
    async with client.stream("POST", url, json=body, headers=_headers(st.api_key)) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            data = line.removeprefix("data:").strip() if line.startswith("data:") else ""
            if not data:
                continue
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if error := chunk.get("error"):
                raise RuntimeError(error.get("message") if isinstance(error, dict) else error)
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                received(
                    delta.get("reasoning_content") or delta.get("reasoning"),
                    delta.get("content"),
                )


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
                f"{_local(st.embed_url)}/embeddings",
                json={"model": name, "input": texts},
                headers=_headers(st.api_key),
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


async def list_openai_models(
    url: str, api_key: str | None = None, timeout: float = 5.0
) -> list[str]:
    """The models of an OpenAI-compatible server (``/models``, ``url`` with /v1; ``api_key``:
    none, the one set; raises if not local, or unreachable)."""
    key = settings().api_key if api_key is None else api_key
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_local(url)}/models", headers=_headers(key))
        r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


async def list_chat_models(
    api: str, url: str, api_key: str | None = None, timeout: float = 5.0
) -> list[str]:
    """The models of the server answering (see CHAT_APIS): Ollama's at ``url`` (without the
    cloud ones), or an OpenAI-compatible one's (raises if not local, or unreachable)."""
    if api == "openai":
        return await list_openai_models(url, api_key, timeout=timeout)
    return await list_models(url, timeout=timeout)


async def list_embed_models(
    api: str, url: str, api_key: str | None = None, timeout: float = 5.0
) -> list[str]:
    """The models of the embeddings' server: Ollama's (at ``url``: all of them, it does not
    tell the embedding ones), or an OpenAI-compatible one's (``/models``; raises if not
    local, or unreachable)."""
    if api != "openai":
        return await list_models(url, timeout=timeout)
    return await list_openai_models(url, api_key, timeout=timeout)


def format_size(n: float) -> str:
    """A size in bytes, readable (e.g. 4.7 GB)."""
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000:
            return f"{n:.0f} {unit}" if unit in ("B", "kB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def can_pull(api: str, name: str) -> bool:
    """Whether a model can be downloaded: a named one, to an Ollama server (not a cloud
    model; an OpenAI-compatible server has no download)."""
    return api == "ollama" and bool(name.strip()) and not is_cloud(name)


def _manifest_url(name: str) -> str | None:
    """(the registry's manifest of a model, ``[ns/]name[:tag]`` with the tag ``latest`` by
    default; none for a model of another registry, e.g. hf.co/…)"""
    repo, _sep, tag = name.strip().partition(":")
    parts = repo.split("/")
    if len(parts) > 2 or "." in parts[0] or not all(parts):
        return None
    repo = repo if len(parts) == 2 else f"library/{repo}"
    return f"{REGISTRY}/v2/{repo}/manifests/{tag or 'latest'}"


async def manifest_size(name: str, timeout: float = 10.0) -> int | None:
    """The size to download of a model (the layers of its manifest on the Ollama registry;
    none if not known: not found, unreachable, or another registry)."""
    url = _manifest_url(name)
    if url is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(
                url, headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
            )
            r.raise_for_status()
        m = r.json()
        layers = [*m.get("layers", []), *([m["config"]] if m.get("config") else [])]
        return sum(int(x.get("size", 0)) for x in layers) or None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


async def pull_model(
    url: str, name: str, progress: Callable[[str, int, int], None], timeout: float = 30.0
) -> None:
    """Download a model to the Ollama server at ``url`` (``/api/pull``, streamed: one JSON
    object per line); ``progress`` is called with the status, the bytes done and the total
    (0: unknown). Cancelled with its task; raises if the server refuses or fails."""
    body = {"model": name, "stream": True}
    # (no read timeout: a layer can take long to start)
    quiet = httpx.Timeout(timeout, read=None)
    async with (
        httpx.AsyncClient(timeout=quiet) as client,
        client.stream("POST", f"{_local(url)}/api/pull", json=body) as r,
    ):
        if r.status_code >= 400:
            text = (await r.aread()).decode(errors="replace")
            with suppress(ValueError, AttributeError):
                text = json.loads(text).get("error") or text
            raise RuntimeError(text or f"HTTP {r.status_code}")
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if error := chunk.get("error"):
                raise RuntimeError(error)
            progress(
                chunk.get("status") or "",
                int(chunk.get("completed") or 0),
                int(chunk.get("total") or 0),
            )


async def delete_model(url: str, name: str, timeout: float = 30.0) -> None:
    """Delete a model from the Ollama server at ``url`` (``/api/delete``)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request("DELETE", f"{_local(url)}/api/delete", json={"model": name})
        r.raise_for_status()


async def installed_models(url: str, timeout: float = 5.0) -> dict[str, int]:
    """The models installed on the Ollama server at ``url`` (not the cloud ones) with their
    size in bytes (raises if not local, or unreachable)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{_local(url)}/api/tags")
        r.raise_for_status()
    models = r.json().get("models", [])
    return {m["name"]: int(m.get("size") or 0) for m in models if not is_cloud(m)}


def _tagged(name: str) -> str:
    name = name.strip()
    return name if ":" in name else f"{name}:latest"


def same_model(a: str, b: str) -> bool:
    """Whether two names are the same model (the tag being ``latest`` by default)."""
    return bool(a.strip() and b.strip()) and _tagged(a) == _tagged(b)


def is_installed(name: str, installed: Iterable[str]) -> bool:
    return any(same_model(name, m) for m in installed)


def model_users(name: str) -> list[str]:
    """What uses a model, said (the general models, the tasks' and the folders'; those from
    an environment variable too); empty if nothing."""
    users: list[str] = []
    for st in (settings(), stored_settings()):
        chat, embed = st.chat_api == "ollama", st.embed_api == "ollama"
        for use, value, on in (
            (_("the model (Settings → Local LLM)"), st.model, chat),
            (_("the embedding model (Settings → Local LLM)"), st.embed_model, embed),
            (_("the model rewriting the excerpts"), st.rewrite_model, chat),
            *(
                (_("the model of the task “{task}”").format(task=_(TASKS[t])), v, chat)
                for t, v in st.tasks.items()
                if t in TASKS
            ),
        ):
            if on and same_model(name, value) and use not in users:
                users.append(use)
    users += [
        _("the {kind} of the folder settings “{name}”").format(kind=kind, name=label)
        for label, citations in _folder_settings()
        for key, kind in (("llm_model", _("LLM")), ("embed_model", _("embedding model")))
        if same_model(name, citations.get(key) or "")
    ]
    return users


def _folder_settings() -> list[tuple[str, dict]]:
    """The name and the citations of each folder settings (where the models are)."""
    from sqlalchemy import select

    from .db.models import FolderSettings
    from .db.session import session_scope

    try:
        with session_scope() as s:
            return [
                (f.name or _("default"), dict(f.citations or {}))
                for f in s.scalars(select(FolderSettings))
            ]
    except Exception:  # (no database: e.g. in unit tests)
        return []
