"""Local LLM settings: stored, overridden by the environment, models listed; the answers
and embeddings from Ollama or an OpenAI-compatible server."""

import asyncio
import json

import httpx
import pytest
import respx
from nicegui.testing import User

from sci_report_analyzer import llm


def test_settings_are_stored_and_overridden(monkeypatch) -> None:
    monkeypatch.delenv(llm.URL_ENV, raising=False)
    monkeypatch.delenv(llm.MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, url="http://gpu:11434/", model="qwen3"))
    assert llm.settings() == llm.OllamaSettings(True, "http://gpu:11434", "qwen3")

    monkeypatch.setenv(llm.URL_ENV, "otherhost:1234")
    monkeypatch.setenv(llm.MODEL_ENV, "llama3.2")
    assert llm.settings() == llm.OllamaSettings(True, "http://otherhost:1234", "llama3.2")
    llm.save_settings(llm.OllamaSettings())


def test_list_models() -> None:
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(
                200, json={"models": [{"name": "qwen3:8b"}, {"name": "gemma3:4b"}]}
            )
        )
        assert asyncio.run(llm.list_models("localhost:11434")) == ["gemma3:4b", "qwen3:8b"]


def test_cloud_models_are_left_out(monkeypatch) -> None:
    tags = {
        "models": [
            {"name": "qwen3:8b"},
            {"name": "gpt-oss:120b-cloud"},
            {"name": "remote:latest", "remote_host": "https://ollama.com:443"},
        ]
    }
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json=tags)
        )
        assert asyncio.run(llm.list_models(llm.DEFAULT_URL)) == ["qwen3:8b"]

    monkeypatch.setenv(llm.MODEL_ENV, "gpt-oss:120b-cloud")
    assert llm.settings().model == ""


def test_only_local_servers(monkeypatch) -> None:
    """The servers must be on this machine or the local network: others refused."""
    for url in [
        "localhost:11434",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:11434",
        "http://192.168.1.20:11434",
        "http://10.0.0.5",
        "http://gpu:11434",
        "http://box.local:8080",
        "http://nas.lan",
    ]:
        assert llm.is_local_url(url), url
    for url in ["https://api.openai.com/v1", "http://ollama.com", "http://8.8.8.8:11434"]:
        assert not llm.is_local_url(url), url
    monkeypatch.delenv(llm.MODEL_ENV, raising=False)
    monkeypatch.setenv(llm.URL_ENV, "https://ollama.example.org")
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    with pytest.raises(llm.NotLocal):
        asyncio.run(llm.chat_json([], {}))
    with pytest.raises(llm.NotLocal):
        asyncio.run(llm.list_models("https://ollama.example.org"))
    llm.save_settings(llm.OllamaSettings())


@pytest.fixture
def openai_chat(monkeypatch):
    """The answers from an OpenAI-compatible server (llama.cpp's port), with an API key."""
    for env in (llm.URL_ENV, llm.MODEL_ENV, llm.API_KEY_ENV):
        monkeypatch.delenv(env, raising=False)
    llm.save_settings(
        llm.OllamaSettings(
            enabled=True,
            model="qwen3-8b",
            chat_api="openai",
            chat_url="http://localhost:8080/v1/",
            api_key="secret",
        )
    )
    yield
    llm.save_settings(llm.OllamaSettings())


def _events(*chunks) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_openai_chat(openai_chat) -> None:
    """An OpenAI-compatible server: its /chat/completions, streamed (server-sent events), the
    JSON schema as its response format, thinking switched by the chat template."""
    stream = _events(
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"reasoning_content": "Hmm"}}]},
        {"choices": [{"delta": {"content": '{"entries"'}}]},
        {"choices": [{"delta": {"content": ": []}"}, "finish_reason": "stop"}]},
    )
    phases = []
    with respx.mock:
        route = respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, text=stream)
        )
        answer = asyncio.run(
            llm.chat_json(
                [{"role": "user", "content": "hi"}],
                {"type": "object"},
                task="career",
                progress=lambda p, n: phases.append((p, n)),
            )
        )
    assert answer == {"entries": []}
    assert phases == [("reading", 0), ("thinking", 3), ("writing", 10), ("writing", 15)]
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer secret"
    sent = json.loads(request.content)
    assert (sent["model"], sent["stream"], sent["temperature"]) == ("qwen3-8b", True, 0)
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert "format" not in sent and "think" not in sent


