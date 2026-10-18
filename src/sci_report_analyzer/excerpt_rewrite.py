"""A new excerpt rewritten by the local LLM (see llm.rewrite_model): a neutral wording of
the text selected (impersonal, short, in its language, without its dates: « j'ai été membre
du comité de direction de X » → « Membre du comité de direction de X »), the details it
leaves out (its notes) and its years, read
in it and in the lines around it in the PDF (« Entre 2019 et 2021, j'ai été XXX et YYY », XXX
selected: 2019–2021), whether it shows the person's influence (« rayonnement »), is a one-off
event or sometime within their period (no date given), and how many days a year it takes, as
a structured output (a JSON schema). Proposed, never filed as is;
no LLM, or no valid answer: none (the years found around it instead: see
categories.context_years)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from . import categories, llm

logger = logging.getLogger(__name__)

CONTEXT_LINES = 2  # lines around the selection sent with it
TIMEOUT = 60.0  # seconds


class Answer(BaseModel):
    """The LLM's answer (its schema: SCHEMA)."""

    text: str = Field(description="The short, neutral rewrite of the selected text only")
    notes: str = Field("", description="Its details left out of the short text, if any")
    start_year: int | None = Field(description="The year it started, if known")
    end_year: int | None = Field(description="The year it ended, if known and not ongoing")
    ongoing: bool = Field(description="Whether it is still going on")
    influence: bool = Field(
        False, description="Whether it shows the person's influence (international visibility…)"
    )
    punctual: bool = Field(False, description="Whether it is a one-off event (no duration)")
    in_period: bool = Field(
        False, description="Whether no date is given: sometime within the person's period"
    )
    days: float | None = Field(None, description="Days a year it takes, if it can be estimated")

    @field_validator("notes", "influence", "punctual", "in_period", "days", mode="wrap")
    @classmethod
    def _lenient(cls, v: Any, handler: ValidatorFunctionWrapHandler, info) -> Any:
        """(not valid: as if not given, the rest of the answer kept)"""
        try:
            return handler(v)
        except ValidationError:
            return cls.model_fields[info.field_name].default


SCHEMA = Answer.model_json_schema()
SCHEMA["required"] = list(SCHEMA["properties"])  # (all asked for; missing ones: their default)

