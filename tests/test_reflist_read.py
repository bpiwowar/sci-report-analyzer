"""A pasted list of references read by the local LLM (reflist_read): its parts, the answer
read into items (no DOI made up), their matches by title, the parts without a valid answer
split as before; its settings page and the Tag from a list dialog."""

import asyncio
import json

import httpx
import pytest
import respx
from nicegui.testing import User
from test_reflist import PASTED, _person

from sci_report_analyzer import llm, pubview, reflist, reflist_read
from sci_report_analyzer.reflist_read import Reference, parts

CHAT = "http://localhost:11434/api/chat"

# The same list as PASTED, messier: items run together, a title cut by a page number.
MESSY = """\
1- Learning to rank with sparse represen-
tations of documents. J. Doe, B. Smith. SIGIR 2022. 2. Neural retrieval models for
12
long documents J. Doe, A. Other. ECIR, 2021
"""


def _ref(**kw) -> dict:
    return {
        "number": None,
        "title": "",
        "authors": [],
        "venue": "",
        "year": None,
        "doi": "",
        "url": "",
        **kw,
    }


READ = {
    "references": [
        _ref(
            number=1,
            title="Learning to rank with sparse representations of documents",
            authors=["J. Doe", "B. Smith"],
            venue="SIGIR",
            year=2022,
            doi="10.9999/made.up",  # (not in the list: left out)
        ),
        _ref(
            number=2,
            title="Neural retrieval models for long documents",
            authors=["J. Doe", "A. Other"],
            venue="ECIR",
            year=2021,
        ),
    ]
}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.delenv(llm.URL_ENV, raising=False)
    monkeypatch.delenv(llm.MODEL_ENV, raising=False)
    llm.save_settings(
        llm.OllamaSettings(enabled=True, model="qwen3", tasks={"reflist": "gemma3:4b"})
    )
    yield
    llm.save_settings(llm.OllamaSettings())


def _answer(content) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"message": {"content": text}})


def test_model(model) -> None:
    assert reflist_read.model() == "gemma3:4b" and not llm.thinks("reflist")
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    assert reflist_read.model() == "qwen3"  # (the general one)
    llm.save_settings(llm.OllamaSettings(enabled=False, model="qwen3"))
    assert reflist_read.model() == ""


def test_parts() -> None:
    """A long list in parts, in its order, every line once, cut before an item."""
    lines = []
    for n in range(1, 41):
        lines += [f"{n}- A made-up title number {n} about things", "   A. Author. Venue, 2020."]
    text = "\n".join(lines)
    found = parts(text, size=500)
    assert len(found) > 1 and all(len(p) <= 500 for p in found)
    assert all(reflist.numbered(p.split("\n")[0]) for p in found)  # (cut before an item)
    assert [x.strip() for x in "\n".join(found).split("\n")] == [x.strip() for x in lines]
    # Without numbers: cut before a paragraph; a single line longer than a part: whole.
    plain = "\n\n".join(f"Paper {n}\ncontinued" for n in range(30))
    found = parts(plain, size=100)
    assert all(p.startswith("Paper ") for p in found)
    assert parts("x" * 300, size=100) == ["x" * 300] and parts("  \n") == []


def test_items_of() -> None:
    sent = PASTED
    answer = {
        "references": [
            _ref(
                number=3,
                title=" Deep ranking for  serach (with a typo) ",
                authors=["J. Doe"],
                venue="Neural Computation",
                year=2021,
                doi="10.1162/NECO.2021.1",
            ),
            _ref(number=1, title="Learning…", url="https://hal.science/hal-03700001v2"),
            _ref(title="   "),  # (no title: left out)
            _ref(title="A made-up one", doi="10.9999/made.up", year=3021),
        ]
    }
    items = reflist_read.items_of(answer, sent)
    assert [(i.number, i.title) for i in items] == [
        (3, "Deep ranking for serach (with a typo)"),
        (1, "Learning…"),
        (None, "A made-up one"),
    ]
    assert items[0].doi == "10.1162/neco.2021.1" and items[0].years == {2021}
    assert items[0].authors == ["J. Doe"] and items[0].venue == "Neural Computation"
    assert items[0].text == (
        "Deep ranking for serach (with a typo). J. Doe. Neural Computation, 2021. "
        "doi: 10.1162/neco.2021.1"
    )
    assert (
        items[0].words == "Deep ranking for serach (with a typo). J. Doe. Neural Computation, 2021"
    )
    assert items[1].hal == "hal-03700001"
    assert items[2].doi is None and items[2].years == set()  # (not in the list; not a year)
    assert reflist_read.items_of({"refs": []}, sent) is None
    assert reflist_read.items_of({"references": [{"title": 3}]}, sent) is None


