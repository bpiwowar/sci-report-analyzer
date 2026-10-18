"""Ollama's models: the size of a download (the registry's manifest), the download with its
progress and cancellation, the deletion, what uses a model, and where a missing model is
offered for download."""

import asyncio
import json

import httpx
import pytest
import respx
from nicegui.testing import User

from sci_report_analyzer import llm

MANIFEST = "https://registry.ollama.ai/v2/library/qwen3/manifests/8b"
OLLAMA = "http://localhost:11434"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for env in (llm.URL_ENV, llm.MODEL_ENV, llm.EMBED_MODEL_ENV, llm.REWRITE_MODEL_ENV):
        monkeypatch.delenv(env, raising=False)
    yield
    llm.save_settings(llm.OllamaSettings())


def _manifest(*sizes: int) -> httpx.Response:
    return httpx.Response(
        200, json={"config": {"size": 10}, "layers": [{"size": s} for s in sizes]}
    )


def _lines(*chunks: dict) -> bytes:
    return "".join(json.dumps(c) + "\n" for c in chunks).encode()


def test_manifest_size() -> None:
    with respx.mock:
        route = respx.get(MANIFEST).mock(return_value=_manifest(1000, 4_700_000_000))
        assert asyncio.run(llm.manifest_size("qwen3:8b")) == 4_700_001_010
        sent = route.calls.last.request
        assert sent.headers["accept"] == "application/vnd.docker.distribution.manifest.v2+json"
        # (a name with a namespace; the tag latest by default)
        other = respx.get("https://registry.ollama.ai/v2/someone/tiny/manifests/latest").mock(
            return_value=_manifest(5)
        )
        assert asyncio.run(llm.manifest_size("someone/tiny")) == 15
        assert other.called


def test_manifest_size_unknown() -> None:
    with respx.mock:
        respx.get(MANIFEST).mock(return_value=httpx.Response(404))
        assert asyncio.run(llm.manifest_size("qwen3:8b")) is None
        respx.get(MANIFEST).mock(side_effect=httpx.ConnectError("offline"))
        assert asyncio.run(llm.manifest_size("qwen3:8b")) is None
    # (another registry: not asked)
    assert asyncio.run(llm.manifest_size("hf.co/someone/model:Q4")) is None


def test_format_size() -> None:
    assert llm.format_size(4_700_000_000) == "4.7 GB"
    assert llm.format_size(512) == "512 B"
    assert llm.format_size(274_000_000) == "274.0 MB"


def test_pull_progress() -> None:
    seen = []
    with respx.mock:
        route = respx.post(f"{OLLAMA}/api/pull").mock(
            return_value=httpx.Response(
                200,
                content=_lines(
                    {"status": "pulling manifest"},
                    {"status": "pulling abc", "total": 100, "completed": 40},
                    {"status": "pulling abc", "total": 100, "completed": 100},
                    {"status": "success"},
                ),
            )
        )
        asyncio.run(llm.pull_model(OLLAMA, "qwen3:8b", lambda *a: seen.append(a)))
    assert json.loads(route.calls.last.request.content) == {"model": "qwen3:8b", "stream": True}
    assert seen == [
        ("pulling manifest", 0, 0),
        ("pulling abc", 40, 100),
        ("pulling abc", 100, 100),
        ("success", 0, 0),
    ]


def test_pull_error() -> None:
    with respx.mock:
        respx.post(f"{OLLAMA}/api/pull").mock(
            return_value=httpx.Response(
                200, content=_lines({"status": "pulling manifest"}, {"error": "disk full"})
            )
        )
        with pytest.raises(RuntimeError, match="disk full"):
            asyncio.run(llm.pull_model(OLLAMA, "qwen3:8b", lambda *a: None))
        respx.post(f"{OLLAMA}/api/pull").mock(
            return_value=httpx.Response(404, json={"error": "pull model manifest: not found"})
        )
        with pytest.raises(RuntimeError, match="not found"):
            asyncio.run(llm.pull_model(OLLAMA, "nope", lambda *a: None))


def test_pull_cancelled() -> None:
    async def stream():
        yield _lines({"status": "pulling abc", "total": 100, "completed": 10})
        for i in range(1000):  # (never done: cancelled first)
            await asyncio.sleep(0.01)
            yield _lines({"status": "pulling abc", "total": 100, "completed": 11 + i % 80})

    async def run() -> list:
        seen: list = []
        with respx.mock:
            respx.post(f"{OLLAMA}/api/pull").mock(
                return_value=httpx.Response(200, content=stream())
            )
            task = asyncio.ensure_future(
                llm.pull_model(OLLAMA, "qwen3:8b", lambda *a: seen.append(a))
            )
            while not seen:
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.wait({task})
            assert task.cancelled()
        return seen

    assert asyncio.run(run())[0] == ("pulling abc", 10, 100)


def test_installed_and_delete() -> None:
    with respx.mock:
        respx.get(f"{OLLAMA}/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b", "size": 5_200_000_000},
                        {"name": "gpt-oss:120b-cloud", "size": 0},
                    ]
                },
            )
        )
        assert asyncio.run(llm.installed_models(OLLAMA)) == {"qwen3:8b": 5_200_000_000}
        route = respx.delete(f"{OLLAMA}/api/delete").mock(return_value=httpx.Response(200))
        asyncio.run(llm.delete_model(OLLAMA, "qwen3:8b"))
        assert json.loads(route.calls.last.request.content) == {"model": "qwen3:8b"}
        route.mock(return_value=httpx.Response(404, json={"error": "not found"}))
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(llm.delete_model(OLLAMA, "nope"))


