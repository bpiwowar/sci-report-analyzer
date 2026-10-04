"""Detection rules: the regexes classifying venues and papers, as settings."""

from sci_report_analyzer import settings_io
from sci_report_analyzer.ranking import detection
from sci_report_analyzer.ranking.badge import detect_track
from sci_report_analyzer.ranking.detection import DEFAULT_DETECTION_RULES, DetectionRule
from sci_report_analyzer.ranking.kinds import WORKSHOP_RE, KindEvidence, detect_kind, host_text
from sci_report_analyzer.ranking.service import MatchSettings, load_settings, save_settings


def _kind(venue):
    st = load_settings()
    return detect_kind(
        None,
        KindEvidence(venue),
        national_keywords=st.national_keywords,
        international_keywords=st.international_keywords,
    )


def _set(rule_id: str, pattern: str, ignore_case: bool = True) -> None:
    st = load_settings()
    st.detection_rules = [
        DetectionRule(id=rule_id, pattern=pattern, ignore_case=ignore_case)
        if r.id == rule_id
        else r
        for r in st.detection_rules
    ]
    save_settings(st)


def test_defaults():
    # Every rule is in the settings, with its default; its examples match it.
    assert [r.id for r in MatchSettings().detection_rules] == [
        d.id for d in DEFAULT_DETECTION_RULES
    ]
    regexes = detection.compile_rules(())
    for d in DEFAULT_DETECTION_RULES:
        assert d.examples and all(regexes[d.id].search(ex) for ex in d.examples), d.id
    # Saved settings without the rules (or some of them) get the defaults.
    st = MatchSettings.model_validate({"detection_rules": [{"id": "joint", "pattern": "x"}]})
    assert [r.id for r in st.detection_rules] == [d.id for d in DEFAULT_DETECTION_RULES]
    assert st.detection_rules[-1].pattern == "x"
    assert MatchSettings.model_validate({}).detection_rules == MatchSettings().detection_rules


def test_rule_references():
    # {rule:track_tutorial} is the tutorial rule's pattern.
    assert _kind("Zorblat Seminar") == "intl_journal"
    _set("workshop", r"\bseminar\b|{rule:track_tutorial}")
    assert _kind("Zorblat Seminar") == "intl_workshop"
    assert _kind("Zorblat Tutorials") == "intl_workshop"
    _set("track_tutorial", r"\bcourse\b")
    assert _kind("Zorblat Course") == "intl_workshop"


def test_language_rules():
    # A rule of a language is part of another: a text matching it matches the latter.
    assert {d.language for d in DEFAULT_DETECTION_RULES} == {None, "en", "fr"}
    parts = {d.id: d.part_of for d in DEFAULT_DETECTION_RULES}
    assert parts["workshop_fr"] == "workshop" and parts["workshop_at"] == "workshop"
    assert all(parts[p] is None for p in parts.values() if p)
    assert WORKSHOP_RE.search("Atelier sur les gadgets")
    assert detect_track("Foo 2024 (Démonstrations)") == "demo"
    assert _kind("Revue des gadgets") == "natl_journal"
    # The leftmost match of the parts: the main conference's text.
    assert host_text("Foo @ ECIR, co-located with SIGIR") == "ECIR"
    # Changed, a part changes its rule's detection only.
    _set("workshop_fr", r"\bséminaires?\b")
    assert not WORKSHOP_RE.search("Atelier sur les gadgets")
    assert WORKSHOP_RE.search("Séminaire sur les gadgets")
    assert WORKSHOP_RE.search("Workshop on Gadgets")


def test_changed_rules_change_the_detection():
    assert _kind("Seminar on Foo") == "intl_journal"
    assert WORKSHOP_RE.search("Workshop on Foo")
    _set("workshop", r"\bseminars?\b")
    assert _kind("Seminar on Foo") == "intl_workshop"
    assert not WORKSHOP_RE.search("Workshop on Foo")

    assert detect_track("ACL 2023 (System Demonstrations)") == "demo"
    _set("track_demo", r"\bshowcase\b")
    assert detect_track("ACL 2023 (System Demonstrations)") is None
    assert detect_track("ACL 2023 Showcase") == "demo"

    _set("workshop_host", r"\bhosted by (.+)")
    assert host_text("Foo Workshop, hosted by ECIR") == "ECIR"
    assert host_text("Foo, co-located with SIGIR") is None
    assert host_text("Foo @ SIGIR") == "SIGIR"  # (its own rule)


def test_invalid_rule_takes_its_default():
    _set("workshop", r"(unclosed")
    assert WORKSHOP_RE.search("Workshop on Foo")
    assert detection.compile_rules(load_settings().detection_rules)["workshop"] is None


def test_using_previews_edited_rules():
    rules = [DetectionRule(id="workshop", pattern=r"\bseminar\b")]
    with detection.using(rules):
        assert WORKSHOP_RE.search("Seminar on Foo")
    assert not WORKSHOP_RE.search("Seminar on Foo")


def test_export_import():
    _set("joint", r"\bjoint\b")
    exported = settings_io.export_settings().model_dump_json()
    _set("joint", detection.DEFAULTS["joint"].pattern)
    data = settings_io.parse_file(exported)
    conflicts = {c.id for c in settings_io.find_conflicts(data)}
    assert conflicts == {"matching:detection.joint"}
    settings_io.import_settings(data, "merge", take_imported=set())
    assert next(r for r in load_settings().detection_rules if r.id == "joint").pattern != (
        r"\bjoint\b"
    )
    settings_io.import_settings(data, "merge", take_imported={"matching:detection.joint"})
    assert next(r for r in load_settings().detection_rules if r.id == "joint").pattern == (
        r"\bjoint\b"
    )
