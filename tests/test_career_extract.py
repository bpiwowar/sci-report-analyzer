"""A person's career read by the LLM in their documents: the passages sent, the lines of the
CV suggested (checked against the text, without those known), the dialog to add them."""

import asyncio
import json

import httpx
import pytest
import respx
from helpers import PDF, make_person
from nicegui.testing import User

from sci_report_analyzer import career, career_extract, documents, folders, llm, persons

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _line(page: int, text: str) -> dict:
    return {"p": page, "t": text, "r": [72.0, 100.0, 300.0, 110.0]}


LINES = [
    _line(1, "Application for a professorship"),
    _line(1, "Research statement"),
    _line(2, "Our group studies ranking."),
    _line(2, "Unrelated filler"),
    _line(2, "In 2016, I was recruited as an associate professor at Quokka University."),
    _line(2, "More filler"),
    _line(2, "Even more filler"),
    _line(2, "And more"),
    _line(3, "Curriculum vitae"),
    _line(3, "Teaching: databases"),
    _line(4, "We published 12 papers in 2021."),
]


def test_passages() -> None:
    found = career_extract.passages("Application", LINES)
    # (the page headed as a CV first)
    assert [p.source for p in found] == ["Application, p. 3", "Application, p. 2"]
    found = found[::-1]
    # (the line with a year and a position, the lines around it; a CV's page whole)
    assert found[0].text.splitlines() == [
        "Our group studies ranking.",
        "Unrelated filler",
        "In 2016, I was recruited as an associate professor at Quokka University.",
        "More filler",
        "Even more filler",
    ]
    assert found[1].text == "Curriculum vitae\nTeaching: databases"


def test_pages_ranked_and_words() -> None:
    """Pages: those headed as a CV first, then by their lines with a year and a career word;
    the words editable (by language, * for any letters), without case nor accents."""
    from sci_report_analyzer import career_words

    lines = [
        _line(1, "In 2019, I was promoted to professor."),
        _line(2, "Recruté maître de conférences en 2012, à l'Université Wombat."),
        _line(2, "Habilitation à diriger des recherches (HDR) soutenue en 2018."),
        _line(3, "2. Parcours"),
        _line(3, "Some text."),
    ]
    found = career_extract.passages("Dossier", lines)
    assert [p.source for p in found] == ["Dossier, p. 3", "Dossier, p. 2", "Dossier, p. 1"]
    words = career_words.words()
    words["en"]["positions"] = ["promot*"]
    words["fr"] = {g: [] for g in career_words.GROUPS}
    words["en"]["headings"] = words["en"]["diplomas"] = []
    career_words.save_words(words)
    try:
        found = career_extract.passages("Dossier", lines)
        assert [p.source for p in found] == ["Dossier, p. 1"]
    finally:
        career_words.save_words(career_words.DEFAULTS)
    assert career_words.words() == career_words.DEFAULTS


def test_batches(monkeypatch) -> None:
    """The pages following each other in a document read together, up to PIECE characters;
    the others apart; a page too long, cut between lines."""
    monkeypatch.setattr(career_extract, "PIECE", 20)
    P = career_extract.Passage
    found = [
        P("a, p. 1", "x" * 8, 1, 1),
        P("a, p. 2", "x" * 8, 1, 2),
        P("a, p. 4", "x" * 8, 1, 4),  # (not following)
        P("b, p. 5", "x" * 8, 2, 5),  # (another document)
        P("b, p. 6", "\n".join(["y" * 9] * 3), 2, 6),  # (too long: cut)
    ]
    split = career_extract.batches(found)
    assert [[p.source for p in part] for part in split] == [
        ["a, p. 1", "a, p. 2"],
        ["a, p. 4"],
        ["b, p. 5"],  # (with the first piece of p. 6: too long)
        ["b, p. 6"],
        ["b, p. 6"],
    ]
    assert [p.text for p in split[3] + split[4]] == ["y" * 9 + "\n" + "y" * 9, "y" * 9]