def test_structured() -> None:
    item = reflist_read.structured("A title", "A. One, B. Two", "Venue", 2020, "10.1234/x", 4)
    assert (item.number, item.title, item.authors, item.doi) == (
        4,
        "A title",
        ["A. One", "B. Two"],
        "10.1234/x",
    )
    item = reflist_read.structured("A title", ref="hal-01234567")
    assert item.hal == "hal-01234567" and item.doi is None
    assert reflist_read.structured("  ") is None


async def test_match_by_title() -> None:
    """An item read: matched by its title (not the words around it), its years."""
    pid = _person()
    rows = await pubview.load_stats(pid)
    by_title = {r.title: r.id for r in rows}
    items = reflist_read.items_of(READ, MESSY)
    matches = reflist.match(items, rows)
    assert [m.best.pub_id for m in matches] == [
        by_title["Learning to Rank with Sparse Representations of Documents"],
        by_title["Neural retrieval models for long documents"],
    ]
    assert not any(c.by_id for m in matches for c in m.candidates)  # (no DOI made up)
    # Split by its lines, the second item is lost in the first.
    assert len(reflist.split_items(MESSY)) == 1


def test_read(model) -> None:
    """Part after part, in order; one without a valid answer split as before."""
    text = PASTED
    two = parts(text, size=260)
    assert len(two) == 2 and two[1].startswith("2. Neural")
    first = {"references": READ["references"][:1]}
    second = {
        "references": [
            READ["references"][1],
            _ref(number=3, title="Deep ranking for search", year=2021),
        ]
    }
    seen: list[tuple[int, int]] = []
    with respx.mock:
        route = respx.post(CHAT).mock(side_effect=[_answer(first), _answer(second)])
        reading = reflist_read.Reading(two)
        got = asyncio.run(reflist_read.read(text, out=reading, progress=lambda *a: seen.append(a)))
        sent = [json.loads(c.request.content) for c in route.calls]
    assert [s["model"] for s in sent] == ["gemma3:4b"] * 2
    assert sent[0]["format"] == reflist_read.SCHEMA and not sent[0]["think"]
    assert "1- Learning to rank" in sent[0]["messages"][0]["content"]
    assert "[3] Deep ranking" in sent[1]["messages"][0]["content"]
    assert got is reading and (got.done, got.failed) == (2, [])
    assert [i.number for i in got.items] == [1, 2, 3]
    assert seen == [(0, 2), (1, 2), (2, 2)]
    # A part without a valid answer: split by its lines.
    with respx.mock:
        respx.post(CHAT).mock(side_effect=[_answer(first), _answer("not json")])
        got = asyncio.run(reflist_read.read(text, out=reflist_read.Reading(two)))
    assert got.failed == [1] and got.error
    assert [i.number for i in got.items] == [1, 2, 3, 4] and got.items[1].title is None
    # Stopped after the first part: the rest split by its lines.
    stopped = reflist_read.Reading(two, items=reflist_read.items_of(first, two[0]), done=1)
    assert [i.number for i in stopped.items + stopped.rest()] == [1, 2, 3, 4]
    # The server unreachable: raised.
    with respx.mock:
        respx.post(CHAT).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(httpx.ConnectError):
            asyncio.run(reflist_read.read(text))
    llm.save_settings(llm.OllamaSettings(enabled=False))
    with pytest.raises(RuntimeError):
        asyncio.run(reflist_read.read(text))


def test_edited_instructions() -> None:
    reflist_read.save_prompt("Read: {text}")
    try:
        assert reflist_read.messages(" 1- A ")[0]["content"] == "Read: 1- A"
        reflist_read.save_prompt('Read {list} as {"json": 1}')
        content = reflist_read.messages("1- A")[0]["content"]
        assert content.startswith("You read a list") and "<list>1- A</list>" in content
    finally:
        reflist_read.save_prompt("")
    assert reflist_read.prompt() == reflist_read.PROMPT


def test_reference_schema() -> None:
    assert set(reflist_read.SCHEMA["$defs"]["Reference"]["required"]) == set(Reference.model_fields)


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_settings_page(user: User, model) -> None:
    await user.open("/settings?tab=reflist")
    await user.should_see(marker="llm-task-reflist")
    await user.should_not_see(marker="llm-off-link")
    select = user.find(marker="llm-task-reflist").elements.pop()
    assert select.value == "gemma3:4b"
    select.value = ""
    user.find(marker="llm-task-reflist-think").elements.pop().value = "on"
    user.find(marker="reflist-prompt").elements.pop().value = "Mine: {text}"
    user.find(marker="reflist-save-settings").click()
    await user.should_see("Saved")
    st = llm.stored_settings()
    assert "reflist" not in st.tasks and st.task_think == {"reflist": True}
    assert reflist_read.prompt() == "Mine: {text}"
    user.find(marker="reflist-prompt-default").click()
    assert user.find(marker="reflist-prompt").elements.pop().value == reflist_read.PROMPT
    reflist_read.save_prompt("")
    llm.save_settings(llm.OllamaSettings(enabled=False))
    await user.open("/settings?tab=reflist")
    await user.should_see(marker="llm-off-link")


