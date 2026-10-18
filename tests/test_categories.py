"""The years of an excerpt, found in its text or around it in its PDF (categories.detect_years,
split_years, context_years; documents.selection_context)."""

import pytest

from sci_report_analyzer.categories import context_years, detect_years, split_years
from sci_report_analyzer.documents import marked_context, selection_context, selection_text


@pytest.mark.parametrize(
    "text, years",
    [
        ("Led the project ... 2026-2032 ....", (2026, 2032)),
        ("Funded ... 2026-32: renewed", (2026, 2032)),
        ("A grant (1998-03)", (1998, 2003)),  # (the century carried over)
        ("2026–2032", (2026, 2032)),
        ("2026 - 2032", (2026, 2032)),
        ("2026/2027", (2026, 2027)),
        ("from 2026 to 2032", (2026, 2032)),
        ("de 2026 à 2032", (2026, 2032)),
        ("A prize in 2026.", (2026, 2026)),
        ("In 2019 a prize, then 2023 another", (2019, 2023)),  # (their span)
        ("2026–2032, after 2010", (2026, 2032)),  # (the first range)
        ("2026-05-12", (2026, 2026)),  # (a date: its year)
        ("2026 - 32 pages", (2026, 2026)),  # (a short end only right after the dash)
        ("No year here", None),
        ("pp. 2026-2032", None),
        ("doi:10.1145/2026.1234", None),
        ("ISBN 978-2026-1234", None),
        ("120260 and 3.2026 and 2,2026", None),
        ("In 1850", None),
        # Open: its end none.
        ("Leads a team (2024-)", (2024, None)),
        ("Leads a team, 2024-...", (2024, None)),
        ("2024–… : head of", (2024, None)),
        ("Since 2024 - present.", (2024, None)),
        ("de 2024 à aujourd'hui", (2024, 2024)),  # (not after a dash: its year)
        ("2024-05 and on", (2024, 2024)),  # (a month: its year)
        ("pp. 2024-", None),
    ],
)
def test_detect_years(text, years):
    assert detect_years(text) == years


@pytest.mark.parametrize(
    "text, years, rest",
    [
        ("2016-32: Led a project", (2016, 2032), "Led a project"),
        ("  2016 – 2032 : Led a project", (2016, 2032), "Led a project"),
        ("De 2026 à 2032 : Projet", (2026, 2032), "Projet"),
        ("Led a project: 2016-32", (2016, 2032), "Led a project"),
        ("Led a project: 2016-32.", (2016, 2032), "Led a project."),
        ("Led a project — 2020", (2020, 2020), "Led a project"),
        ("Led a project (2016–2020).", (2016, 2020), "Led a project."),
        # Elsewhere: kept in the text.
        ("Published in 2016.", (2016, 2016), "Published in 2016."),
        ("In 2016: a prize", (2016, 2016), "In 2016: a prize"),
        ("Led a project 2016-2032.", (2016, 2032), "Led a project 2016-2032."),
        ("2016-32:", (2016, 2032), "2016-32:"),  # (nothing else)
        ("No year", None, "No year"),
        ("Leads a team (2024-)", (2024, None), "Leads a team"),
        ("Leads a team: 2024-...", (2024, None), "Leads a team"),
        ("2024 – present: Leads a team", (2024, None), "Leads a team"),
    ],
)
def test_split_years(text, years, rest):
    assert split_years(text) == (years, rest)


@pytest.mark.parametrize(
    "text, before, after, years",
    [
        # In its sentence: before it, then after it.
        ("XXX", "Entre 2019 et 2021, j'ai été ", " et YYY.", (2019, 2021)),
        ("YYY", "Entre 2019 et 2021, j'ai été XXX et ", ".", (2019, 2021)),
        ("XXX", "Between 2019 and 2021, I was ", " and YYY.", (2019, 2021)),
        ("chair", "I was ", " from 2018 to 2020. In 2023, a prize.", (2018, 2020)),
        # The nearest date: the last before it, the first after it.
        ("XXX", "From 2010 to 2012 I was A, then from 2019 to 2021 I was ", "", (2019, 2021)),
        ("XXX", "I was ", " (2015-17), then YYY (2019-21).", (2015, 2017)),
        # Not past its sentence (another's date), but in its line, then in lines over it.
        ("XXX", "A prize in 2015. Then I was ", ".", (2015, 2015)),
        ("member of the jury", "2016–2020: Head of the group\n• ", "", (2016, 2020)),
        (
            "Organised the workshop",
            "Research projects (2020-2024)\nLed a team of six\n",
            " on graph mining\n",
            (2020, 2024),
        ),
        # Its own years win.
        ("XXX in 2022", "Entre 2019 et 2021, j'ai été ", ".", (2022, 2022)),
        # None.
        ("XXX", "I was ", " and YYY.", None),
        ("XXX", "pp. 2019-2021. I was ", ".", None),
        ("XXX", "", "", None),
    ],
)
def test_context_years(text, before, after, years):
    assert context_years(text, before, after) == years


