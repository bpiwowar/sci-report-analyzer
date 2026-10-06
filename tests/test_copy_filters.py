from sci_report_analyzer import copy_filters
from sci_report_analyzer.copy_filters import marmotte

REPORT = """# Research

On **AZee** [1] and ==key== %% [CV, p. 2](/doc/1?page=2) %% work.
Next line #positive

- **one** item
    - sub-item
- two #question
- [ ] a task

> [!note] a callout
> within

## Details

| a | b |
|---|---|
| 1 | 2 |

#only-a-tag

# Influence

Text with `code #x`.
%% over
lines %%
End.
"""


def test_marmotte():
    html, plain = marmotte(REPORT)
    lines = html.splitlines()
    # "#": numbered banners, "##": bold lines.
    assert lines[0] == "<p><strong>--- 1. Research ---</strong><br>"
    assert "<p><strong>- Details</strong></p>" in lines
    assert "<p><strong>--- 2. Influence ---</strong><br>" in lines
    # The comments, tags, tasks, callouts, tagged lines out; highlights as spans.
    body = "\n".join(lines)
    for gone in ("CV, p. 2", "#positive", "a task", "callout", "two", "only-a-tag", "over"):
        assert gone not in body, gone
    assert '<span style="background-color:#FFF3A3">key</span>' in body
    assert "<code>code #x</code>" in body
    # Lists as paragraphs (nested: indented); tables left out.
    assert "<p>• <strong>one</strong> item</p>" in lines
    assert "<p>&nbsp;&nbsp;&nbsp;&nbsp;• sub-item</p>" in lines
    assert "tableau omis" in body and "<table" not in body
    assert plain.startswith("--- 1. Research ---\n\nOn AZee [1] and key  work.\nNext line")
    assert "\n\n\n" not in plain


def test_enabled_filters():
    assert copy_filters.enabled() == []
    copy_filters.set_enabled(["marmotte", "unknown"])
    assert copy_filters.enabled() == ["marmotte"]
