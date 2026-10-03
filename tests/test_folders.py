from datetime import date

from helpers import make_person

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
    folders.delete_folder(fid)
    assert [p.name for p in annotations.periods(a, include_hidden_folders=True)] == ["HDR"]


def test_folder_stars_are_per_person_and_folder():
    from helpers import add_source, pub

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
    from helpers import add_source, pub

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
