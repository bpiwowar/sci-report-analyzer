from datetime import date

from helpers import add_source, make_person, pub

from sci_report_analyzer import annotations, folders


def test_folder_membership_and_periods():
    a, b = make_person("Ann"), make_person("Bob")
    fid = folders.save_folder(None, "Committee", date(2026, 5, 1))
    pa = folders.add_person(fid, a, 2020, 2025)
    assert folders.add_person(fid, a) == pa  # one period per person and folder
    folders.add_person(fid, b)
    [f] = folders.folders()
    assert [(m.name, m.start_year, m.end_year) for m in f.members] == [
        ("Ann", 2020, 2025),
        ("Bob", None, None),
    ]
    # The folder period is one of the person's periods, named after the folder.
    annotations.save_period(a, "HDR", 2015, 2024)
    names = [(p.name, p.folder_id) for p in annotations.periods(a)]
    assert names == [("HDR", None), ("Committee", fid)]
    folders.save_folder(fid, "Hiring committee", date(2026, 5, 1))
    assert annotations.periods(a)[1].name == "Hiring committee"
    # Hidden folders' periods are left out (unless asked for).
    folders.set_hidden(fid, True)
    assert [p.name for p in annotations.periods(a)] == ["HDR"]
    assert len(annotations.periods(a, include_hidden_folders=True)) == 2
    assert folders.folders(include_hidden=False) == []
    folders.remove_person(fid, b)
    assert [m.name for m in folders.folders()[0].members] == ["Ann"]
    # Taken out keeping the data, the folder period becomes one of the person's own.
    pb = folders.add_person(fid, b, 2021, 2023)
    folders.remove_person(fid, b, keep=True)
    assert [m.name for m in folders.folders()[0].members] == ["Ann"]
    assert [(p.id, p.name, p.start_year, p.folder_id) for p in annotations.periods(b)] == [
        (pb, "Hiring committee", 2021, None)
    ]
    folders.delete_folder(fid)
    assert [p.name for p in annotations.periods(a, include_hidden_folders=True)] == ["HDR"]


def test_folder_stars_are_per_person_and_folder():
    a = make_person("Ann")
    add_source(a, "dblp", "x/1", [pub("p", "A paper by Ann", 2021, authors=["Ann"])])
    pub_id = annotations_pub(a)
    f1 = folders.save_folder(None, "F1")
    f2 = folders.save_folder(None, "F2")
    p1, p2 = folders.add_person(f1, a), folders.add_person(f2, a)
    annotations.toggle_star(p1, pub_id)
    stars = {f.name: f.members[0].stars for f in folders.folders()}
    assert stars == {"F1": 1, "F2": 0}
    assert p2 != p1


def test_tags_global_and_per_period():
    from sci_report_analyzer import pubview

    a = make_person("Ann")
    add_source(a, "dblp", "x/1", [pub("p", "A paper by Ann", 2021, authors=["Ann"])])
    pub_id = annotations_pub(a)
    p1 = annotations.save_period(a, "P1", 2020, 2022)
    p2 = annotations.save_period(a, "P2", 2020, 2022)
    glob = annotations.save_tag("survey", "#123456")
    local = annotations.save_tag("discuss", None, per_period=True)
    assert annotations.save_tag("survey") == glob  # an existing name is reused
    annotations.toggle_tag(pub_id, glob)
    annotations.toggle_tag(pub_id, local, p1)
    annotations.set_note(pub_id, "a note")
    annotations.set_note(pub_id, "in P1", p1)
    import asyncio

    [s] = asyncio.run(pubview.load_stats(a))
    assert s.tags_in(p1) == {glob, local}
    assert s.tags_in(p2) == {glob}
    assert s.tags_in(None) == {glob}
    assert pubview.tagged([s], [local], p2) == []
    assert [pubview.hashtag(n) for n in ("to read", "PhD/ICLR", "2023", "été!")] == [
        "#to-read",
        "#PhD/ICLR",
        "#tag-2023",
        "#été",
    ]
    # The built-in starred tag cannot be deleted; others can (from every paper).
    annotations.delete_tag(annotations.starred_tag_id())
    assert annotations.starred_tag_id() is not None
    annotations.delete_tag(local)
    [s] = asyncio.run(pubview.load_stats(a))
    assert s.tags_in(p1) == {glob}


