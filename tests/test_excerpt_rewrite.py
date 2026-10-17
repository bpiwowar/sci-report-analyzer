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
    assert all(f'"{k}"' in prompt for k in ("influence", "punctual", "in_period", "days"))
    assert set(excerpt_rewrite.SCHEMA["required"]) == {
        "text",
        "start_year",
        "end_year",
        "ongoing",
        "influence",
        "punctual",
        "in_period",
        "days",
    }


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
        (  # (its influence, timing and days predicted)
            {
                "text": "Keynote at the Z conference",
                "start_year": 2022,
                "end_year": 2022,
                "ongoing": False,
                "influence": True,
                "punctual": True,
                "in_period": False,
                "days": 2,
            },
            Rewrite(
                text="Keynote at the Z conference",
                start=2022,
                end=2022,
                influence=True,
                punctual=True,
                days=2.0,
            ),
        ),
        (  # (not both one-off and within the period: one-off; days at most a year)
            {
                "text": "Reviewer",
                "start_year": None,
                "end_year": None,
                "ongoing": False,
                "punctual": True,
                "in_period": True,
                "days": 1000,
            },
            Rewrite(text="Reviewer", punctual=True, days=366.0),
        ),
        (  # (invalid predictions: as if not given, the rest kept)
            {
                "text": "Reviewer",
                "start_year": 2021,
                "end_year": None,
                "ongoing": False,
                "influence": "maybe",
                "punctual": None,
                "in_period": True,
                "days": "a lot",
            },
            Rewrite(text="Reviewer", start=2021, in_period=True),
        ),
        (
            {
                "text": "Reviewer",
                "start_year": None,
                "end_year": None,
                "ongoing": False,
                "days": -3,
            },
            Rewrite(text="Reviewer"),
        ),
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
    """Settings → Rewriting the excerpts: its model, among the server's (none; by default, the
    general one); the local LLM off: said."""
    await user.open("/settings?tab=rewrite")
    await user.should_see(marker="llm-task-rewrite")
    select = user.find(marker="llm-task-rewrite").elements.pop()
    assert select.value == "gemma3:4b" and llm.NONE in select.options and "" in select.options
    select.value = llm.NONE
    await user.should_not_see(marker="llm-off-link")
    user.find(marker="llm-task-rewrite-think").elements.pop().value = "on"
    user.find(marker="rewrite-prompt").elements.pop().value = "Mine: {text}"
    user.find(marker="rewrite-save").click()
    await user.should_see("Saved")
    assert llm.stored_settings().rewrite_model == llm.NONE and llm.rewrite_model() == ""
    assert llm.stored_settings().task_think == {"rewrite": True}
    assert excerpt_rewrite.prompt() == "Mine: {text}"
    excerpt_rewrite.save_prompt("")
    assert excerpt_rewrite.prompt() == excerpt_rewrite.PROMPT
    llm.save_settings(llm.OllamaSettings(enabled=False))
    await user.open("/settings?tab=rewrite")
    await user.should_see(marker="llm-off-link")


def test_edited_instructions() -> None:
    """The instructions edited: sent; not a valid template (braces of their own): the default
    ones."""
    excerpt_rewrite.save_prompt("Rewrite {text} (around: {before} / {after})")
    try:
        [m] = excerpt_rewrite.messages("Led X", "In 2020,", "and Y.")
        assert m["content"] == "Rewrite Led X (around: In 2020, / and Y.)"
        excerpt_rewrite.save_prompt('Rewrite {passage} as {"json": 1}')
        [m] = excerpt_rewrite.messages("Led X", "", "")
        assert m["content"].startswith("You rewrite a passage") and "Led X" in m["content"]
    finally:
        excerpt_rewrite.save_prompt("")


def test_trial(model) -> None:
    """A passage rewritten by a given model (to compare them): timed, its error said."""
    answer = {"text": "Reviewer", "start_year": 2020, "end_year": None, "ongoing": True}
    with respx.mock:
        route = respx.post(CHAT).mock(return_value=_answer(answer))
        t = asyncio.run(excerpt_rewrite.trial("I review", "", "", "qwen3:8b"))
        assert json.loads(route.calls[0].request.content)["model"] == "qwen3:8b"
        assert (t.model, t.rewrite, t.error) == (
            "qwen3:8b",
            Rewrite(text="Reviewer", start=2020),
            "",
        )
        assert t.seconds >= 0
        route.mock(return_value=_answer("not json"))
        t = asyncio.run(excerpt_rewrite.trial("I review", "", "", "qwen3:8b"))
        assert t.rewrite is None and t.error
        route.mock(return_value=_answer({"text": ""}))
        t = asyncio.run(excerpt_rewrite.trial("I review", "", "", "qwen3:8b"))
        assert t.rewrite is None and t.error == ""  # (an answer, not valid)
        route.mock(return_value=httpx.Response(200, json={"error": "out of memory"}))
        t = asyncio.run(excerpt_rewrite.trial("I review", "", "", "gemma3:4b"))
        assert (t.rewrite, t.error) == (None, "out of memory")
