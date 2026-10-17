"""A new excerpt rewritten by the local LLM (excerpt_rewrite): its model (a setting), the
text sent (the lines around it), its structured answer, and none on any error."""

import asyncio
import json

import httpx
import pytest
import respx
from nicegui.testing import User

from sci_report_analyzer import excerpt_rewrite, llm
from sci_report_analyzer.excerpt_rewrite import Rewrite, context_window, rewrite

CHAT = "http://localhost:11434/api/chat"


@pytest.fixture
def model(monkeypatch):
    monkeypatch.delenv(llm.URL_ENV, raising=False)
    monkeypatch.delenv(llm.REWRITE_MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3", rewrite_model="gemma3:4b"))
    yield
    llm.save_settings(llm.OllamaSettings())


def _answer(content) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"message": {"content": text}})


def test_setting(monkeypatch, model) -> None:
    assert llm.rewrite_model() == "gemma3:4b"
    monkeypatch.setenv(llm.REWRITE_MODEL_ENV, "qwen3:8b")
    assert llm.rewrite_model() == "qwen3:8b"
    monkeypatch.setenv(llm.REWRITE_MODEL_ENV, "none")
    assert llm.rewrite_model() == ""
    monkeypatch.setenv(llm.REWRITE_MODEL_ENV, "gpt-oss:120b-cloud")
    assert llm.rewrite_model() == ""
    monkeypatch.delenv(llm.REWRITE_MODEL_ENV)
    llm.save_settings(llm.OllamaSettings(enabled=False, rewrite_model="gemma3:4b"))
    assert llm.rewrite_model() == ""  # (the LLM off)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3", rewrite_model="none"))
    assert llm.rewrite_model() == ""
    # None chosen: the general model.
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    assert llm.rewrite_model() == "qwen3"


def test_none_no_call(monkeypatch, model) -> None:
    monkeypatch.setenv(llm.REWRITE_MODEL_ENV, "none")
    with respx.mock:
        route = respx.post(CHAT).mock(return_value=_answer({}))
        assert asyncio.run(rewrite("XXX", "j'ai été ", " et YYY.")) is None
        assert not route.called


def test_context_window() -> None:
    before = "Line A\nLine B\nLine C\nEntre 2019 et 2021, j'ai été "
    after = " et YYY.\nLine D\nLine E\nLine F"
    assert context_window(before, after) == (
        "Line B\nLine C\nEntre 2019 et 2021, j'ai été ",
        " et YYY.\nLine D\nLine E",
    )
    assert context_window(before, after, 1) == (
        "Line C\nEntre 2019 et 2021, j'ai été ",
        " et YYY.\nLine D",
    )
    assert context_window("", "") == ("", "")


def test_rewrite(model) -> None:
    answer = {
        "text": "Membre du comité de direction de l'institut X",
        "start_year": 2019,
        "end_year": 2021,
        "ongoing": False,
    }
    with respx.mock:
        route = respx.post(CHAT).mock(return_value=_answer(answer))
        got = asyncio.run(
            rewrite(
                "membre du comité de direction de l'institut X",
                "Line 0\nLine A\nLine B\nEntre 2019 et 2021, j'ai été ",
                " et trésorière de Y.\nLine C",
            )
        )
    assert got == Rewrite(
        text="Membre du comité de direction de l'institut X", start=2019, end=2021
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "gemma3:4b" and sent["format"] == excerpt_rewrite.SCHEMA
    assert sent["think"] is False
    prompt = sent["messages"][0]["content"]
    assert "Entre 2019 et 2021, j'ai été" in prompt and "Line 0" not in prompt
    assert "Line A" in prompt and "Line C" in prompt


@pytest.mark.parametrize(
    "answer, expected",
    [
        (
            {
                "text": " Chair  of the Y committee ",
                "start_year": 2020,
                "end_year": 2024,
                "ongoing": True,
            },
            Rewrite(text="Chair of the Y committee", start=2020, end=None),
        ),
        (
            {"text": "Reviewer", "start_year": 2022, "end_year": 2018, "ongoing": False},
            Rewrite(text="Reviewer", start=2018, end=2022),
        ),
        (
            {"text": "Reviewer", "start_year": 20, "end_year": None, "ongoing": False},
            Rewrite(text="Reviewer"),
        ),
        ({"text": "", "start_year": None, "end_year": None, "ongoing": False}, None),
        ({"start_year": 2019}, None),
        ({"text": "Reviewer", "start_year": "soon", "end_year": None, "ongoing": False}, None),
    ],
)
def test_proposal(answer, expected) -> None:
    assert excerpt_rewrite.proposal(answer) == expected


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"message": {"content": "not json"}}),
        httpx.Response(200, json={"message": {"content": '{"text": 3}'}}),
        httpx.Response(500, text="model not found"),
        httpx.Response(200, json={"error": "out of memory"}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("too slow"),
    ],
)
def test_fallback(model, response) -> None:
    with respx.mock:
        route = respx.post(CHAT)
        if isinstance(response, Exception):
            route.mock(side_effect=response)
        else:
            route.mock(return_value=response)
        assert asyncio.run(rewrite("XXX", "Entre 2019 et 2021, j'ai été ", " et YYY.")) is None


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_settings_page(user: User, model) -> None:
    """Settings → Local LLM: the model rewriting the excerpts, among the server's (none; by
    default, the general one)."""
    await user.open("/settings?tab=llm")
    await user.should_see(marker="llm-rewrite-model")
    select = user.find(marker="llm-rewrite-model").elements.pop()
    assert select.value == "gemma3:4b" and llm.NONE in select.options and "" in select.options
    select.value = llm.NONE
    user.find(marker="llm-save").click()
    await user.should_see("Saved")
    assert llm.stored_settings().rewrite_model == llm.NONE and llm.rewrite_model() == ""
