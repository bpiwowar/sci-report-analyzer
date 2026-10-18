"""An excerpt's bold and italics, as inline Markdown (inline_md): carried over from the PDF
to the text selected, and its plain text."""

from sci_report_analyzer.inline_md import formatted_like, html, plain

MD = r"Membre du **comité de direction** de *X\_Y*, et ***très*** bien"


def test_formatted_like() -> None:
    """The words in bold or italics in the PDF's Markdown, in the text selected (its own
    words kept: those of the page may differ at its ends; punctuation out of the markers)."""
    assert formatted_like("membre du comité de direction de X_Y, et très bien", MD) == (
        r"membre du **comité de direction** de *X\_Y*, et ***très*** bien"
    )
    assert formatted_like("du comité de direction", MD) == "du **comité de direction**"
    assert formatted_like("Membre du conseil", "Membre du conseil") == "Membre du conseil"
    assert formatted_like("a_b <c>", None) == "a_b <c>"  # (none: as is, not escaped)
    assert formatted_like("rien de tel", MD) == "rien de tel"  # (no word matched)


def test_plain() -> None:
    """Its markers and escapes out; plain text as is (stars not around a word kept)."""
    assert plain(r"membre du **comité** de *X\_Y*, ***très*** \*bien\*") == (
        "membre du comité de X_Y, très *bien*"
    )
    assert plain("2 * 3 * 4, a_b") == "2 * 3 * 4, a_b"
    assert plain(formatted_like("membre du comité de direction de X_Y, et très bien", MD)) == (
        "membre du comité de direction de X_Y, et très bien"
    )


def test_html() -> None:
    assert html(r"a **b<c** *d\_e* ***f*** 2 * 3 \<x\> \*g\*") == (
        "a <b>b&lt;c</b> <i>d_e</i> <b><i>f</i></b> 2 * 3 &lt;x&gt; *g*"
    )
