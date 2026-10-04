from helpers import set_correction, set_kind, set_level
from sqlalchemy import select

from sci_report_analyzer import settings_io, venues
from sci_report_analyzer.db.models import Venue, VenueKey
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.ranking.normalize import NormRule
from sci_report_analyzer.ranking.service import VenuePattern, load_settings, save_settings, service


def _venue_attrs():
    with session_scope() as s:
        out = {}
        for vk in s.scalars(select(VenueKey)):
            v = vk.venue
            out[vk.key] = (v.match_text, v.level_rank, v.kind if v.kind_manual else None)
        return out


def test_roundtrip_replace():
    set_correction("Venue A", "Neural Computation")
    set_level("Venue B", "conference", "A")
    set_kind("Venue D", "natl_conference")
    st = load_settings()
    st.min_score = 0.7
    save_settings(st)
    exported = settings_io.export_settings().model_dump_json()

    set_correction("Venue A", None)
    set_correction("Venue C", "Something")
    data = settings_io.parse_file(exported)
    settings_io.import_settings(data, "replace")
    attrs = _venue_attrs()
    assert attrs["venue a"] == ("Neural Computation", None, None)
    assert attrs["venue b"] == (None, "A", None)
    assert attrs["venue c"] == (None, None, None)  # erased by the replace
    assert attrs["venue d"] == (None, None, "natl_conference")
    assert load_settings().min_score == 0.7


def test_merge_with_conflicts():
    set_correction("Venue A", "Local text")
    set_correction("Venue Z", "Kept")
    data = settings_io.SettingsFile(
        matching=load_settings(),
        venues=[
            settings_io.VenueIO(
                name="Venue A",
                variants=[settings_io.VariantIO(raw="Venue A")],
                match_text="Imported text",
            ),
            settings_io.VenueIO(
                name="New", variants=[settings_io.VariantIO(raw="Venue New")], match_text="New"
            ),
        ],
    )
    data.matching.min_score = 0.5
    conflicts = {c.id for c in settings_io.find_conflicts(data)}
    assert conflicts == {"venue:venue a.match_text", "matching:min_score"}

    # keep local for the venue, take imported for min_score
    settings_io.import_settings(data, "merge", {"matching:min_score"})
    got = {k: v[0] for k, v in _venue_attrs().items()}
    assert got == {"venue a": "Local text", "venue z": "Kept", "venue new": "New"}
    assert load_settings().min_score == 0.5

    settings_io.import_settings(data, "merge", {"venue:venue a.match_text"})
    assert _venue_attrs()["venue a"][0] == "Imported text"


def test_merged_variants_are_exported():
    set_correction("Venue A", "X")
    set_correction("Venue A bis", "Y")
    with session_scope() as s:
        a = s.get(VenueKey, "venue a").venue_id
        b = s.get(VenueKey, "venue a bis").venue_id
    venues.merge_venues(a, [b])
    data = settings_io.export_settings()
    (v,) = [v for v in data.venues if "venue a" in v.keys]
    assert sorted(v.keys) == ["venue a", "venue a bis"] and v.match_text == "X"
    with session_scope() as s:
        assert s.scalar(select(Venue).where(Venue.id == b)) is None


def test_norm_rule_conflicts():
    st = load_settings()
    st.norm_rules[0].replacement = "local"
    save_settings(st)
    data = settings_io.SettingsFile(matching=load_settings())
    data.matching.norm_rules[0].replacement = "imported"
    data.matching.norm_rules.append(NormRule(id="new", name="New", pattern="b"))
    first = data.matching.norm_rules[0].id
    assert {c.id for c in settings_io.find_conflicts(data)} == {f"matching:rules.{first}"}
    settings_io.import_settings(data, "merge", set())
    rules = load_settings().norm_rules
    assert rules[0].replacement == "local" and rules[-1].id == "new"
    settings_io.import_settings(data, "merge", {f"matching:rules.{first}"})
    assert load_settings().norm_rules[0].replacement == "imported"


def test_venue_rules_and_identifiers_roundtrip():
    set_correction("Venue A", "X")
    with session_scope() as s:
        vid = s.get(VenueKey, "venue a").venue_id
    venues.save_patterns(vid, [VenuePattern(pattern="^Venue A", track="demo")])
    venues.save_issns(vid, ["1234-5678"])
    venues.update_venue(vid, url="https://venue-a.org")
    exported = settings_io.export_settings().model_dump_json()
    venues.update_venue(vid, url=None)
    settings_io.import_settings(settings_io.parse_file(exported), "replace")
    with session_scope() as s:
        v = s.get(VenueKey, "venue a").venue
        assert v.patterns == [{"pattern": "^Venue A", "track": "demo"}]
        assert v.identifiers == {"issn": ["1234-5678"]}
        assert v.url == "https://venue-a.org"


def test_workshop_hosts_roundtrip():
    with session_scope() as s:
        host = Venue(name="Big Conference")
        ws = Venue(name="Small Workshop", kind="intl_workshop", kind_manual=True)
        s.add_all([host, ws])
        s.flush()
        ws.hosts = [{"venue_id": host.id, "from": 2018, "to": None}]
        ids = host.id, ws.id
    for vid, text in zip(ids, ("Big Conference", "Small Workshop"), strict=True):
        venues.add_variant(vid, text)  # venues without texts are dropped
    exported = settings_io.export_settings()
    (wio,) = [v for v in exported.venues if v.name == "Small Workshop"]
    assert wio.hosts[0].venue == "Big Conference" and wio.hosts[0].start == 2018
    with session_scope() as s:
        s.scalar(select(Venue).where(Venue.name == "Small Workshop")).hosts = None
    settings_io.import_settings(settings_io.parse_file(exported.model_dump_json()), "replace")
    with session_scope() as s:
        ws = s.scalar(select(Venue).where(Venue.name == "Small Workshop"))
        host = s.scalar(select(Venue).where(Venue.name == "Big Conference"))
        assert ws.hosts == [{"venue_id": host.id, "from": 2018, "to": None}]


def test_joint_venue_roundtrip():
    with session_scope() as s:
        a, b, j = Venue(name="Alpha Conf"), Venue(name="Beta Conf"), Venue(name="Alpha-Beta")
        s.add_all([a, b, j])
        s.flush()
        j.joint = {"parts": [a.id, b.id], "manual": True, "use": b.id}
        ids = a.id, b.id, j.id
        for v in (a, b, j):  # venues without texts are dropped
            s.add(VenueKey(key=service.key(v.name), venue_id=v.id, manual=True, example=v.name))
    exported = settings_io.export_settings()
    (jio,) = [v for v in exported.venues if v.name == "Alpha-Beta"]
    assert jio.joint.parts == ["Alpha Conf", "Beta Conf"] and jio.joint.use == "Beta Conf"
    settings_io.import_settings(settings_io.parse_file(exported.model_dump_json()), "replace")
    with session_scope() as s:
        assert s.get(Venue, ids[2]).joint == {
            "parts": [ids[0], ids[1]],
            "manual": True,
            "use": ids[1],
        }
