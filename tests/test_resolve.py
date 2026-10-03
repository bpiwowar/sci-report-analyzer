import asyncio
from pathlib import Path

from helpers import add_source, make_person, pub

from sci_report_analyzer import annotations, pubview, venues
from sci_report_analyzer.ranking.service import service


def resolve(*a, **kw):
    return asyncio.run(service.resolve(*a, **kw))


def test_archival_and_findings():
    assert resolve("CoRR").archival
    assert resolve("arXiv preprint arXiv:2101.00001").archival
    b = resolve("Findings of the Association for Computational Linguistics: EMNLP 2023")
    assert b is None or b.findings


def test_paren_acronym_falls_back_to_core_alias():
    # "EMNLP" is a CORE alias in the golden records; "NLP" stands for its last words.
    b = resolve("Empirical Methods in NLP (EMNLP)", None, "conference")
    assert b is not None and b.source == "core" and b.coreRank
    # Only when the names overlap: "IC" is also "International Conference on Internet
    # Computing".
    assert resolve("Some Odd Stream Title Nobody Uses (EMNLP)", None, "conference") is None
    ic = "Journées Francophones d'Ingénierie des Connaissances (IC)"
    assert resolve(ic, None, "conference") is None


def test_venue_search_text_and_level():
    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [pub("a", "P1", 2020, "My Weird Venue 2021"), pub("b", "P2", 2020, "Tiny Workshop 2020")],
    )
    venues.set_correction("My Weird Venue 2020", "Neural Computation")
    venues.set_level("Tiny Workshop", "conference", "B")
    st = {s.title: s for s in asyncio.run(pubview.load_stats(pid))}
    assert st["P1"].badge is not None and st["P1"].badge.corrected
    assert st["P2"].badge.manual and st["P2"].badge.coreRank == "B"


def test_predatory_flag():
    b = resolve("Journal of Totally Legit Science")
    assert b is not None and b.predatory


def test_forced_publication_match():
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Some paper", 2020, "Unknown venue xyz")])
    stats = asyncio.run(pubview.load_stats(pid))
    assert stats[0].badge is None
    annotations.set_rank_override(stats[0].id, {"type": "journal", "rank": "Q1"}, "why")
    stats = asyncio.run(pubview.load_stats(pid))
    assert stats[0].badge.quartile == "Q1" and stats[0].badge.forced
    assert stats[0].category.key == "q1" and stats[0].rank_note == "why"
    annotations.set_rank_override(stats[0].id, None)
    assert asyncio.run(pubview.load_stats(pid))[0].badge is None


def test_flag_track_makes_satellite_category():
    pid = make_person()
    add_source(pid, "dblp", "x/1", [pub("a", "Some paper", 2020, "Unknown venue xyz")])
    stats = asyncio.run(pubview.load_stats(pid))
    short = next(f for f in annotations.all_flags() if f.name == "short")
    annotations.toggle_flag(stats[0].id, short.id)
    stats = asyncio.run(pubview.load_stats(pid))
    assert stats[0].track == "short" and stats[0].category.key.startswith("short:k_")


def test_year_bins():
    assert pubview.year_bin_defs([2000, 2003]) == [(str(y), y, y) for y in range(2000, 2004)]
    bins = pubview.year_bin_defs([2000, 2024])
    assert len(bins) <= 10 and bins[0][1] == 2000 and bins[-1][2] == 2024


def test_norm_rules():
    from sci_report_analyzer.ranking.normalize import NormRule
    from sci_report_analyzer.ranking.service import load_settings, save_settings

    st = load_settings()
    st.norm_rules.insert(
        0,
        NormRule(
            id="ws",
            name="ws",
            pattern=r"(\w+) \d+ workshops?",
            replacement=r"\1 Workshop",
            ignore_case=True,
        ),
    )
    st.norm_rules.append(
        NormRule(id="dblp", name="d", pattern=r"^Proc\. ", replacement="", sources=["dblp"])
    )
    save_settings(st)
    assert service.clean("NeurIPS 2024 Workshop on Foo") == "NeurIPS Workshop on Foo"
    assert service.key("NeurIPS 2023 Workshops") == "neurips workshop"
    assert service.clean("Proc. ECIR", "dblp") == "ECIR"
    assert service.clean("Proc. ECIR", "hal") == "Proc. ECIR"


def test_openalex_off_by_default():
    from sci_report_analyzer.ranking.service import MatchSettings

    assert not MatchSettings().source_on("openalex")
    assert MatchSettings().source_on("core")
    assert not MatchSettings(sources={"core": True}).source_on("openalex")


def test_owner_and_student_highlighting():
    from helpers import thesis

    from sci_report_analyzer.db.models import Person
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    with session_scope() as s:
        p = s.get(Person, pid)
        p.aliases = ["J. Q. Doe-Smith"]
        p.student_aliases = {"Alice Martin": ["A. Martin-Durand"]}
    add_source(pid, "thesesfr", "123456789", theses=[thesis("s1", "director", "T")])
    with session_scope() as s:
        from sqlalchemy import update

        from sci_report_analyzer.db.models import Thesis

        s.execute(update(Thesis).values(student="Alice Martin"))
    add_source(
        pid,
        "hal",
        "idhal:jd",
        [
            pub("h1", "Paper one", 2020, "V", authors=["A. Martin-Durand", "J. Q. Doe-Smith"]),
        ],
    )
    (st,) = asyncio.run(pubview.load_stats(pid))
    assert st.author_marks == ["student", "owner"]
    assert st.author_pos == 2  # found through the alias