def test_chat_json(monkeypatch) -> None:
    monkeypatch.delenv(llm.URL_ENV, raising=False)
    monkeypatch.delenv(llm.MODEL_ENV, raising=False)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    with respx.mock:
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": '{"entries": []}'}})
        )
        assert asyncio.run(llm.chat_json([{"role": "user", "content": "hi"}], {})) == {
            "entries": []
        }
        sent = json.loads(route.calls[0].request.content)
        assert (sent["model"], sent["stream"], sent["format"]) == ("qwen3", True, {})
        assert sent["think"] is False  # (a reasoning model: not thinking, by default)
        # A task's own model.
        llm.save_settings(
            llm.OllamaSettings(enabled=True, model="qwen3", tasks={"career": "gemma3:4b"})
        )
        asyncio.run(llm.chat_json([], {}, task="career"))
        assert json.loads(route.calls[1].request.content)["model"] == "gemma3:4b"
        assert llm.model() == "qwen3" and llm.model(task="career") == "gemma3:4b"
        # Thinking: off by default, and for reading a career even if on by default; a task's
        # own choice.
        llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3", think=True))
        assert llm.thinks() and not llm.thinks("career")
        llm.save_settings(
            llm.OllamaSettings(enabled=True, model="qwen3", task_think={"career": True})
        )
        asyncio.run(llm.chat_json([], {}, task="career"))
        assert json.loads(route.calls[2].request.content)["think"] is True
    # Streamed: the phases (thinking, writing) and the characters written.
    stream = "\n".join(
        json.dumps({"message": m})
        for m in [{"thinking": "Hmm"}, {"content": '{"entries"'}, {"content": ": []}"}]
    )
    phases = []
    with respx.mock:
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, text=stream)
        )
        answer = asyncio.run(llm.chat_json([], {}, progress=lambda p, n: phases.append((p, n))))
    assert answer == {"entries": []}
    assert phases == [("reading", 0), ("thinking", 3), ("writing", 10), ("writing", 15)]
    llm.save_settings(llm.OllamaSettings())


ANSWER = {
    "entries": [
        {
            "kind": "position",
            "title": "Associate professor",
            "institution": "Quokka University",
            "start": "2016",
            "end": "",
            "quote": "recruited as an associate professor at Quokka University",
        },
        {
            "kind": "diploma",
            "title": "PhD",
            "institution": "",
            "obtained": "June 2015",
            "thesis": "On wombats",
            "quote": "PhD in Wombat Studies",  # (not in the text)
        },
        {"kind": "position", "title": "Postdoc", "start": "2013", "end": "2014", "quote": ""},
        {"kind": "award", "title": "Best paper", "quote": ""},  # (not a kind of line)
    ]
}


@pytest.fixture
def answering(monkeypatch):
    sent = []

    async def chat_json(messages, schema, folder_id=None, task=None, progress=None):
        assert task == "career"
        sent.append(messages)
        return ANSWER

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return sent


def _person_with_document() -> tuple[int, int, int]:
    pid = make_person("Jane Doe")
    persons.update(
        pid, name="Jane Doe", affiliation=None, aliases=[], notes="Private opinion: too junior."
    )  # (the notes: never sent)
    period = folders.add_person(folders.save_folder(None, "Hiring 2026"), pid)
    doc = documents.add(period, "Application.pdf", PDF)
    documents.set_lines(doc, LINES)
    letters = documents.add(period, "Letters.pdf", PDF)  # (never opened: not read)
    return pid, doc, letters


def test_extract(answering) -> None:
    pid, _doc, letters = _person_with_document()
    assert [(d.name, d.read) for d in career_extract.person_documents(pid)] == [
        ("Application", True),
        ("Letters", False),
    ]
    career.save(pid, career.Entry("position", "Post-doc", "2013", "2014", "Inria"))
    career.save(pid, career.Entry("position", "postdoc", "2013-01", "2014"))
    found = asyncio.run(career_extract.extract(pid))
    assert "Jane Doe" in answering[0][0]["content"]
    assert "[Application, p. 2]" in answering[0][1]["content"]
    assert "[Application, p. 3]" in answering[0][1]["content"]  # (following: together)
    assert len(answering) == 1
    assert "too junior" not in json.dumps(answering)
    # (the postdoc known; the award not a line of a CV)
    assert [(s.entry.title, s.entry.start, s.entry.end) for s in found] == [
        ("Associate professor", "2016", None),
        ("PhD", None, "2015"),
    ]
    assert [(s.checked, s.source) for s in found] == [
        (True, "Application, p. 2"),
        (False, "Application, p. 2, Application, p. 3"),
    ]
    assert found[1].entry.elapsed  # (a diploma: the years since)
    assert found[1].entry.details == "On wombats"  # (a diploma's thesis)
    assert [p.page for p in found[0].pages] == [2]
    # Only the pages chosen: none (nothing sent).
    answering.clear()
    asyncio.run(career_extract.extract(pid, []))
    assert answering == []  # (nothing to read: no profile, no note, no page)
    assert [p.page for p in career_extract.pages(pid, [letters])] == []  # (not read)


