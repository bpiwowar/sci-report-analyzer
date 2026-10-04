"""Text taken from a PDF, cleaned (pdftext): in Python, and the same in the browser."""

import json
import shutil
import subprocess

import pytest

from sci_report_analyzer.pdftext import SCRIPT, clean_pdf_text

SOFT = "­"
EM = "—"

# (the text taken from a PDF, cleaned, and cleaned as one paragraph)
CASES = [
    (  # (words hyphenated at a line end: mended; a compound in a line kept)
        "The findings suggest that long-term expo-\n"
        "sure has a measurable effect on the out-\n"
        "come in both groups.",
        "The findings suggest that long-term exposure has a measurable effect on the outcome"
        " in both groups.",
        None,
    ),
    (  # (accents a LaTeX font puts before their letter, the dotless i)
        "Une r´e´evaluation na¨ıve des ´ev´ene-\nments rares.",
        "Une réévaluation naïve des événements rares.",
        None,
    ),
    ("o`u sont-ils ? `a la page", "où sont-ils ? à la page", None),
    ("x^2 and ~a, a `code` span", "x^2 and ~a, a `code` span", None),
    (  # (a capital after the hyphen, or a digit before it: a compound, a range)
        "the solver uses the well known Navier-\nStokes equations for the flow field.",
        "the solver uses the well known Navier-Stokes equations for the flow field.",
        None,
    ),
    (
        "the measured interval was 10-\n20 units in total.",
        "the measured interval was 10-20 units in total.",
        None,
    ),
    (
        f"exposure to the compound over time was mea{SOFT}\nsured in both cohorts across.",
        "exposure to the compound over time was measured in both cohorts across.",
        None,
    ),
    (f"eco{SOFT}nomic", "economic", None),
    (
        f"THE REPORT OF THE INTER{SOFT}\nNATIONAL COMMITTEE WAS ADOPTED.",
        "THE REPORT OF THE INTERNATIONAL COMMITTEE WAS ADOPTED.",
        None,
    ),
    (
        "the committee reached its considera‐\ntion of the remaining items.",
        "the committee reached its consideration of the remaining items.",
        None,
    ),
    (
        "ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ eﬃcient",
        "ff fi fl ffi ffl st st efficient",
        None,
    ),
    ("zero​width and no-break", "zerowidth and no-break", None),
    (  # (justified text's space runs)
        "The results were  consistent across all of the\nsites  and the effect size remained.",
        "The results were consistent across all of the sites and the effect size remained.",
        None,
    ),
    (
        f"the meeting ran long and the outcome was unexpected{EM}\n"
        "and it changed the plan for the quarter ahead.",
        f"the meeting ran long and the outcome was unexpected{EM}and it changed the plan for"
        " the quarter ahead.",
        None,
    ),
    ("発" * 40 + "\n" + "見" * 10, "発" * 40 + "見" * 10, None),
    (  # (a web address cut at a line end: its break kept)
        "the report is available at https://example.org/research/\nmethods.pdf for download.",
        "the report is available at https://example.org/research/\nmethods.pdf for download.",
        "the report is available at https://example.org/research/\nmethods.pdf for download.",
    ),
    (  # (a short line ends a paragraph; a blank line too)
        "the first paragraph wraps onto a second\n"
        "line, then stops.\n"
        "the second paragraph also wraps onto a\n"
        "second line before its own final stop.\n\n\n"
        "A third one.",
        "the first paragraph wraps onto a second line, then stops.\n"
        "the second paragraph also wraps onto a second line before its own final stop.\n\n"
        "A third one.",
        "the first paragraph wraps onto a second line, then stops. the second paragraph also wraps onto a second line before its own final"
        " stop. A third one.",
    ),
    (  # (items of a list: each its own)
        "(a) participants received the active treatment\n"
        "for twelve weeks before the final assessment.\n"
        "(b) participants received placebo for the same\n"
        "period before the final assessment.\n"
        "• a point\n"
        "[12] A. Fictitious. A paper. 2020.",
        "(a) participants received the active treatment for twelve weeks before the final"
        " assessment.\n"
        "(b) participants received placebo for the same period before the final assessment.\n"
        "• a point\n"
        "[12] A. Fictitious. A paper. 2020.",
        None,
    ),
    (
        f"the summary of totals below re{SOFT}\n- item one",
        "the summary of totals below re\n- item one",
        "the summary of totals below re- item one",
    ),
    ("  Just a clean line.  \r\n", "Just a clean line.", None),
]


def _expected(case):
    _text, cleaned, one = case
    return [cleaned, one if one is not None else " ".join(cleaned.split("\n"))]


@pytest.mark.parametrize("case", CASES)
def test_clean(case):
    assert [clean_pdf_text(case[0]), clean_pdf_text(case[0], one_paragraph=True)] == _expected(case)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_same_in_the_browser():
    """The browser's cleaning (the clipboard) gives the same texts."""
    script = SCRIPT.strip().removeprefix("<script>").removesuffix("</script>")
    texts = [c[0] for c in CASES]
    out = subprocess.run(
        [
            "node",
            "-e",
            "const window = globalThis;" + script + "process.stdout.write(JSON.stringify("
            f"{json.dumps(texts)}.map(t => [vrCleanPdfText(t), vrCleanPdfText(t, true)])))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == [_expected(c) for c in CASES]