def test_same_model() -> None:
    assert llm.same_model("bge-m3", "bge-m3:latest")
    assert not llm.same_model("qwen3:8b", "qwen3:14b")
    assert not llm.same_model("", "")
    assert llm.is_installed("bge-m3", ["qwen3:8b", "bge-m3:latest"])


def test_model_users() -> None:
    llm.save_settings(
        llm.OllamaSettings(
            enabled=True,
            model="qwen3:8b",
            embed_model="bge-m3",
            rewrite_model="qwen3:8b",
            tasks={"career": "mistral"},
        )
    )
    assert len(llm.model_users("qwen3:8b")) == 2  # (the general model, the rewriting one)
    assert any("career" in u.lower() or "Reading" in u for u in llm.model_users("mistral:latest"))
    assert llm.model_users("bge-m3:latest")
    assert llm.model_users("unused:1b") == []
    # (the models of an OpenAI-compatible server are not Ollama's)
    llm.save_settings(llm.OllamaSettings(model="qwen3:8b", chat_api="openai"))
    assert llm.model_users("qwen3:8b") == []


def test_no_offer_for_cloud_nor_openai() -> None:
    assert llm.can_pull("ollama", "qwen3:8b")
    assert not llm.can_pull("ollama", "gpt-oss:120b-cloud")
    assert not llm.can_pull("openai", "qwen3:8b")
    assert not llm.can_pull("ollama", "  ")


def type_model(select, name: str) -> None:
    """(a model typed in a select: added to its options, then chosen)"""
    select.set_options([*select.options, name], value=name)


def _tags(*models: tuple[str, int]) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n, "size": s} for n, s in models]})


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_settings_lists_and_deletes(user: User) -> None:
    """Settings → Local LLM: the installed models with their sizes; a model in use cannot be
    deleted (what uses it said), another can, once confirmed."""
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3:8b"))
    with respx.mock:
        respx.get(f"{OLLAMA}/api/tags").mock(
            return_value=_tags(("qwen3:8b", 5_200_000_000), ("gemma3:4b", 3_300_000_000))
        )
        delete = respx.delete(f"{OLLAMA}/api/delete").mock(return_value=httpx.Response(200))
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-installed-gemma3:4b")
        await user.should_see("3.3 GB")
        used = user.find(marker="llm-model-delete-qwen3:8b").elements.pop()
        assert not used.enabled
        free = user.find(marker="llm-model-delete-gemma3:4b").elements.pop()
        assert free.enabled
        user.find(marker="llm-model-delete-gemma3:4b").click()
        await user.should_see("This deletes gemma3:4b (3.3 GB) from the Ollama server")
        user.find(marker="llm-delete-confirm").click()
        await user.should_see("gemma3:4b deleted")
        assert json.loads(delete.calls.last.request.content) == {"model": "gemma3:4b"}


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_missing_model_offered_and_downloaded(user: User) -> None:
    """A model typed that the server has not got: the offer, with its size, then the
    download; the model is selected."""
    llm.save_settings(llm.OllamaSettings(enabled=True))
    with respx.mock:
        tags = respx.get(f"{OLLAMA}/api/tags").mock(return_value=_tags(("gemma3:4b", 1)))
        respx.get(MANIFEST).mock(return_value=_manifest(4_700_000_000 - 10))
        pull = respx.post(f"{OLLAMA}/api/pull").mock(
            return_value=httpx.Response(
                200,
                content=_lines(
                    {"status": "pulling abc", "total": 100, "completed": 100}, {"status": "success"}
                ),
            )
        )
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-installed-gemma3:4b")
        select = user.find(marker="llm-model").elements.pop()
        type_model(select, "qwen3:8b")
        await user.should_see("Download qwen3:8b (4.7 GB)?")
        tags.mock(return_value=_tags(("gemma3:4b", 1), ("qwen3:8b", 4_700_000_000)))
        user.find(marker="llm-pull-confirm").click()
        await user.should_see("qwen3:8b downloaded")
        assert pull.called
        assert json.loads(pull.calls.last.request.content)["model"] == "qwen3:8b"
        assert select.value == "qwen3:8b"
        await user.should_see(marker="llm-installed-qwen3:8b")


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_no_offer_for_cloud_models(user: User) -> None:
    llm.save_settings(llm.OllamaSettings(enabled=True))
    with respx.mock:
        respx.get(f"{OLLAMA}/api/tags").mock(return_value=_tags(("gemma3:4b", 1)))
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-installed-gemma3:4b")
        type_model(user.find(marker="llm-model").elements.pop(), "gpt-oss:120b-cloud")
        await asyncio.sleep(0.5)
        await user.should_not_see(marker="llm-pull-confirm")


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_no_offer_for_openai_backend(user: User) -> None:
    llm.save_settings(
        llm.OllamaSettings(enabled=True, chat_api="openai", chat_url="http://localhost:8080/v1")
    )
    with respx.mock:
        respx.get(f"{OLLAMA}/api/tags").mock(return_value=_tags(("gemma3:4b", 1)))
        respx.get("http://localhost:8080/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "x"}]})
        )
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-installed-gemma3:4b")
        type_model(user.find(marker="llm-model").elements.pop(), "qwen3:8b")
        await asyncio.sleep(0.5)
        await user.should_not_see(marker="llm-pull-confirm")
        await user.should_see(marker="llm-models-note")
