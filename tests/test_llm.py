"""Local LLM (Ollama) settings: stored, overridden by the environment, models listed."""

import asyncio

import httpx
import respx

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
        assert len(asyncio.run(llm.list_models(llm.DEFAULT_URL, local_only=False))) == 3

    monkeypatch.setenv(llm.MODEL_ENV, "gpt-oss:120b-cloud")
    assert llm.settings().model == ""
    llm.save_settings(llm.OllamaSettings(local_only=False))
    assert llm.settings().model == "gpt-oss:120b-cloud"
    llm.save_settings(llm.OllamaSettings())
