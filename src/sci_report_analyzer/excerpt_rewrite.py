"""A new excerpt rewritten by the local LLM (see llm.rewrite_model): a neutral wording of
the text selected (impersonal, short, in its language, without its dates: « j'ai été membre
du comité de direction de X » → « Membre du comité de direction de X ») and its years, read
in it and in the lines around it in the PDF (« Entre 2019 et 2021, j'ai été XXX et YYY », XXX
selected: 2019–2021), as a structured output (a JSON schema). Proposed, never filed as is;
no LLM, or no valid answer: none (the years found around it instead: see
categories.context_years)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from . import llm

logger = logging.getLogger(__name__)

CONTEXT_LINES = 2  # lines around the selection sent with it
TIMEOUT = 60.0  # seconds


class Answer(BaseModel):
    """The LLM's answer (its schema: SCHEMA)."""

    text: str = Field(description="The neutral rewrite of the selected text only")
    start_year: int | None = Field(description="The year it started, if known")
    end_year: int | None = Field(description="The year it ended, if known and not ongoing")
    ongoing: bool = Field(description="Whether it is still going on")


SCHEMA = Answer.model_json_schema()

PROMPT = """You rewrite a passage selected in a researcher's document (a CV, an application, \
a report) as an entry of a list.

The passage selected:
<selected>{text}</selected>

The text around it in the document (for its dates only):
<before>{before}</before>
<after>{after}</after>

Answer in JSON:
- "text": the selected passage only (not what is around it), rewritten as a short, neutral, \
impersonal entry in the passage's own language: no first person ("I", "je", "j'ai été"…), \
no dates or years, its facts kept (names, roles, places), nothing added; e.g. \
"j'ai été membre du comité de direction de X" → "Membre du comité de direction de X", \
"I was the chair of the Y committee" → "Chair of the Y committee". The most important \
information first: what it is and its kind, then the details; e.g. a project: its type or \
funding first, "Projet ANR Quokka (coordinateur)" rather than "Coordinateur du projet \
Quokka financé par l'ANR", "ERC Starting Grant Wombat" rather than "Wombat, a project \
funded by an ERC Starting Grant".
- "start_year", "end_year": the years the passage is about, read in it, else in the text \
around it when it applies to the passage (e.g. "Between 2019 and 2021, I was A and B", \
A selected: 2019 and 2021); null if unknown. A single year: both the same.
- "ongoing": true if it is still going on ("since 2020", "depuis 2020"): "end_year" null then.
"""


def context_window(before: str, after: str, lines: int = CONTEXT_LINES) -> tuple[str, str]:
    """The text around a selection sent to the LLM (see documents.selection_context: its
    lines separated by "\\n"): its line, and ``lines`` lines before and after it."""
    return "\n".join(before.split("\n")[-lines - 1 :]), "\n".join(after.split("\n")[: lines + 1])


def prompt() -> str:
    """The instructions sent (those saved, else PROMPT); ``{text}``: the passage selected,
    ``{before}``, ``{after}``: the text around it."""
    return llm.prompt("rewrite", PROMPT)


def save_prompt(text: str) -> None:
    """(blank or the default one: following the default)"""
    llm.save_prompt("rewrite", PROMPT, text)


def messages(text: str, before: str, after: str) -> list[dict[str, str]]:
    before, after = context_window(before, after)
    values = {"text": text.strip(), "before": before.strip(), "after": after.strip()}
    try:
        content = prompt().format(**values)
    except (KeyError, IndexError, ValueError):  # (instructions edited: braces of their own)
        logger.warning("The rewriting instructions are not a valid template: the default ones")
        content = PROMPT.format(**values)
    return [{"role": "user", "content": content}]


def _year(y: int | None) -> int | None:
    return y if y is not None and 1900 <= y <= 2099 else None


class Rewrite(BaseModel):
    """A rewrite proposed: its text and years (an end none: ongoing or unknown)."""

    text: str
    start: int | None = None
    end: int | None = None


def proposal(answer: dict) -> Rewrite | None:
    """The rewrite in the LLM's answer (none if not valid: not following SCHEMA, no text)."""
    try:
        a = Answer.model_validate(answer)
    except ValidationError as e:
        logger.info("Invalid rewrite: %s", e)
        return None
    text = " ".join(a.text.split())
    if not text:
        return None
    start, end = _year(a.start_year), None if a.ongoing else _year(a.end_year)
    if start and end and end < start:
        start, end = end, start
    return Rewrite(text=text, start=start, end=end)


async def rewrite(
    text: str, before: str = "", after: str = "", timeout: float = TIMEOUT
) -> Rewrite | None:
    """The rewrite of a passage selected (``before`` and ``after`` it: the text around it,
    see documents.selection_context) by the local LLM; none if no model (llm.rewrite_model),
    or no valid answer (the server unreachable, too slow, an invalid one)."""
    name = llm.rewrite_model()
    if not name or not text.strip():
        return None
    return (await trial(text, before, after, name, timeout=timeout)).rewrite


@dataclass
class Trial:
    """A passage rewritten by a model (see trial): its rewrite (none: no valid answer; the
    ``error`` raised, if any) and how long it took (seconds)."""

    model: str
    rewrite: Rewrite | None
    seconds: float
    error: str = ""


async def trial(text: str, before: str, after: str, name: str, timeout: float = TIMEOUT) -> Trial:
    """The rewrite of a passage selected by the model ``name`` (e.g. to compare models: see
    ui.viewer_side.change_llm_dialog), timed; any error caught (said in it)."""
    start = time.monotonic()
    try:
        answer = await llm.chat_json(
            messages(text, before, after), SCHEMA, task="rewrite", timeout=timeout, name=name
        )
    except Exception as e:  # (no valid answer: the deterministic years instead)
        logger.info("No rewrite: %s", e)
        return Trial(name, None, time.monotonic() - start, str(e) or type(e).__name__)
    r = proposal(answer) if isinstance(answer, dict) else None
    return Trial(name, r, time.monotonic() - start)