async def _open_dialog(user: User, pid: int) -> None:
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find("tag-from-list").click()
    await user.should_see(marker="reflist-text")


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_dialog_llm_off(user: User) -> None:
    """The LLM off: the button greyed, said, with where to turn it on."""
    pid = _person()
    await _open_dialog(user, pid)
    assert not user.find(marker="reflist-llm").elements.pop().enabled
    await user.should_see("No reading by the local AI: the local LLM is off")
    (link,) = user.find(marker="reflist-llm-settings").elements
    assert link.props["href"] == "/settings?tab=llm"


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_dialog_read(user: User, model, monkeypatch) -> None:
    """Read with the LLM: the references shown (title, then authors, venue, year), matched;
    one edited and matched again."""
    sent = []

    async def chat_json(messages, schema, folder_id=None, task=None, progress=None, **kw):
        assert task == "reflist" and schema == reflist_read.SCHEMA
        sent.append(messages[0]["content"])
        if progress:
            progress("writing", 10)
        return READ

    monkeypatch.setattr(llm, "chat_json", chat_json)
    pid = _person()
    await _open_dialog(user, pid)
    await user.should_not_see(marker="reflist-llm-off")
    user.find(marker="reflist-text").elements.pop().value = MESSY
    user.find(marker="reflist-llm").click()
    await user.should_see("2 of 2 items matched")
    await user.should_see("Read by the local LLM (gemma3:4b)")
    await user.should_see("1. Learning to rank with sparse representations of documents")
    await user.should_see("J. Doe, B. Smith · SIGIR · 2022")
    await user.should_not_see(marker="reflist-reading")
    assert len(sent) == 1 and "represen-\ntations" in sent[0]
    # An item's parts edited: matched again by its new title.
    user.find(marker="reflist-edit-1").click()
    await user.should_see(marker="reflist-item-title-1")
    user.find(marker="reflist-item-title-1").elements.pop().value = "Neural retrieval models"
    user.find(marker="reflist-rematch-1").click()
    await user.should_not_see(marker="reflist-item-title-1")
    await user.should_see("2. Neural retrieval models")
    rows = await pubview.load_stats(pid)
    short = next(r.id for r in rows if r.title == "Neural retrieval models")
    assert user.find(marker="reflist-choice-1").elements.pop().value == short
    # Find the papers: split by the lines again.
    user.find(marker="reflist-find").click()
    await user.should_see("1 of 1 items matched")
    await user.should_not_see(marker="reflist-read")


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_dialog_read_fails(user: User, model, monkeypatch) -> None:
    async def chat_json(*args, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    pid = _person()
    await _open_dialog(user, pid)
    user.find(marker="reflist-text").elements.pop().value = MESSY
    user.find(marker="reflist-llm").click()
    await user.should_see("The LLM could not read the list: down")
    assert user.find(marker="reflist-llm").elements.pop().enabled


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_dialog_stop(user: User, model, monkeypatch) -> None:
    """Stopped while reading its second part: the first read, the rest split by its lines."""
    first = {"references": READ["references"][:1]}
    calls = []

    async def chat_json(messages, schema, folder_id=None, task=None, progress=None, **kw):
        calls.append(messages)
        if len(calls) > 1:
            await asyncio.Event().wait()  # (never answers)
        return first

    monkeypatch.setattr(llm, "chat_json", chat_json)
    monkeypatch.setattr(reflist_read, "PIECE", 260)
    pid = _person()
    await _open_dialog(user, pid)
    user.find(marker="reflist-text").elements.pop().value = PASTED
    user.find(marker="reflist-llm").click()
    await user.should_see(marker="reflist-stop")
    await user.should_see("part 2 of 2")
    assert not user.find(marker="reflist-llm").elements.pop().enabled
    user.find(marker="reflist-stop").click()
    await user.should_see("3 of 4 items matched")
    await user.should_see("stopped: 1 part split by its lines")
    await user.should_see("1. Learning to rank with sparse representations of documents")
    await user.should_not_see(marker="reflist-reading")
    assert user.find(marker="reflist-llm").elements.pop().enabled
