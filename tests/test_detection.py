"""Detection rules: the regexes classifying venues and papers, as settings."""

from sci_report_analyzer import settings_io
from sci_report_analyzer.ranking import detection
from sci_report_analyzer.ranking.badge import detect_track
from sci_report_analyzer.ranking.detection import DEFAULT_DETECTION_RULES, DetectionRule
from sci_report_analyzer.ranking.kinds import WORKSHOP_RE, KindEvidence, detect_kind, host_text
from sci_report_analyzer.ranking.service import MatchSettings, load_settings, save_settings


def _kind(venue, title=None):
    st = load_settings()
    return detect_kind(
        None,
        KindEvidence(venue, title=title),
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
    # {rule:campaigns} is the campaigns' pattern: a new campaign is a shared task's.
    title = "Team Foo at Zorblat-2024 Task 3: Bar"
    assert _kind("Proceedings of the Workshop on Foo", title) == "intl_workshop"
    default = detection.DEFAULTS["campaigns"].pattern
    _set("campaigns", default + "|Zorblat", ignore_case=False)
    assert _kind("Proceedings of the Workshop on Foo", title) == "shared_task"


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
    assert host_text("Foo @ SIGIR") is None


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
