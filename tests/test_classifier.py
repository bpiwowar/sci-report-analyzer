"""Embeddings (Ollama, OpenAI-compatible), a folder's models, and the excerpts' category
suggested from them (trained on those filed)."""

import asyncio
import re

import httpx
import numpy as np
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
    assert (t.count, t.categories, t.prototypes, t.model) == (5, 3, 3, "nomic-embed-text")
    assert t.top3 == 0.8  # (the lone award, hidden, not to be found)
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


def _blobs(rng, centre, n, noise=0.05):
    return classifier._unit(centre + noise * rng.standard_normal((n, len(centre))))


def test_clusters() -> None:
    """A category of two far groups: a cluster each (none smaller than allowed, at most as
    many as allowed); a small one, or without threshold: its mean."""
    rng = np.random.default_rng(0)
    e = np.eye(8)
    x = np.vstack([_blobs(rng, e[0], 6), _blobs(rng, e[1], 6)])
    found = classifier.clusters(x, 0.9, 5, 10)
    assert sorted(sorted(c.tolist()) for c in found) == [list(range(6)), list(range(6, 12))]
    assert len(classifier.clusters(x, 0.9, 5, 1)) == 1  # (at most one)
    assert len(classifier.clusters(x, 0.9, 7, 10)) == 1  # (clusters of 7 at least)
    assert len(classifier.clusters(x, None, 5, 10)) == 1
    # A lone excerpt far from the others: dissolved in them
    x = np.vstack([_blobs(rng, e[0], 10), e[2:3]])
    assert [len(c) for c in classifier.clusters(x, 0.9, 5, 10)] == [11]


def test_fit_chooses_clusters() -> None:
    """A category made of two groups, another between them: a single mean each confuses
    them, two prototypes for the first do not (chosen by leave-one-out); otherwise one."""
    rng = np.random.default_rng(1)
    e = np.eye(8)
    x = np.vstack(
        [
            _blobs(rng, e[0], 6),
            _blobs(rng, e[1], 6),
            _blobs(rng, (e[0] + e[1]) / np.sqrt(2) + 0.3 * e[2], 6),
        ]
    )
    labels = np.array([1] * 12 + [2] * 6)
    best = classifier.fit(x, labels, 5, 10)
    assert best.threshold is not None and best.top1 == 1.0
    assert [len(best.prototypes[c]) for c in (1, 2)] == [2, 1]
    assert classifier._fit(x, labels, None, 5, 10).top1 < 1.0
    # Every category one group: a single mean each
    x = np.vstack([_blobs(rng, e[0], 10), _blobs(rng, e[1], 10)])
    best = classifier.fit(x, np.array([1] * 10 + [2] * 10), 5, 10)
    assert best.threshold is None and best.top1 == 1.0


def test_trained_before_clusters(embeddings) -> None:
    """Trained before the clusters (a mean per category): still suggesting."""
    from sci_report_analyzer.db.models import ExcerptClassifier
    from sci_report_analyzer.db.session import session_scope

    folder, _period, cats = _folder()
    asyncio.run(classifier.train(folder))
    with session_scope() as s:
        row = s.get(ExcerptClassifier, folders.settings_id(s, folder))
        row.centroids = {c: p[0] for c, p in row.centroids["prototypes"].items()}
    t = classifier.trained(folder)
    assert (t.categories, t.prototypes, t.top1) == (3, 3, None)
    found = asyncio.run(classifier.suggest(folder, "A search engine"))
    assert found[0][0] == cats["Research"]


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
    await user.should_see(marker="classifier-accuracy")
    await user.should_see(marker=f"category-suggested-{cats['Research']}")
    user.find(marker=f"category-suggested-{cats['Research']}").click()
    user.find(marker="excerpt-edit-save").click()
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


async def test_cluster_settings(user: User, monkeypatch) -> None:
    """Settings → Local LLM: the clusters' bounds (saved, the rest kept)."""
    monkeypatch.delenv(llm.EMBED_MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, embed_model="bge-m3"))
    try:
        await user.open("/settings?tab=llm")
        await user.should_see(marker="llm-cluster-max")
        assert user.find(marker="llm-cluster-min").elements.pop().value == 5
        user.find(marker="llm-cluster-max").elements.pop().value = 4
        user.find(marker="llm-cluster-min").elements.pop().value = 3
        user.find(marker="llm-save").click()
        await user.should_see("Saved")
        st = llm.settings()
        assert (st.cluster_max, st.cluster_min, st.embed_model) == (4, 3, "bge-m3")
    finally:
        llm.save_settings(llm.OllamaSettings())


def test_evaluate_and_keep(embeddings) -> None:
    """Each excerpt hidden, then suggested: the rates (overall, by category); one filed in
    the wrong category suggested in the right one; kept, no longer listed
    (moved: no longer kept)."""
    folder, period, cats = _folder()
    wrong = categories.add_excerpt(cats["Research"], period, "A course for students", 1, [])
    ev = asyncio.run(classifier.evaluate(folder))
    assert len(ev.excerpts) == 6
    assert {e.excerpt_id: e for e in ev.excerpts}[wrong].suggested == cats["Teaching"]
    assert ev.rates()[cats["Teaching"]] == (0, 2)  # (closer to the wrong one)
    assert ev.rates()[cats["Research"]] == (2, 3)
    assert wrong in [e.excerpt_id for e in ev.wrong]
    classifier.keep(folder, wrong)
    assert classifier.kept(folder) == {wrong: cats["Research"]}
    ev = asyncio.run(classifier.evaluate(folder))
    assert wrong not in [e.excerpt_id for e in ev.wrong]
    assert ev.rates()[cats["Research"]] == (2, 3)  # (still missed by the suggestions)
    # Moved: no longer kept (where it was kept).
    categories.move_excerpt(wrong, cats["Teaching"])
    ev = asyncio.run(classifier.evaluate(folder))
    assert ev.rates()[cats["Teaching"]] == (2, 3) and ev.rates()[cats["Research"]] == (2, 2)
    assert not {e.excerpt_id: e for e in ev.excerpts}[wrong].kept


