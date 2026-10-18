"""Options with a built-in default: only a changed one is saved (one as its default follows
the default, a newer one too); their Reset buttons enabled only when there is something to
reset."""

from dataclasses import asdict

import pytest
from helpers import enabled
from nicegui.testing import User

from sci_report_analyzer import contribution, folders, report_templates, reports
from sci_report_analyzer.db.app_settings import get_setting, set_setting
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking import normalize, service, tracks
from sci_report_analyzer.ranking.service import (
    MATCHING_KEY,
    MatchSettings,
    load_settings,
    save_settings,
)

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def stored() -> dict | None:
    return get_setting(MATCHING_KEY)


# ---- the matching settings -----------------------------------------------------------------


def test_matching_defaults_not_saved():
    save_settings(load_settings())
    assert stored() is None


def test_only_the_changes_saved():
    st = load_settings()
    st.min_score = 0.7
    st.sources["openalex"] = True
    st.norm_rules[1].pattern = "changed"
    short = next(t for t in st.tracks if t.id == "short")
    short.colour = "#123456"
    save_settings(st)
    saved = stored()
    assert saved["min_score"] == 0.7 and saved["sources"] == {"openalex": True}
    assert "detection_rules" not in saved and "core_edition" not in saved
    # A built-in rule or track as its default: its id.
    ids = [r.id for r in normalize.DEFAULT_NORM_RULES]
    assert [r if isinstance(r, str) else r["id"] for r in saved["norm_rules"]] == ids
    assert isinstance(saved["norm_rules"][1], dict) and saved["norm_rules"][0] == ids[0]
    found = next(t for t in saved["tracks"] if isinstance(t, dict))
    assert found["id"] == "short" and all(isinstance(r, str) for r in found["rules"])
    assert [t for t in saved["tracks"] if isinstance(t, str)] == [
        t.id for t in tracks.DEFAULT_TRACKS if t.id != "short"
    ]
    st = load_settings()
    assert st.norm_rules[1].pattern == "changed" and st.sources["openalex"]
    assert st.stored() == saved


def test_set_back_to_the_default_clears_it():
    st = load_settings()
    default = st.norm_rules[0].pattern
    st.min_score, st.norm_rules[0].pattern = 0.7, "changed"
    save_settings(st)
    assert set(stored()) == {"min_score", "norm_rules"}
    st.norm_rules[0].pattern = default
    save_settings(st)
    assert stored() == {"min_score": 0.7}
    st.min_score = MatchSettings().min_score
    save_settings(st)
    assert stored() is None


def test_newer_defaults_reach_the_options_at_their_default(monkeypatch):
    st = load_settings()
    st.norm_rules[1].pattern = "changed"  # (the rules saved, the first as its default)
    next(t for t in st.tracks if t.id == "short").colour = "#123456"
    save_settings(st)
    # A new release: other defaults.
    first = normalize.DEFAULT_NORM_RULES[0].model_copy(update={"pattern": "newer"})
    monkeypatch.setitem(service._NORM_DEFAULTS, first.id, first)
    demo = tracks.DEFAULTS["demo"].model_copy(update={"colour": "#abcdef"})
    monkeypatch.setitem(tracks.DEFAULTS, "demo", demo)
    rule = tracks.DEFAULTS["short"].rules[0]
    monkeypatch.setitem(tracks.DEFAULT_RULES, rule.id, rule.model_copy(update={"pattern": "newer"}))
    st = load_settings()
    assert [r.pattern for r in st.norm_rules[:2]] == ["newer", "changed"]
    assert next(t for t in st.tracks if t.id == "demo").colour == "#abcdef"
    short = next(t for t in st.tracks if t.id == "short")
    assert short.colour == "#123456" and short.rules[0].pattern == "newer"


def test_newer_default_rules_when_none_changed(monkeypatch):
    st = load_settings()
    st.min_score = 0.7
    save_settings(st)
    newer = (
        normalize.DEFAULT_NORM_RULES[0].model_copy(update={"pattern": "newer"}),
        *normalize.DEFAULT_NORM_RULES[1:],
    )
    monkeypatch.setattr(normalize, "DEFAULT_NORM_RULES", newer)
    assert load_settings().norm_rules[0].pattern == "newer"


def test_former_copies_of_the_defaults_dropped():
    """Saved by a former version (the defaults in full): only the changes kept, once
    read."""
    old = MatchSettings().model_dump()
    old["min_score"] = 0.7
    set_setting(MATCHING_KEY, old)
    assert load_settings().min_score == 0.7
    assert stored() == {"min_score": 0.7}
    set_setting(MATCHING_KEY, MatchSettings().model_dump())
    load_settings()
    assert stored() is None


# ---- the others ----------------------------------------------------------------------------