async def test_extract_dialog(user: User, answering) -> None:
    pid, doc, letters = _person_with_document()
    await user.open(f"/person/{pid}?tab=career")
    await user.should_see(marker="career-extract")
    assert not user.find(marker="career-extract").elements.pop().enabled  # (no LLM: greyed)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    try:
        await user.open(f"/person/{pid}?tab=career")
        user.find(marker="career-extract").click()
        # 1. The documents: that never opened, not (to open first); their pages, those likely
        # about a career ticked, the others to tick.
        await user.should_see(marker=f"career-extract-doc-{doc}")
        assert user.find(marker=f"career-extract-doc-{doc}").elements.pop().value
        unread = user.find(marker=f"career-extract-doc-{letters}").elements.pop()
        assert not unread.value and "disable" in unread.props
        await user.should_see(marker=f"career-extract-open-{letters}")
        await user.should_see("2 pages ticked")

        def page(n: int):
            return user.find(marker=f"career-extract-page-{doc}-{n}").elements.pop()

        assert page(2).value and page(3).value
        # (only the pages ticked shown; all of them, to tick others)
        await user.should_not_see(marker=f"career-extract-page-{doc}-4")
        user.find(marker="career-extract-all-pages").elements.pop().value = True
        await user.should_see(marker=f"career-extract-page-{doc}-4")
        assert not page(4).value
        await user.should_see(marker=f"career-extract-pages-{doc}")
        box = user.find(marker=f"career-extract-doc-{doc}").elements.pop()
        box.value = False
        await user.should_see("0 pages ticked")
        box.value = True
        await user.should_see("2 pages ticked")
        page(3).value = False
        await user.should_see("1 page ticked")
        page(3).value = True
        # 2. The instructions (changed, saved), and what is sent.
        user.find(marker="career-extract-next").click()
        await user.should_see(marker="career-extract-sent-1-user")
        sent = user.find(marker="career-extract-sent-1-user").elements.pop().text
        assert "[Application, p. 2]" in sent and "[Application, p. 3]" in sent
        area = user.find(marker="career-extract-instructions").elements.pop()
        assert "{name}" in area.value
        area.value = "Read the CV of {name}."
        user.find(marker="career-extract-prompt-save").click()
        await user.should_see("Saved as the default instructions")
        assert career_extract.prompt() == "Read the CV of {name}."
        # 3. Read; 4. the lines found, each with its source.
        user.find(marker="career-extract-read").click()
        await user.should_see(marker="career-extract-add")
        assert answering[0][0]["content"] == "Read the CV of Jane Doe."
        await user.should_see("Quokka University")
        await user.should_see("Thesis: On wombats")
        await user.should_see(marker="career-extract-source-0")  # (its page, to open)
        quote = user.find(marker="career-extract-source-0-text").elements.pop()
        assert "<mark>recruited as an associate professor" in quote.content
        tick = user.find(marker="career-extract-tick-1").elements.pop()
        assert not tick.value  # (its words not in the documents: not ticked)
        user.find(marker="career-extract-add").click()
        await user.should_see("1 line added to the career")
        assert [e.title for e in career.entries(pid)] == ["Associate professor"]
    finally:
        llm.save_settings(llm.OllamaSettings())
        career_extract.save_prompt("")
    assert career_extract.prompt() == career_extract.PROMPT


