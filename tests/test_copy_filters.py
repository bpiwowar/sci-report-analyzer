import pytest
from nicegui.testing import User

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


def test_strip_comments():
    text = "A %% one %% B %% over\nlines %% C\n```\nkept %% in code %%\n```\nD %% two %%"
    assert copy_filters.strip_comments(text) == "A  B  C\n```\nkept %% in code %%\n```\nD "


def test_tag_rules():
    """A tag turned into a text before (and after) its line's, coloured; without a rule:
    removed."""
    md = (
        "- #influence Membre du **Comité** %% Dossier, p. 1 %% — 2021\n"
        "- Projet `#influence` #positive\n"
    )
    icon = copy_filters.TagRule("influence", "🌍", color="#c00")
    html, plain = marmotte(md, [icon])
    assert '<p>• <span style="color:#CC0000">🌍</span> Membre du <strong>Comité</strong>' in html
    assert "<code>#influence</code>" in html and "#positive" not in html  # (not in code)
    assert plain.startswith("• 🌍 Membre du Comité")
    line = copy_filters.TagRule("#influence", "★", "(R)", "#7030A0", whole=True)
    html, _ = marmotte(md, [line])
    assert (
        '<span style="color:#7030A0">★ Membre du <strong>Comité</strong>  — 2021 (R)</span>' in html
    )
    html, _ = marmotte(md, [])
    assert "<p>• Membre du" in html
    # Saved in the settings (used by default; those without a tag left out).
    copy_filters.set_tag_rules([icon, copy_filters.TagRule(" ")])
    assert copy_filters.tag_rules() == [icon]
    assert marmotte(md)[0] == marmotte(md, [icon])[0]


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_tag_rules_settings(user: User):
    await user.open("/settings?tab=copy")
    user.find(marker="tag-rule-add").click()
    await user.should_see(marker="tag-rule-tag-0")
    user.find(marker="tag-rule-tag-0").type("influence")
    user.find(marker="tag-rule-before-0").type("🌍")
    user.find(marker="tag-rule-whole-0").click()
    assert copy_filters.tag_rules() == [copy_filters.TagRule("influence", "🌍", whole=True)]
