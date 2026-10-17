"""Embeddings (Ollama, OpenAI-compatible), a folder's models, and the excerpts' category
suggested from them (trained on those filed)."""

import asyncio
import re

import httpx
import pytest
import respx
from helpers import PDF, make_person
from nicegui.testing import User

from sci_report_analyzer import categories, classifier, documents, folders, llm

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

VOCABULARY = ("ranking", "search", "teaching", "course", "students", "award", "prize")


async def _fake_embed(texts, folder_id=None, timeout=0.0):
    """Bags of words over VOCABULARY (counted, to check what is embedded)."""
    _fake_embed.calls += len(texts)
    return [[float(w in t.lower()) for w in VOCABULARY] + [0.01] for t in texts]


@pytest.fixture
def embeddings(monkeypatch):
    monkeypatch.delenv(llm.EMBED_MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, embed_model="nomic-embed-text"))
    _fake_embed.calls = 0
    monkeypatch.setattr(llm, "embed", _fake_embed)
    yield
    llm.save_settings(llm.OllamaSettings())


def _folder() -> tuple[int, int, dict[str, int]]:
    folder = folders.save_folder(None, "Hiring 2026")
    period = folders.add_person(folder, make_person("Jane Doe"))
    cats = {n: categories.add(folder, n) for n in ("Research", "Teaching", "Awards")}
    for cat, text in [
        ("Research", "A ranking model for search"),
        ("Research", "Neural ranking"),
        ("Teaching", "A course for 200 students"),
        ("Teaching", "Teaching IR"),
        ("Awards", "Best paper award"),
    ]:
        categories.add_excerpt(cats[cat], period, text, 1, [])
    return folder, period, cats


def test_embed_apis(monkeypatch) -> None:
    monkeypatch.delenv(llm.URL_ENV, raising=False)
    monkeypatch.delenv(llm.EMBED_MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, url="gpu:11434", embed_model="bge-m3"))
    with respx.mock:
        route = respx.post("http://gpu:11434/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]})
        )
        assert asyncio.run(llm.embed(["a", "b"])) == [[1.0, 0.0], [0.0, 1.0]]
        assert route.calls[0].request.content == b'{"model":"bge-m3","input":["a","b"]}'
    llm.save_settings(
        llm.OllamaSettings(
            enabled=True,
            embed_api="openai",
            embed_url="http://box:8080/v1/",
            embed_model="e5",
        )
    )
    data = [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]
    with respx.mock:
        route = respx.post("http://box:8080/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": data})
        )
        assert asyncio.run(llm.embed(["a", "b"])) == [[1.0], [2.0]]
        respx.get("http://box:8080/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "e5"}, {"id": "bge"}]})
        )
        assert asyncio.run(llm.list_embed_models("openai", "http://box:8080/v1")) == ["bge", "e5"]
    llm.save_settings(llm.OllamaSettings())


def test_folder_models(monkeypatch) -> None:
    monkeypatch.delenv(llm.MODEL_ENV, raising=False)
    monkeypatch.delenv(llm.EMBED_MODEL_ENV, raising=False)
    folder = folders.save_folder(None, "Hiring 2026")
    llm.save_settings(llm.OllamaSettings(model="qwen3", embed_model="bge-m3"))
    assert (llm.model(folder), llm.embed_model(folder)) == ("", "")  # (the LLM off)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3", embed_model="bge-m3"))
    assert (llm.model(folder), llm.embed_model(folder)) == ("qwen3", "bge-m3")
    llm.save_folder_models(folder, "gemma3", " ")
    assert llm.folder_models(folder) == ("gemma3", "")
    assert (llm.model(folder), llm.embed_model(folder)) == ("gemma3", "bge-m3")
    assert llm.model(None) == "qwen3"
    llm.save_settings(llm.OllamaSettings())


def test_train_and_suggest(embeddings) -> None:
    folder, period, cats = _folder()
    assert classifier.trained(folder) is None
    assert asyncio.run(classifier.suggest(folder, "ranking")) == []  # (not trained)
    t = asyncio.run(classifier.train(folder))
    assert (t.count, t.categories, t.model) == (5, 3, "nomic-embed-text")
    assert classifier.trained(folder).count == 5
    found = asyncio.run(classifier.suggest(folder, "A search engine"))
    assert found[0][0] == cats["Research"] and len(found) == 3
    assert asyncio.run(classifier.suggest(folder, "Students", k=1))[0][0] == cats["Teaching"]
    # Trained again: only the new excerpts embedded (the others kept).
    categories.add_excerpt(cats["Awards"], period, "A prize", 1, [])
    calls = _fake_embed.calls
    assert asyncio.run(classifier.train(folder)).count == 6
    assert _fake_embed.calls == calls + 1
    # Another model in the folder: to train again.
    llm.save_folder_models(folder, "", "bge-m3")
    assert asyncio.run(classifier.suggest(folder, "A search engine")) == []


def test_train_without_excerpt(embeddings) -> None:
    folder = folders.save_folder(None, "Empty")
    with pytest.raises(ValueError):
        asyncio.run(classifier.train(folder))


async def test_suggestions_in_the_picker(user: User, fake_viewer, embeddings):
    """Filing an excerpt: its categories suggested once trained (Train, in the dialog), when
    it was trained shown; a suggestion clicked files it."""
    folder, period, cats = _folder()
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "Our ranking model beats sparse search", "p": 1, "rects": []}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}?folder={folder}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-suggestions")
    await user.should_see("Never trained")
    await user.should_see("No suggestion (train first)")
    user.find(marker="classifier-train").click()
    await user.should_see("Learnt from 5 excerpts")
    await user.should_see("Trained on")
    await user.should_see(marker=f"category-suggested-{cats['Research']}")
    user.find(marker=f"category-suggested-{cats['Research']}").click()
    await user.should_see("Added to Research")
    filed = [e for e in categories.excerpts(period) if e.quoted.startswith("Our ranking")]
    assert [e.category_id for e in filed] == [cats["Research"]]


async def test_suggestions_greyed_without_model(user: User, fake_viewer):
    """Without an embedding model: the suggestions shown greyed, saying what to set."""
    folder, period, _cats = _folder()
    doc = documents.add(period, "Application.pdf", PDF)
    selection = {"text": "Our ranking model", "p": 1, "rects": []}
    user.javascript_rules[re.compile(r"vrPdf\.(selection|excerpted)\(\)")] = lambda _: selection
    await user.open(f"/doc/{doc}?folder={folder}")
    await user.should_see(marker="pdf-excerpt")
    user.find(marker="pdf-excerpt").click()
    await user.should_see(marker="category-suggestions-off")
    assert "disable" in user.find(marker="classifier-train").elements.pop().props