async def test_extract_stopped(user: User, monkeypatch) -> None:
    """Reading stopped: the lines found so far (here, in the first part); the model named."""
    pid, doc, _letters = _person_with_document()
    calls = []

    async def chat_json(messages, schema, folder_id=None, task=None, progress=None):
        calls.append(messages)
        if len(calls) > 1:
            await asyncio.sleep(60)  # (the second part never answered)
        return ANSWER

    monkeypatch.setattr(llm, "chat_json", chat_json)
    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    try:
        await user.open(f"/person/{pid}?tab=career")
        user.find(marker="career-extract").click()
        await user.should_see("Model: qwen3 (local")
        # (pages 2 and 4, not following each other: two parts)
        user.find(marker="career-extract-all-pages").elements.pop().value = True
        await user.should_see(marker=f"career-extract-page-{doc}-4")
        user.find(marker=f"career-extract-page-{doc}-3").elements.pop().value = False
        user.find(marker=f"career-extract-page-{doc}-4").elements.pop().value = True
        user.find(marker="career-extract-next").click()
        await user.should_see(marker="career-extract-sent-2-user")
        user.find(marker="career-extract-read").click()
        await user.should_see("Part 2 of 2: the model reads the text")
        icons = [user.find(marker=f"career-extract-part-{i}").elements.pop() for i in (0, 1)]
        assert [i.props["name"] for i in icons] == ["check_circle", "pending"]
        await user.should_see("3 lines found so far")
        shown = user.find(marker="career-extract-current").elements.pop().text
        assert shown.startswith("[Application, p. 4]")  # (the part read now)
        user.find(marker="career-extract-stop").click()
        await user.should_see(marker="career-extract-stopped")
        await user.should_see("Quokka University")
        await user.should_see(marker="career-extract-add")
    finally:
        llm.save_settings(llm.OllamaSettings())


async def test_career_settings_page(user: User) -> None:
    """Settings → Reading a career: its model, its words (saved; the defaults back)."""
    from sci_report_analyzer import career_words

    llm.save_settings(llm.OllamaSettings(enabled=True, model="qwen3"))
    try:
        await user.open("/settings?tab=career")
        await user.should_see(marker="career-words-fr-headings")
        select = user.find(marker="llm-task-career").elements.pop()
        select.set_options(["gemma3:4b", "qwen3"], value="gemma3:4b")  # (as listed by Ollama)
        area = user.find(marker="career-words-fr-headings").elements.pop()
        area.value = "parcours\nmon cv"
        user.find(marker="llm-task-career-think").elements.pop().value = "on"
        user.find(marker="career-prompt").elements.pop().value = "Mine: {name}"
        user.find(marker="career-save-settings").click()
        await user.should_see("Saved")
        assert llm.stored_settings().tasks == {"career": "gemma3:4b"}
        assert llm.model() == "qwen3"  # (the default one unchanged)
        assert llm.stored_settings().task_think == {"career": True}
        assert career_words.words()["fr"]["headings"] == ["parcours", "mon cv"]
        assert career_extract.prompt() == "Mine: {name}"
        user.find(marker="career-words-reset").click()
        user.find(marker="career-prompt-default").click()
        user.find(marker="career-save-settings").click()
        await user.should_see("Saved")
        assert career_words.words() == career_words.DEFAULTS
        assert career_extract.prompt() == career_extract.PROMPT
    finally:
        career_extract.save_prompt("")
        llm.save_settings(llm.OllamaSettings())
        career_words.save_words(career_words.DEFAULTS)


def test_ticked(monkeypatch) -> None:
    """The pages ticked first: the most likely, within the limit (a page headed as a CV
    first)."""
    pid, doc, _letters = _person_with_document()
    found = career_extract.pages(pid)
    assert [(p.page, p.reason) for p in found] == [(1, ""), (2, "lines"), (3, "heading"), (4, "")]
    assert career_extract.ticked(found) == {(doc, 2), (doc, 3)}
    monkeypatch.setattr(career_extract, "BUDGET", 10)
    assert career_extract.ticked(found) == {(doc, 3)}
    assert career_extract.quoted("A b\n  C d", "b c") == (2, 7)


def test_affiliations() -> None:
    """Their validated profiles' affiliations, each once (whatever the case, accents and
    spaces) with its sources; not the person's own."""
    from sci_report_analyzer.db.models import SourceLink
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    persons.update(pid, name="Jane Doe", affiliation="Own Lab", aliases=[], notes="")
    with session_scope() as s:
        for source, aff, status in [
            ("hal", "Université  Quokka", "validated"),
            ("openalex", "universite quokka", "validated"),
            ("orcid", "Wombat Institute", "validated"),
            ("dblp", "Rejected Inc.", "rejected"),
        ]:
            s.add(
                SourceLink(
                    person_id=pid,
                    source=source,
                    external_id=source,
                    status=status,
                    evidence={"affiliation": aff},
                )
            )
    assert career_extract.affiliations(pid) == [
        "Université Quokka (from hal, openalex)",
        "Wombat Institute (from orcid)",
    ]