def test_contribution_roles_as_their_default():
    cfg = contribution.default_config()
    contribution.save_config(cfg)
    assert get_setting(contribution.KEY) is None
    cfg.fallback = "first"
    contribution.save_config(cfg)
    assert get_setting(contribution.KEY)["fallback"] == "first"
    cfg.fallback = contribution.default_config().fallback
    contribution.save_config(cfg)
    assert get_setting(contribution.KEY) is None
    # A former copy of the defaults: dropped once read.
    set_setting(contribution.KEY, asdict(contribution.default_config()))
    assert contribution.load_config() == contribution.default_config()
    assert get_setting(contribution.KEY) is None


def test_contribution_newer_default(monkeypatch):
    contribution.save_config(contribution.default_config())
    newer = contribution.default_config()
    newer.fallback = "first"
    monkeypatch.setattr(contribution, "default_config", lambda: newer)
    assert contribution.load_config().fallback == "first"


def test_citation_templates_as_their_default():
    t = reports.default_templates()
    reports.save_templates(t)
    assert get_setting(reports.TEMPLATES_KEY) is None
    t.items.pop()
    reports.save_templates(t)
    assert len(get_setting(reports.TEMPLATES_KEY)["items"]) == len(t.items)
    reports.save_templates(reports.default_templates())
    assert get_setting(reports.TEMPLATES_KEY) is None
    set_setting(reports.TEMPLATES_KEY, asdict(reports.default_templates()))
    assert reports.load_templates() == reports.default_templates()
    assert get_setting(reports.TEMPLATES_KEY) is None


def test_report_templates_as_their_default():
    fid = folders.save_folder(None, "Hiring 2026")
    sid = folders.default_settings()

    def saved():
        with session_scope() as s:
            return s.get(folders.FolderSettings, sid).report_templates

    report_templates.save(fid, report_templates.default_templates())
    assert saved() is None
    report_templates.add(fid, "Prize", "## Why\n")
    assert len(saved()) == 2
    report_templates.save(fid, report_templates.default_templates())
    assert saved() is None
    # A former copy of the defaults (as the migration made them): dropped once read.
    with session_scope() as s:
        s.get(folders.FolderSettings, sid).report_templates = [
            asdict(t) for t in report_templates.default_templates()
        ]
    assert report_templates.load(fid) == report_templates.default_templates()
    assert saved() is None


# ---- the Reset buttons ---------------------------------------------------------------------


async def test_reset_buttons_follow_the_edits(user: User) -> None:
    await user.open("/settings?tab=rules")
    await user.should_see("Cleaning rules")
    # The cleaning rules: all, and each.
    await enabled(user, "norm-reset", False)
    await enabled(user, "norm-reset-0", False)
    user.find("norm-pattern-0").elements.pop().value = "changed"
    await enabled(user, "norm-reset", True)
    await enabled(user, "norm-reset-0", True)
    user.find("norm-reset-0").click()
    await enabled(user, "norm-reset", False)
    await enabled(user, "norm-reset-0", False)
    # A detection rule.
    await enabled(user, "detect-reset-workshop", False)
    user.find("detect-pattern-workshop").elements.pop().value = r"\bseminars?\b"
    await enabled(user, "detect-reset-workshop", True)
    user.find("detect-reset-workshop").click()
    await enabled(user, "detect-reset-workshop", False)
    # The tracks: all, and each.
    await enabled(user, "tracks-reset", False)
    await enabled(user, "track-reset-short", False)
    user.find("track-colour-short").elements.pop().value = "#123456"
    await enabled(user, "tracks-reset", True)
    await enabled(user, "track-reset-short", True)
    user.find("tracks-reset").click()
    await enabled(user, "tracks-reset", False)
    # The contribution roles.
    await enabled(user, "contribution-reset", False)
    user.find("contribution-add-rule").click()
    await enabled(user, "contribution-reset", True)
    user.find("contribution-reset").click()
    await enabled(user, "contribution-reset", False)
    # An LLM task's instructions, and the words finding a career.
    await enabled(user, "rewrite-prompt-default", False)
    user.find("rewrite-prompt").elements.pop().value = "Mine"
    await enabled(user, "rewrite-prompt-default", True)
    user.find("rewrite-prompt-default").click()
    await enabled(user, "rewrite-prompt-default", False)
    await enabled(user, "career-words-reset", False)
    user.find("career-words-fr-headings").elements.pop().value = "parcours"
    await enabled(user, "career-words-reset", True)


async def test_reset_buttons_of_saved_changes(user: User) -> None:
    """Changes saved: their Reset enabled when shown."""
    st = load_settings()
    next(r for r in st.norm_rules if r.language is None).pattern = "changed"
    save_settings(st)
    t = reports.default_templates()
    t.items.pop()
    reports.save_templates(t)
    await user.open("/settings?tab=rules")
    await user.should_see("Cleaning rules")
    await enabled(user, "norm-reset", True)
    await enabled(user, "norm-reset-0", True)
    await enabled(user, "report-templates-reset", True)
    user.find("report-templates-reset").click()
    await enabled(user, "report-templates-reset", False)