def test_openai_chat_think_and_errors(openai_chat) -> None:
    """Thinking on (vLLM's "reasoning"), the reasoning left in the answer removed, a server
    error raised; a model named "…-cloud" is not Ollama's."""
    llm.save_settings(
        llm.OllamaSettings(
            enabled=True,
            model="big-cloud",
            think=True,
            chat_api="openai",
            chat_url="http://localhost:8080/v1",
        )
    )
    assert llm.model() == "big-cloud"
    stream = _events(
        {"choices": [{"delta": {"reasoning": "Hmm"}}]},
        {"choices": [{"delta": {"content": '<think>x</think>\n{"a": 1}'}}]},
    )
    with respx.mock:
        route = respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, text=stream)
        )
        assert asyncio.run(llm.chat_json([], {})) == {"a": 1}
        sent = json.loads(route.calls[0].request.content)
        assert sent["chat_template_kwargs"] == {"enable_thinking": True}
        assert "Authorization" not in route.calls[0].request.headers  # (no API key)
        route.mock(return_value=httpx.Response(200, text=_events({"error": {"message": "bad"}})))
        with pytest.raises(RuntimeError, match="bad"):
            asyncio.run(llm.chat_json([], {}))


def test_openai_models_and_embeddings(openai_chat, monkeypatch) -> None:
    """The models of an OpenAI-compatible server (/v1/models), and its embeddings, with the API
    key (the environment's first)."""
    monkeypatch.setenv(llm.API_KEY_ENV, "from-env")
    models = {"data": [{"id": "qwen3-8b"}, {"id": "gemma-3-4b"}]}
    with respx.mock:
        route = respx.get("http://localhost:8080/v1/models").mock(
            return_value=httpx.Response(200, json=models)
        )
        assert asyncio.run(llm.list_chat_models("openai", "http://localhost:8080/v1")) == [
            "gemma-3-4b",
            "qwen3-8b",
        ]
        assert route.calls[0].request.headers["Authorization"] == "Bearer from-env"
        asyncio.run(llm.list_chat_models("openai", "http://localhost:8080/v1", "typed"))
        assert route.calls[1].request.headers["Authorization"] == "Bearer typed"
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        )
        assert asyncio.run(llm.list_chat_models("ollama", llm.DEFAULT_URL)) == ["qwen3:8b"]

    st = llm.settings()
    st.embed_api, st.embed_url, st.embed_model = "openai", "http://localhost:8081/v1", "bge-m3"
    llm.save_settings(st)
    with respx.mock:
        route = respx.post("http://localhost:8081/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        )
        assert asyncio.run(llm.embed(["x"])) == [[1.0]]
        assert route.calls[0].request.headers["Authorization"] == "Bearer from-env"


def test_openai_chat_not_local(openai_chat) -> None:
    st = llm.settings()
    st.chat_url = "https://api.openai.com/v1"
    llm.save_settings(st)
    with pytest.raises(llm.NotLocal):
        asyncio.run(llm.chat_json([], {}))
    with pytest.raises(llm.NotLocal):
        asyncio.run(llm.list_chat_models("openai", st.chat_url))


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_settings_page_openai(user: User, monkeypatch) -> None:
    """Settings → Local LLM: the answers from an OpenAI-compatible server (its URL, its API
    key), its models listed."""
    for env in (llm.URL_ENV, llm.MODEL_ENV, llm.API_KEY_ENV):
        monkeypatch.delenv(env, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True))
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        respx.get("http://localhost:8080/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "qwen3-8b"}]})
        )
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-chat-api")
        user.find(marker="llm-api-key").elements.pop().value = "secret"
        user.find(marker="llm-chat-api").elements.pop().value = "openai"
        await user.should_see(marker="llm-chat-url")
        user.find(marker="llm-chat-url").elements.pop().value = "http://localhost:8080/v1"
        user.find(marker="llm-refresh").click()
        await user.should_see("1 model on the server")
        select = user.find(marker="llm-model").elements.pop()
        assert "qwen3-8b" in select.options
        select.value = "qwen3-8b"
        user.find(marker="llm-save").click()
        await user.should_see("Saved")
    st = llm.stored_settings()
    assert (st.chat_api, st.chat_url, st.api_key, st.model) == (
        "openai",
        "http://localhost:8080/v1",
        "secret",
        "qwen3-8b",
    )
    llm.save_settings(llm.OllamaSettings())
