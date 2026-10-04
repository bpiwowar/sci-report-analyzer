"""Tracks: their definitions (names, colours, rules) as settings, and a paper's track set by
hand."""

import asyncio
import json

from helpers import add_source, make_person, pub
from sqlalchemy import select

from sci_report_analyzer import annotations, pubview, settings_io, venues
from sci_report_analyzer.db.models import Publication, VenueKey
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.i18n import using
from sci_report_analyzer.ranking import tracks
from sci_report_analyzer.ranking.badge import category_label, category_of, category_order
from sci_report_analyzer.ranking.service import MatchSettings, load_settings, save_settings


def _add_track(tid: str, name: str, pattern: str, colour: str = "#bf3989") -> None:
    st = load_settings()
    rule = tracks.TrackRule(id=f"{tid}_1", pattern=pattern)
    st.tracks.append(tracks.Track(id=tid, names={"en": name}, colour=colour, rules=[rule]))
    save_settings(st)


def test_defaults():
    # The built-in tracks, in order, each with its colour; their examples match their rules.
    assert [t.id for t in MatchSettings().tracks] == ["findings", "tutorial", "demo", "short"]
    assert tracks.colour("short") == "#d4a72c" and tracks.colour("demo") == "#8a6fd0"
    for t in tracks.DEFAULT_TRACKS:
        for r in t.rules:
            assert r.examples and all(tracks.matches(t.id, ex) for ex in r.examples), r.id
    # Saved settings missing a built-in track get it back (at the end); "main" is no track.
    st = MatchSettings.model_validate(
        {"tracks": [{"id": "demo", "colour": "#000000"}, {"id": "main"}]}
    )
    assert [t.id for t in st.tracks] == ["demo", "findings", "tutorial", "short"]
    assert st.tracks[0].colour == "#000000"


def test_names_by_language():
    assert tracks.name("short") == "Short"
    with using("fr"):
        assert tracks.name("short") == "Court"
    _add_track("industry", "Industry", r"\bindustry track\b")
    with using("fr"):
        assert tracks.name("industry") == "Industry"  # (no French name: the English one)
    assert tracks.name("gone") == "gone"


def test_findings_detected_last():
    assert tracks.detect("Findings of ACL 2023") == "findings"
    assert tracks.detect("Findings of ACL 2023 (Short Papers)") == "short"
    assert tracks.detect("Findings of ACL 2023", findings=False) is None


def test_added_track_detected_and_counted_apart():
    _add_track("industry", "Industry", r"\bindustry track\b")
    assert tracks.detect("WIDG 2024, Industry Track") == "industry"
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "A paper", 2024, "WIDG 2024, Industry Track")])
    (s,) = asyncio.run(pubview.load_stats(pid))
    assert s.track == "industry" and s.category.track == "industry"
    assert category_label(s.category).startswith("Industry ")
    assert s.category.stripe == "#bf3989"  # (striped in the track's colour)
    # Its categories come after the built-in tracks' (the tracks' order).
    a = category_of(None, "short", "intl_conference")
    b = category_of(None, "industry", "intl_conference")
    assert category_order(a) < category_order(b)


def test_edited_rule_and_invalid_one():
    st = load_settings()
    demo = next(t for t in st.tracks if t.id == "demo")
    demo.rules[0].pattern = r"\bshowcase\b"
    save_settings(st)
    assert tracks.detect("ACL 2023 Showcase") == "demo"
    assert tracks.detect("ACL 2023 (System Demonstrations)") is None
    assert tracks.rule_origin(demo.rules[0]) == "edited"
    assert tracks.origin(demo) == "edited"
    demo.rules[0].pattern = r"(unclosed"  # invalid: its default
    save_settings(st)
    assert tracks.detect("ACL 2023 (System Demonstrations)") == "demo"
    demo.rules[0].pattern = ""  # empty: off
    save_settings(st)
    assert tracks.detect("ACL 2023 (System Demonstrations)") is None


def test_conference_name_from_the_name_rules():
    """The name of the conference a venue is a track of: from the track's name rules (each
    built-in one changes its examples), editable; none when they change nothing."""
    for t in tracks.DEFAULT_TRACKS:
        for r in t.name_rules:
            assert r.examples and all(r.apply(ex) != ex for ex in r.examples), r.id
    widg = "Conference on Widget Processing (WIDG)"
    demo = "WIDG (Demonstration) Conference on Widget Processing"
    assert tracks.conference_name("demo", f"{demo} (WIDG)") == widg
    assert tracks.conference_name("demo", demo) == widg  # (its acronym in front)
    assert tracks.conference_name("demo", "WIDG Demo Track") == "WIDG"
    assert tracks.conference_name("tutorial", "Tutorials of WIDG") == "WIDG"
    assert tracks.conference_name("demo", "Demo") is None  # nothing left
    assert tracks.conference_name("demo", widg) is None  # nothing changed
    # Settings saved before the name rules: the built-in tracks get their defaults.
    st = MatchSettings.model_validate({"tracks": [{"id": "demo", "colour": "#000000"}]})
    assert st.tracks[0].name_rules == tracks.DEFAULTS["demo"].name_rules
    # Edited (origin, and in force once saved); an added track has none.
    st = load_settings()
    demo_track = next(t for t in st.tracks if t.id == "demo")
    rule = demo_track.name_rules[1]
    assert tracks.name_rule_origin(rule) == "default"
    rule.replacement = " (the conference)"
    assert tracks.name_rule_origin(rule) == "edited" and tracks.origin(demo_track) == "edited"
    demo_track.name_rules.append(tracks.NameRule(id="demo_name_1", pattern="^Proc\\. "))
    assert tracks.name_rule_origin(demo_track.name_rules[-1]) == "added"
    save_settings(st)
    assert tracks.conference_name("demo", "ACL (Demos)") == "ACL (the conference)"
    assert tracks.conference_name("demo", "Proc. WIDG") == "WIDG"
    _add_track("industry", "Industry", r"\bindustry track\b")
    assert tracks.get("industry").name_rules == []
    assert tracks.conference_name("industry", "WIDG Industry Track") is None