def annotations_pub(person_id: int) -> int:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    with session_scope() as s:
        return s.scalar(select(Publication.id).where(Publication.person_id == person_id))


def test_folder_tags_are_per_person_and_folder():
    a, b = make_person("Ann"), make_person("Bob")
    f1, f2 = folders.save_folder(None, "F1"), folders.save_folder(None, "F2")
    p1 = folders.add_person(f1, a)
    folders.add_person(f1, b)
    folders.add_person(f2, a)
    folders.set_tags(p1, [" shortlisted", "audition", "shortlisted", ""])
    by_folder = {f.name: {m.name: m.tags for m in f.members} for f in folders.folders()}
    assert by_folder == {
        "F1": {"Ann": ["audition", "shortlisted"], "Bob": []},
        "F2": {"Ann": []},
    }
    assert folders.tags_of(next(f for f in folders.folders() if f.id == f1)) == [
        "audition",
        "shortlisted",
    ]


def test_primary_source_default_and_folder_override():
    import asyncio

    from sci_report_analyzer import pubview, reports, source_settings

    pid = make_person()
    add_source(pid, "hal", "idhal:jd", [pub("a", "In HAL", 2020, "ACL", doi="10.1/a")])
    add_source(
        pid,
        "dblp",
        "x",
        [pub("a", "In HAL", 2020, "ACL", doi="10.1/a"), pub("b", "Only DBLP", 2021, "ACL")],
    )
    assert source_settings.primary_for(pid) is None  # none by default
    source_settings.set_default_primary("hal")
    assert source_settings.primary_for(pid) == "hal"
    stats = asyncio.run(pubview.load_stats(pid))
    keys = reports.citation_keys(stats)
    listed = reports.papers_to_discuss(stats, keys, [], None, primary="hal")
    assert [p.stat.title for p in listed] == ["In HAL"]
    # A folder uses the default, none, or another source.
    fid = folders.save_folder(None, "Committee")
    period = folders.add_person(fid, pid, 2020, 2024)
    assert source_settings.primary_for(pid, period) == "hal"
    folders.save_folder(fid, "Committee", primary_source=source_settings.NO_PRIMARY)
    assert source_settings.primary_for(pid, period) is None
    folders.save_folder(fid, "Committee", primary_source="dblp")
    assert source_settings.primary_for(pid, period) == "dblp"
    assert folders.folders()[0].primary_source == "dblp"
    # Not for someone without a profile there (nothing would count), nor a disabled source.
    other = make_person("John Roe")
    add_source(other, "dblp", "y", [pub("c", "Paper C", 2020, "ACL")])
    assert source_settings.primary_for(other) is None
    source_settings.set_disabled({"hal"})
    assert source_settings.primary_for(pid) is None


def test_notes_saved_only_from_the_stored_ones():
    """A person's notes within a folder: saved over the notes the editor loaded only."""
    fid = folders.save_folder(None, "Committee", notes="The folder's own.")
    period = folders.add_person(fid, make_person("Ann"))
    assert folders.set_notes(period, "First.")  # (no base: saved)
    assert folders.set_notes(period, "Second.", base="First.\n")  # (as loaded: spaces aside)
    assert not folders.set_notes(period, "Stale.", base="First.")  # (changed since)
    assert folders.notes_of(period) == "Second."
    assert not folders.set_notes(period, "", base="")  # (loaded empty, written since)
    assert folders.notes_of(period) == "Second."
    # The folder's own notes: kept unless given.
    folders.save_folder(fid, "Hiring committee")
    assert folders.folders()[0].notes == "The folder's own."
    folders.save_folder(fid, "Hiring committee", notes="")
    assert folders.folders()[0].notes is None
