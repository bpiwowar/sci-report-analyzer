"""Citations of several keys separated by commas, with or without brackets."""

import asyncio

import pytest
from helpers import add_source, make_person, pub

from sci_report_analyzer import folders, pubview, reports

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _context():
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Estimating the cost", 2023, "SIGIR", authors=["Jane Doe"]),
            pub("b", "The model of carbon", 2024, "ECIR", authors=["Ann Roe", "Jane Doe"]),
            pub("c", "Prospectively assessing", 2026, "CIKM", authors=["Ann Roe"]),
        ],
    )
    folders.add_person(folders.save_folder(None, "Hiring"), pid)
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    assert sorted(keys.values()) == ["doe2023estimating", "roe2024model", "roe2026prospectively"]
    return reports.note_context(stats, keys)


def test_comma_separated_keys():
    ctx = _context()
    keys = "@doe2023estimating,@roe2024model, @roe2026prospectively"
    r = reports.render(f"[{keys}]{{.year}}", ctx)
    assert r.text == "2023, 2024, 2026"
    assert r.cited == {"doe2023estimating": 1, "roe2024model": 1, "roe2026prospectively": 1}
    # Without the template: their numbers; with ";" too; a locator is not a key.
    assert reports.render(f"[{keys}]", ctx).text == "[1], [2], [3]"
    out = reports.render("[see @roe2024model, p. 3; @doe2023estimating,@roe2026prospectively]", ctx)
    assert out.text == "see [2], p. 3, [1], [3]"
    # Without brackets: the keys followed by the template.
    assert reports.render(f"Read {keys}{{.year}}.", ctx).text == "Read 2023, 2024, 2026."
    assert reports.render("@roe2024model{.short-venue}", ctx).text == "ECIR"
    # Without brackets nor template: each in-text (title, venue…), as before.
    out = reports.render("@doe2023estimating, @roe2024model", ctx).text
    assert out.startswith("[1] **Estimating the cost**") and "[2] **The model of carbon**" in out
    # An unknown key: the citation left as is, as for one key.
    r = reports.render("[@roe2024model, @nobody]{.year} @roe2024model,@nobody{.year}", ctx)
    assert r.text == "[@roe2024model, @nobody]{.year} @roe2024model,@nobody{.year}"
    assert r.unknown == ["nobody"]
    # A mail, code: kept.
    text = "x@roe2024model{.year} `@roe2024model{.year}`"
    assert reports.render(text, ctx).text == text