def test_using_previews_edited_tracks():
    defs = tracks.default_tracks()
    defs[2].rules[0].pattern = r"\bshowcase\b"
    with tracks.using(defs):
        assert tracks.detect("ACL 2023 Showcase") == "demo"
    assert tracks.detect("ACL 2023 Showcase") is None


def test_new_id():
    assert tracks.new_id("Industry papers", ["demo"]) == "industry_papers"
    assert tracks.new_id("Démo", ["demo"]) == "demo2"
    assert tracks.new_id("Main", []) == "main2"
    assert tracks.new_id("???", []) == "track"


def test_forget_deleted_tracks():
    _add_track("industry", "Industry", r"\bindustry track\b")
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "A paper", 2024, "Widget Conference")])
    (s,) = asyncio.run(pubview.load_stats(pid))
    annotations.set_track_override(s.id, "industry")
    with session_scope() as ss:
        key = ss.scalar(select(VenueKey.key))
    venues.set_variant_track(key, "industry")
    assert annotations.forget_tracks({"industry"}) == 2
    with session_scope() as ss:
        assert ss.get(Publication, s.id).track_override is None
        assert ss.get(VenueKey, key).track is None


def test_export_import_tracks():
    _add_track("industry", "Industry", r"\bindustry track\b")
    st = load_settings()
    next(t for t in st.tracks if t.id == "short").colour = "#123456"
    save_settings(st)
    exported = settings_io.export_settings().model_dump_json()
    assert '"flags"' not in exported
    save_settings(MatchSettings())
    data = settings_io.parse_file(exported)
    conflicts = {c.id for c in settings_io.find_conflicts(data)}
    assert conflicts == {"matching:tracks.short"}
    settings_io.import_settings(data, "merge", take_imported=set())
    st = load_settings()
    assert tracks.colour("short") == "#d4a72c"  # (kept local)
    assert "industry" in [t.id for t in st.tracks]  # (added)
    settings_io.import_settings(data, "merge", take_imported={"matching:tracks.short"})
    assert tracks.colour("short") == "#123456"


def test_replace_import_keeps_local_added_tracks():
    _add_track("industry", "Industry", r"\bindustry track\b")
    data = settings_io.SettingsFile()
    settings_io.import_settings(data, "replace")
    assert [t.id for t in load_settings().tracks][-1] == "industry"


def test_import_track_mapping():
    """Before an import: the file's tracks against the local ones; a local track added by
    hand that the file lacks is kept, or removed (then no paper is of it)."""
    _add_track("talks", "Talks", r"\btalks?\b")
    exported = settings_io.export_settings().model_dump_json()
    save_settings(MatchSettings())
    _add_track("industry", "Industry", r"\bindustry track\b")
    st = load_settings()
    next(t for t in st.tracks if t.id == "short").colour = "#123456"
    save_settings(st)
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "A paper", 2024, "WIDG 2024")])
    (s,) = asyncio.run(pubview.load_stats(pid))
    annotations.set_track_override(s.id, "industry")
    data = settings_io.parse_file(exported)
    m = settings_io.track_mapping(data)
    assert [t.id for t in m.added] == ["talks"] and [t.id for t in m.local] == ["industry"]
    differ = [local.id for local, imported in m.both if m.differ(local, imported)]
    assert differ == ["short"]
    settings_io.import_settings(data, "merge", remove_tracks={"industry", "short"})
    ids = [t.id for t in load_settings().tracks]
    assert "talks" in ids and "industry" not in ids and "short" in ids  # (built in: kept)
    with session_scope() as ss:
        assert ss.get(Publication, s.id).track_override is None
    # Kept unless removed.
    _add_track("industry", "Industry", r"\bindustry track\b")
    settings_io.import_settings(data, "replace")
    assert [t.id for t in load_settings().tracks][-1] == "industry"


def test_import_of_a_file_with_flags():
    """A file of before tracks: its flags' colours become the tracks' (the other flags are
    ignored)."""
    old = {
        "format": "sci-report-analyzer-settings",
        "version": 4,
        "matching": {"min_score": 0.7},
        "flags": [
            {"name": "short paper", "colour": "#57606a", "track": "short"},
            {"name": "to check", "colour": "#ff0000"},
        ],
    }
    data = settings_io.parse_file(json.dumps(old))
    short = next(t for t in data.matching.tracks if t.id == "short")
    assert short.colour == "#57606a" and data.matching.min_score == 0.7
    settings_io.import_settings(data, "replace")
    assert tracks.colour("short") == "#57606a"


def test_migration_copy_of_the_default_tracks():
    from sci_report_analyzer.db.migrations.versions import a4c7e2f9d316_tracks_no_flags as m

    # (A copy of before the name rules: they get their defaults when loaded.)
    assert [t.model_dump(exclude={"name_rules"}) for t in tracks.DEFAULT_TRACKS] == m.TRACKS
