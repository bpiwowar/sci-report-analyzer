"""The years of an excerpt, found in its text (categories.detect_years / split_years)."""

import pytest

from sci_report_analyzer.categories import detect_years, split_years


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
    ],
)
def test_split_years(text, years, rest):
    assert split_years(text) == (years, rest)