PROMPT = """You rewrite a passage selected in a researcher's document (a CV, an application, \
a report) as an entry of a list.

The passage selected:
<selected>{text}</selected>

The text around it in the document (for its dates only):
<before>{before}</before>
<after>{after}</after>

Answer in JSON:
- "text": the selected passage only (not what is around it), rewritten as a short, neutral, \
impersonal entry in the passage's own language, a line of a list (a few words, at most one \
short sentence): no first person ("I", "je", "j'ai été"…), no dates or years, its main facts \
(what it is, the role, the name of the project, committee or institution), nothing added; \
e.g. "j'ai été membre du comité de direction de X" → "Membre du comité de direction de X", \
"I was the chair of the Y committee" → "Chair of the Y committee". The most important \
information first: what it is and its kind; e.g. a project: its type or funding first, \
"Projet ANR Quokka (coordinateur)" rather than "Coordinateur du projet Quokka financé par \
l'ANR", "ERC Starting Grant Wombat" rather than "Wombat, a project funded by an ERC \
Starting Grant".
- "notes": the details of the selected passage left out of "text" (its topic, partners, \
amounts, numbers, tasks…), in the same language and neutral style, briefly; nothing that is \
not in the passage, no dates or years; "" if "text" already says it all.
- "start_year", "end_year": the years the passage is about, read in it, else in the text \
around it when it applies to the passage (e.g. "Between 2019 and 2021, I was A and B", \
A selected: 2019 and 2021); null if unknown. A single year: both the same.
- "ongoing": true if it is still going on ("since 2020", "depuis 2020"): "end_year" null then.
- "influence": true if it shows the person's influence or visibility beyond their own lab \
("rayonnement"): invited talks, keynotes, prizes, program or steering committees, editorial \
boards, expertise for agencies, international collaborations…; else false.
- "punctual": true if it is a one-off event, a moment with no duration (a talk, a prize, a \
defence, an event organised on given days); else false.
- "in_period": true if no date or year is given for it (in the passage or around it): it \
happened sometime within the period of the document; else false. Not both "punctual" and \
"in_period".
- "days": an estimate of how many days a year it takes (e.g. 0.5 for a talk, 3 for a jury, \
10 for a committee, 180 for half a job); null if it cannot be estimated.
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
    """A rewrite proposed: its text and years (an end none: ongoing or unknown), whether it
    shows the person's influence, is a one-off event or within their period (not both), and
    its days a year (none: not estimated; see categories.clean_days)."""

    text: str
    notes: str = ""  # (its details: see Answer)
    start: int | None = None
    end: int | None = None
    influence: bool = False
    punctual: bool = False
    in_period: bool = False
    days: float | None = None


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
    return Rewrite(
        text=text,
        notes=" ".join(a.notes.split()),
        start=start,
        end=end,
        influence=a.influence,
        punctual=a.punctual,
        in_period=a.in_period and not a.punctual,
        days=categories.clean_days(a.days),
    )


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
    ``error`` raised, if any) and how long it took (seconds; of the first run if ``cached``).
    ``think``: whether the model thought (false if asked but it cannot: ``no_think``);
    ``warmed``: the model was loaded first (not in the time)."""

    model: str
    rewrite: Rewrite | None
    seconds: float
    error: str = ""
    cached: bool = False
    think: bool = False
    no_think: bool = False
    warmed: bool = False


# The trials already run, by the hash of what changes the answer (see trial_key).
_trials: dict[str, Trial] = {}


def trial_key(text: str, before: str, after: str, name: str, think: bool) -> str:
    """What an answer depends on, hashed: the model (and where it runs), the messages sent,
    the schema and whether it thinks."""
    st = llm.settings()
    inputs = [st.chat_api, llm.chat_server(st), name, think, SCHEMA]
    inputs.append(messages(text, before, after))
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


async def trial(
    text: str,
    before: str,
    after: str,
    name: str,
    timeout: float = TIMEOUT,
    think: bool | None = None,
    fresh: bool = False,
    warming: Callable[[], None] | None = None,
) -> Trial:
    """The rewrite of a passage selected by the model ``name`` (e.g. to compare models: see
    ui.viewer_side.change_llm_dialog), timed; any error caught (said in it). ``think``: the
    model thinks (none: its task's setting); if it cannot, it does not (said: ``no_think``).
    The answer is kept (same model, passage, instructions and thinking): shown again, with its
    time, without asking the model, unless ``fresh``. The model is warmed up first, once
    (``warming`` is called then), not timed."""
    think = llm.thinks("rewrite") if think is None else think
    no_think = False
    if think and await llm.can_think(name) is False:
        think, no_think = False, True
    key = trial_key(text, before, after, name, think)
    if not fresh and (kept := _trials.get(key)) is not None:
        return replace(kept, cached=True, no_think=no_think, warmed=False)
    if warming and not llm.is_warm(name):
        warming()
    warmed = await llm.warm_up(name, timeout)
    start = time.monotonic()
    try:
        answer = await llm.chat_json(
            messages(text, before, after),
            SCHEMA,
            task="rewrite",
            timeout=timeout,
            name=name,
            think=think,
        )
    except Exception as e:  # (no valid answer: the deterministic years instead)
        logger.info("No rewrite: %s", e)
        return Trial(
            name, None, time.monotonic() - start, str(e) or type(e).__name__, False, think, no_think
        )
    r = proposal(answer) if isinstance(answer, dict) else None
    t = Trial(name, r, time.monotonic() - start, "", False, think, no_think, warmed)
    if r is not None:
        _trials[key] = t
    return t