def test_context_years_bounded():
    before = "In 2012 a prize.\n" + "Some text\n" * 6 + "I was "
    assert context_years("XXX", before, ".") is None
    assert context_years("XXX", before, ".", lines=7) == (2012, 2012)


def _line(p: int, y: float, text: str, x: float = 50.0) -> dict:
    """A line of a PDF (see documents.set_lines): 5 units a character, 10 high."""
    end = x + 5 * len(text)
    return {"p": p, "t": text, "r": [x, y, end, y + 10], "c": [[0, x, end]]}


def _rect(line: dict, a: int, b: int) -> list[float]:
    """The rectangle of characters a to b of a line, with its page."""
    x = line["r"][0]
    return [x + 5 * a, line["r"][1], x + 5 * b, line["r"][3], line["p"]]


def test_selection_context():
    one = _line(1, 700, "Entre 2019 et 2021, j'ai été XXX et YYY.")
    lines = [_line(1, 760, "Old paragraph of 2010."), _line(1, 715, "Responsabilités"), one]
    lines.append(_line(1, 688, "suite du paragraphe"))
    at = one["t"].index("XXX")
    before, after = selection_context(lines, [_rect(one, at, at + 3)])
    assert before == "Responsabilités\nEntre 2019 et 2021, j'ai été "
    assert after == " et YYY.\nsuite du paragraphe"
    assert context_years("XXX", before, after) == (2019, 2021)
    # The first part and the last: what is before the one, after the other.
    at = one["t"].index("YYY")
    before, after = selection_context(lines, [_rect(one, 0, 5), _rect(one, at, at + 3)])
    assert before.endswith("Responsabilités\n") and after.startswith(".")


def test_selection_context_heading_and_distant():
    head = _line(1, 720, "2016 – 2020 : Head of the group")
    item = _line(1, 703, "Member of the jury of the prize")
    far = _line(1, 640, "Committees")
    other = _line(1, 610, "Reviewer for a journal")
    lines = [_line(1, 790, "Since 2010: lecturer"), head, item, far, other]
    before, after = selection_context(lines, [_rect(item, 0, 6)])
    assert context_years("Member", before, after) == (2016, 2020)
    # A heading without a date, a distant block over it with one: not looked in.
    before, after = selection_context(lines, [_rect(other, 0, 8)])
    assert "2016" not in before and "2010" not in before
    assert context_years("Reviewer", before, after) is None


def test_selection_context_beside():
    """A date in a column beside the line (on its row)."""
    date = _line(2, 500, "2019-2021", x=40)
    text = _line(2, 500, "Head of the doctoral school", x=150)
    before, after = selection_context([date, text], [_rect(text, 0, 4)])
    assert context_years("Head", before, after) == (2019, 2021)
    assert selection_context([date, text], [[0, 0, 1, 1, 3]]) == ("", "")


def test_selection_text():
    """A selection's text read at its place (e.g. to reword an excerpt as in its PDF)."""
    one = _line(1, 700, "Entre 2019 et 2021, j'ai été XXX et YYY,")
    two = _line(1, 688, "trésorier du ZZZ.")
    at = one["t"].index("XXX")
    assert selection_text([one, two], [_rect(one, at, at + 3)]) == "XXX"
    assert selection_text([one, two], [_rect(one, at, len(one["t"])), _rect(two, 0, 9)]) == (
        "XXX et YYY,\ntrésorier"
    )
    assert selection_text([one, two], [[0, 0, 1, 1, 3]]) == ""
    assert selection_text([], [_rect(one, 0, 3)]) == ""


def test_marked_context():
    """Selections marked in the text around them: those next to each other in one text,
    distant ones (another block) apart, in the order of the PDF."""
    lines = [
        _line(1, 760, "Responsabilités"),
        _line(1, 745, "XXXX YYY et encore"),
        _line(1, 733, "ZZZ WWW UUUU fin"),
        _line(1, 721, "suite"),
        _line(1, 500, "Loin : AAA BBB"),
        _line(1, 488, "après"),
    ]
    far, one, two = _rect(lines[4], 7, 10), _rect(lines[1], 5, 8), _rect(lines[2], 8, 12)
    assert marked_context(lines, [[far], [two], [one]]) == [
        "Responsabilités\nXXXX <selected>YYY</selected> et encore\n"
        "ZZZ WWW <selected>UUUU</selected> fin\nsuite",
        "Loin : <selected>AAA</selected> BBB\naprès",
    ]
    # (side by side: one after the other; at most ``keep`` lines around)
    both = [[_rect(lines[1], 0, 4)], [_rect(lines[1], 4, 8)]]
    assert marked_context(lines, both, keep=0) == [
        "<selected>XXXX</selected><selected> YYY</selected> et encore"
    ]
    assert marked_context(lines, [[[0, 0, 1, 1, 3]]]) == []