def test_core_rank_at_publication_year():
    from sci_report_analyzer.ranking.badge import core_rank_at

    h = {"CORE2013": "B", "CORE2018": "A", "CORE2023": "A*"}
    assert core_rank_at(h, 2010) == ("CORE2013", "B")  # not ranked yet: its first rank
    assert core_rank_at(h, 2014) == ("CORE2013", "B")  # gap in CORE2014: the one before
    assert core_rank_at(h, 2019) == ("CORE2018", "A")
    assert core_rank_at(h, 2025) == ("CORE2023", "A*")
    assert core_rank_at(h, 2027) == ("ICORE2026", None)  # dropped since


def test_paper_gets_core_rank_of_its_year():
    from sci_report_analyzer.ranking.service import load_settings, save_settings

    pid = make_person()
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub("a", "Old paper", 2019, "Symposium on Timely Rankings"),
            pub("b", "New paper", 2024, "Symposium on Timely Rankings"),
        ],
    )
    stats = {s.title: s for s in asyncio.run(pubview.load_stats(pid))}
    old, new = stats["Old paper"].badge, stats["New paper"].badge
    assert (old.coreRank, old.coreEdition) == ("B", "CORE2018")
    assert old.extra["coreLatest"] == ["ICORE2026", "A"]
    assert stats["Old paper"].category.key == "b"
    assert (new.coreRank, new.coreEdition) == ("A", "CORE2023")
    st = load_settings()
    st.core_edition = "latest"
    save_settings(st)
    stats = {s.title: s for s in asyncio.run(pubview.load_stats(pid))}
    assert stats["Old paper"].badge.coreRank == "A"


def test_predatory_list_is_downloaded_and_refreshed(tmp_path, monkeypatch):
    import os

    import httpx
    import respx

    from sci_report_analyzer import config
    from sci_report_analyzer.ranking import datasets

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with respx.mock:
        journals = respx.get(f"{datasets.PREDATORY_BASE}/journals.csv").mock(
            return_value=httpx.Response(
                200, text="url,name,abbr\nhttp://x.org,Journal of Legit Science,JLS\n"
            )
        )
        respx.get(f"{datasets.PREDATORY_BASE}/publishers.csv").mock(
            return_value=httpx.Response(200, text="url,name,abbr,\n,Shady Press,,\n")
        )
        assert asyncio.run(datasets.refresh_predatory())
        assert datasets.load_predatory() == [
            {
                "name": "Journal of Legit Science",
                "source": "predatory",
                "type": "journal",
                "predatory": True,
                "url": "http://x.org",
                "aliases": ["JLS"],
            },
            {"name": "Shady Press", "source": "predatory", "type": "journal", "predatory": True},
        ]
        assert not asyncio.run(datasets.refresh_predatory())  # up to date
        old = datasets.predatory_path().stat().st_mtime - datasets.PREDATORY_MAX_AGE - 1
        os.utime(datasets.predatory_path(), (old, old))
        assert asyncio.run(datasets.refresh_predatory())
        assert journals.call_count == 2


def test_datasets_ship_with_the_app():
    import json

    from sci_report_analyzer.ranking import datasets

    shipped = Path(datasets.__file__).resolve().parent.parent / "data"
    assert not (shipped / "journals.json").exists()  # downloaded from the repository's:
    repo = Path(datasets.__file__).resolve().parents[3] / "datasets"
    journals = json.loads((repo / "journals.json").read_text())
    assert len(journals) > 10000
    assert all(r["source"] == "scimago" for r in journals)
    current = json.loads((shipped / "conferences.json").read_text())
    past = json.loads((shipped / "conferences.past.json").read_text())
    assert len(current) > 500 and len(past) > 500
    sigir = next(r for r in current if "SIGIR" in r.get("aliases", []))
    assert sigir["coreHistory"]["ERA2010"] == "A" and sigir["coreEdition"] == "ICORE2026"


def test_a_society_is_not_its_journal():
    async def name(text):
        b = await service.resolve(text)
        return b.name if b else None

    # Both reduce to "neuroscience", but the generic words differ.
    assert asyncio.run(name("Society for Neuroscience")) is None
    assert asyncio.run(name("Computer Music Conference")) != "Computer Music Journal"
    assert asyncio.run(name("Journal of Neuroscience")) == "Journal of Neuroscience"


def test_journals_are_downloaded_and_merged(monkeypatch, tmp_path):
    """Installed (not run from the source tree), the Scimago journals are downloaded from
    the repository, each update merged into the copy there; unchanged: not downloaded."""
    import httpx
    import respx

    from sci_report_analyzer import config
    from sci_report_analyzer.ranking import datasets

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(datasets, "SOURCE_JOURNALS", None)
    old = {
        "name": "Old Journal",
        "source": "scimago",
        "sjrYear": 2020,
        "sjrHistory": {"2020": "Q2"},
    }
    new = {
        "name": "Old Journal",
        "source": "scimago",
        "sjrYear": 2024,
        "sjrHistory": {"2024": "Q1"},
    }
    with respx.mock:
        route = respx.get(datasets.JOURNALS_URL)
        route.mock(return_value=httpx.Response(200, json=[old], headers={"etag": '"v1"'}))
        assert asyncio.run(datasets.refresh_journals())
        assert not asyncio.run(datasets.refresh_journals())  # checked recently
        route.mock(return_value=httpx.Response(304))
        assert not asyncio.run(datasets.refresh_journals(force=True))
        assert route.calls.last.request.headers["if-none-match"] == '"v1"'
        route.mock(return_value=httpx.Response(200, json=[new], headers={"etag": '"v2"'}))
        assert asyncio.run(datasets.refresh_journals(force=True))
    (journal,) = datasets.load_journals()
    assert journal["sjrYear"] == 2024
    assert journal["sjrHistory"] == {"2020": "Q2", "2024": "Q1"}