def test_evaluate_without_model_or_excerpt(embeddings) -> None:
    folder = folders.save_folder(None, "Empty")
    with pytest.raises(ValueError):
        asyncio.run(classifier.evaluate(folder))
    llm.save_settings(llm.OllamaSettings())
    with pytest.raises(RuntimeError):
        asyncio.run(classifier.evaluate(folder))


async def test_classification_dialog(user: User, embeddings) -> None:
    """Parameters → Folders: a settings' classification (the rates, those suggested
    elsewhere); one moved where suggested, another kept: no longer listed, the rates
    updated; trained on them."""
    folder, period, cats = _folder()
    wrong = categories.add_excerpt(cats["Research"], period, "A course for students", 1, [])
    award = next(e.id for e in categories.excerpts(period) if e.text == "Best paper award")
    sid = folders.settings_of(folder)
    await user.open("/settings?tab=folders")
    user.find(marker=f"settings-classification-{sid}").click()
    await user.should_see(marker="classification-rate")
    await user.should_see(marker=f"classification-category-{cats['Research']}")
    await user.should_see(marker=f"reclassify-excerpt-{wrong}")
    await user.should_see("Move to Teaching")
    user.find(marker=f"reclassify-move-{wrong}").click()
    await user.should_not_see(marker=f"reclassify-excerpt-{wrong}")
    filed = {e.id: e.category_id for e in categories.excerpts(period)}
    assert filed[wrong] == cats["Teaching"]
    await user.should_see(marker=f"reclassify-excerpt-{award}")
    user.find(marker=f"reclassify-keep-{award}").click()
    await user.should_not_see(marker=f"reclassify-excerpt-{award}")
    await user.should_see(marker="classification-kept-count")
    assert classifier.kept(folder) == {award: cats["Awards"]}
    user.find(marker="classification-train").click()
    await user.should_see("Learnt from 6 excerpts")
    assert classifier.trained(folder).count == 6


async def test_classification_dialog_off(user: User, monkeypatch) -> None:
    """Without an embedding model (or a category, or an excerpt): said, and where to set
    it."""
    monkeypatch.delenv(llm.EMBED_MODEL_ENV, raising=False)
    folder = folders.save_folder(None, "Hiring 2026")
    sid = folders.settings_of(folder)
    await user.open("/settings?tab=folders")
    user.find(marker=f"settings-classification-{sid}").click()
    await user.should_see(marker="classification-off")
    await user.should_see("choose an embedding model", retries=20)


async def test_deleting_a_category_reclassifies_its_excerpts(user: User, embeddings) -> None:
    """A category deleted with excerpts: each listed with the category suggested (not
    itself), moved there or elsewhere one by one; all moved, deleted."""
    folder, period, cats = _folder()
    prize = categories.add_excerpt(cats["Awards"], period, "A teaching prize", 1, [])
    award = next(e.id for e in categories.excerpts(period) if e.text == "Best paper award")
    await classifier.train(folder)
    sid = folders.settings_of(folder)
    await user.open("/settings?tab=folders")
    user.find(marker=f"settings-categories-{sid}").click()
    await user.should_see(marker=f"category-delete-{cats['Awards']}")
    user.find(marker=f"category-delete-{cats['Awards']}").click()
    await user.should_see(marker="category-delete-blocked")
    await user.should_see(marker=f"reclassify-excerpt-{prize}")
    await user.should_see(marker=f"reclassify-move-{prize}")
    await user.should_see("Move to Teaching")
    user.find(marker=f"reclassify-move-{prize}").click()
    await user.should_not_see(marker=f"reclassify-excerpt-{prize}")
    user.find(marker=f"reclassify-other-{award}").elements.pop().value = cats["Research"]
    await user.should_see(marker="category-delete-ready")
    filed = {e.id: e.category_id for e in categories.excerpts(period)}
    assert (filed[prize], filed[award]) == (cats["Teaching"], cats["Research"])
    user.find(marker="category-delete-ok").click()
    await user.should_not_see(marker=f"category-delete-{cats['Awards']}")
    assert [n.name for n in categories.tree(folder)] == ["Research", "Teaching"]


async def test_deleting_a_category_untrained(user: User, embeddings) -> None:
    """Never trained: no suggestion, trained from there, then suggested."""
    folder, period, cats = _folder()
    award = next(e.id for e in categories.excerpts(period) if e.text == "Best paper award")
    sid = folders.settings_of(folder)
    await user.open("/settings?tab=folders")
    user.find(marker=f"settings-categories-{sid}").click()
    await user.should_see(marker=f"category-delete-{cats['Awards']}")
    user.find(marker=f"category-delete-{cats['Awards']}").click()
    await user.should_see(marker="category-delete-no-suggestion")
    user.find(marker="category-delete-train").click()
    await user.should_see(marker=f"reclassify-move-{award}")
    assert classifier.trained(folder) is not None
