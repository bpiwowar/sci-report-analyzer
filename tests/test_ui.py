"""Simulated-user UI tests (no browser needed)."""

import asyncio
import json

import pytest
from helpers import add_source, make_person, note_saved, pub, thesis
from nicegui import ui
from nicegui.testing import User

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _seed() -> int:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "x/1",
        [
            pub(
                "a",
                "Deep ranking for search",
                2021,
                "Neural Computation",
                authors=["Jane Doe", "Bob"],
                author_pos=1,
                num_authors=2,
            ),
            pub(
                "b",
                "A workshop contribution",
                2019,
                "Some Workshop on Things",
                authors=["Al", "Jane Doe"],
                author_pos=2,
                num_authors=2,
            ),
            pub(
                "c",
                "Deep ranking for search",
                2019,
                "CoRR",
                archival=True,
                pdf_url="https://arxiv.org/pdf/1901.00001",
            ),
        ],
    )
    add_source(
        pid,
        "thesesfr",
        "123456789",
        theses=[
            thesis("s1", "director", "A thesis I supervised"),
            thesis("s2", "rapporteur", "A thesis I reviewed"),
        ],
    )
    return pid


async def test_people_page(user: User) -> None:
    _seed()
    await user.open("/")
    await user.should_see("All people")
    table = user.find("people-table").elements.pop()
    assert [(r["name"], r["pubs"], r["n_folders"]) for r in table.rows] == [("Jane Doe", 2, 0)]


async def test_publications_panel(user: User) -> None:
    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    await user.should_see("A workshop contribution")
    await user.should_see("2 publications")  # the arXiv version folded in
    charts = user.find(ui.echart).elements
    titles = {c.options["title"]["text"].split(" ·")[0] for c in charts}
    assert {"Publications by year", "Co-authors per paper"} <= titles


async def test_sources_status(user: User) -> None:
    pid = _seed()
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see("Validated sources")
    await user.should_see("up to date")


async def test_theses_tab(user: User) -> None:
    pid = _seed()
    await user.open(f"/person/{pid}?tab=theses")
    await user.should_see("Supervised (1)")
    await user.should_see("Reviewer (rapporteur) (1)")


async def test_student_outcome(user: User) -> None:
    from sci_report_analyzer.db.models import Person
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}?tab=theses")
    await user.should_see("Supervised (1)")
    table = user.find("theses-director").elements.pop()
    (row,) = table.rows
    assert [lk["label"] for lk in row["links"]] == ["LinkedIn", "Google", "Scholar"]
    assert row["outcome"] == ""
    assert "outcome" not in user.find("theses-rapporteur").elements.pop().rows[0]
    user.find("theses-director").trigger(
        "outcome", {"student": "A Student", "note": " Researcher at Inria "}
    )
    await user.should_see("Note saved")
    with session_scope() as s:
        assert s.get(Person, pid).student_outcomes == {"A Student": "Researcher at Inria"}
    assert table.rows[0]["outcome"] == "Researcher at Inria"


async def test_period_filter(user: User) -> None:
    from sci_report_analyzer import annotations

    pid = _seed()
    annotations.save_period(pid, "Recent", 2020, 2024)
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    assert any(0 in e.options for e in user.find(ui.select).elements)  # period selector
    await user.should_see("A workshop contribution")


@pytest.mark.parametrize(
    "tab",
    [
        "sources",
        "matching",
        "rules",
        "rules-en",
        "rules-fr",
        "kinds",
        "flags",
        "data",
        "keys",
        "io",
    ],
)
async def test_settings_tabs(user: User, tab: str) -> None:
    await user.open(f"/settings?tab={tab}")
    await user.should_see("Settings")


async def test_export(user: User) -> None:
    await user.open("/settings?tab=io")
    user.find("Export settings").click()
    response = await user.download.next()
    assert b"sci-report-analyzer-settings" in response.content


def _pub_id(title: str) -> int:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    with session_scope() as s:
        return s.scalar(select(Publication.id).where(Publication.title == title))


async def test_hide_from_details(user: User) -> None:
    pid = _seed()
    pub_id = _pub_id("A workshop contribution")
    await user.open(f"/person/{pid}")
    await user.should_see("A workshop contribution")
    user.find(f"pub-{pub_id}").click()
    user.find("hide-publication").click()
    await user.should_not_see("A workshop contribution", retries=40)
    await user.should_see("show hidden (1)")


async def test_flag_and_star(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer import annotations
    from sci_report_analyzer.db.models import PeriodTag, PublicationFlag
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    annotations.save_period(pid, "Recent", 2020, 2024)
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("flag-short").click()
    with session_scope() as s:
        assert (
            s.scalar(
                select(PublicationFlag.flag_id).where(PublicationFlag.publication_id == pub_id)
            )
            is not None
        )
    # choosing the period shows the star toggles
    period_select = next(e for e in user.find(ui.select).elements if 0 in e.options)
    period_select.value = next(k for k in period_select.options if k)
    await user.should_see(f"star-{pub_id}")
    user.find(f"star-{pub_id}").click()
    with session_scope() as s:
        assert (
            s.scalar(select(PeriodTag.tag_id).where(PeriodTag.publication_id == pub_id))
            == annotations.starred_tag_id()
        )


async def test_period_sticks(user: User) -> None:
    from sci_report_analyzer import annotations

    pid = _seed()
    period = annotations.save_period(pid, "Recent", 2020, 2024)
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    select = next(e for e in user.find(ui.select).elements if 0 in e.options)
    select.value = period
    assert annotations.panel_state(pid)["period_id"] == period
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    await user.should_not_see("A workshop contribution", retries=20)  # 2019: outside period


async def test_help_page(user: User) -> None:
    await user.open("/help")
    await user.should_see("What SciReport Analyzer does")
    await user.should_see("CORE A*")


async def test_quit_button(user: User, monkeypatch) -> None:
    from nicegui import app

    stopped = []
    monkeypatch.setattr(app, "shutdown", lambda: stopped.append(True))
    await user.open("/help")
    user.find(marker="quit").click()
    await user.should_see("Quit SciReport Analyzer?")
    user.find(marker="quit-confirm").click()
    for _ in range(20):
        if stopped:
            break
        await asyncio.sleep(0.1)
    assert stopped


async def test_panel_reloads_after_resync(user: User, monkeypatch) -> None:
    import asyncio

    from sci_report_analyzer import sync
    from sci_report_analyzer.sources import ADAPTERS
    from sci_report_analyzer.sources.base import FetchResult

    pid = _seed()

    async def fake_fetch(self, external_id, owner_names):
        await asyncio.sleep(0.3)
        return FetchResult(
            publications=[pub("z", "Brand new after resync", 2022, "Neural Computation")]
        )

    async def no_theses(self, external_id, owner_names):
        return FetchResult()

    # (on the classes: patching the instances would leave the real methods on them afterwards)
    monkeypatch.setattr(type(ADAPTERS["dblp"]), "fetch", fake_fetch)
    monkeypatch.setattr(type(ADAPTERS["thesesfr"]), "fetch", no_theses)
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    sync.start_sync(pid)
    await user.should_see("Brand new after resync", retries=100)
    await user.should_not_see("Deep ranking for search", retries=20)  # no longer in DBLP


@pytest.mark.parametrize("tab", ["conferences", "journals", "other"])
async def test_venue_pages(user: User, tab: str) -> None:
    _seed()
    await user.open(f"/venues?tab={tab}")
    await user.should_see("Venues")


async def test_venue_dialog_and_manual_level(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")  # links publications to venues
    await user.should_see("Deep ranking for search")
    with session_scope() as s:
        vid = s.scalar(
            select(Publication.venue_id).where(Publication.title == "A workshop contribution")
        )
        assert vid is not None
        assert s.get(Venue, vid).name
    await user.open(f"/venues?focus={vid}")
    await user.should_see("Variants (raw texts, matched by their cleaned text)")
    user.find("venue-save").click()  # reloads the page after closing the dialog
    await user.should_see("Test a venue string")


async def test_venue_dialog_search_and_use_record(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")  # links publications to venues
    with session_scope() as s:
        vid = s.scalar(
            select(Publication.venue_id).where(Publication.title == "A workshop contribution")
        )
    await user.open(f"/venues?focus={vid}")
    await user.should_not_see(marker="venue-use-0")
    user.find("venue-search-open").click()
    await user.should_see(marker="venue-use-0")
    user.find("venue-use-0").click()
    # Using a record closes the search and shows the chosen record.
    await user.should_not_see(marker="venue-use-0")
    await user.should_see("Automatic")
    user.find("venue-save").click()
    await user.should_see("Test a venue string")
    with session_scope() as s:
        assert s.get(Venue, vid).record_key


async def test_problem_icon_and_filter(user: User) -> None:
    pid = _seed()
    add_source(
        pid,
        "hal",
        "jdoe",
        [pub("h1", "A paper by others", 2020, "Neural Computation", authors=["X Y", "Z W"])],
    )
    ok, bad = _pub_id("Deep ranking for search"), _pub_id("A paper by others")
    await user.open(f"/person/{pid}")
    await user.should_see("A paper by others")
    await user.should_see(marker=f"problems-{bad}")
    await user.should_not_see(marker=f"problems-{ok}")
    user.find("problems-only").click()
    await user.should_not_see("Deep ranking for search")
    await user.should_see("A paper by others")
    user.find(f"pub-{bad}").click()
    await user.should_see("the person's name is not found among the authors — add an alias")


async def test_matching_tab_choose_source(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, VenueText
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    add_source(
        pid,
        "hal",
        "jdoe",
        [
            pub(
                "h1",
                "Deep ranking for search",
                2021,
                "Some Workshop on Things",
                authors=["Jane Doe"],
            )
        ],
    )
    pub_id = _pub_id("Deep ranking for search")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-matching").click()
    await user.should_see("Venue from the sources")
    user.find("override-rank").click()
    await user.should_see("Pick a ranking record")
    assert user.find("cancel-override").elements  # the open editor can be cancelled
    with session_scope() as s:
        vid = next(
            t.venue_id
            for t in s.query(VenueText)
            if t.source == "hal" and t.raw == "Some Workshop on Things"
        )
    # Selecting a venue does not apply it: it has to be validated.
    user.find(f"pick-venue-{vid}").elements.pop().value = True
    user.find("validate-source").click()  # asks for a confirmation
    await user.should_see(marker="confirm-validate")
    with session_scope() as s:
        assert s.get(Publication, pub_id).venue_source is None
    user.find("confirm-validate").click()
    with session_scope() as s:
        assert s.get(Publication, pub_id).venue_source == "hal"


async def test_search_for_another_venue(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    add_source(
        pid,
        "hal",
        "jdoe",
        [pub("h1", "Deep ranking for search", 2021, "Some Workshop", authors=["Jane Doe"])],
    )
    pub_id = _pub_id("Deep ranking for search")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-matching").click()
    user.find("search-venue").click()
    await user.should_see(marker="venue-query")
    user.find("venue-query").type("Timely Rankings")
    await user.should_see(marker="venue-hit-0")
    await user.should_see(marker="venue-hit-short-0")  # the acronym, in bold
    user.find("venue-hit-0").click()
    await user.should_see(marker="venue-chosen")
    with session_scope() as s:
        own = s.get(Publication, pub_id).venue_id
    for box in user.find(ui.checkbox).elements:  # merge every source's venue into it
        box.value = True
    user.find("venue-use").click()
    with session_scope() as s:
        p = s.get(Publication, pub_id)
        v = s.get(Venue, p.venue_id)
        assert v.name == "Symposium on Timely Rankings" and not p.venue_manual
        assert s.get(Venue, own) is None  # merged into it


async def test_search_creates_a_new_venue(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-matching").click()
    user.find("search-venue").click()
    await user.should_see(marker="venue-query")
    user.find("venue-query").type("Brand New Meeting (BNM)")
    await user.should_see(marker="venue-new")
    user.find("venue-new").click()
    await user.should_see(marker="venue-new-kind")
    assert user.find("venue-new-short").elements.pop().value == "BNM"
    user.find("venue-new-kind").elements.pop().value = "natl_conference"
    user.find("venue-use").click()
    with session_scope() as s:
        p = s.get(Publication, pub_id)
        v = s.get(Venue, p.venue_id)
        assert v.name == "Brand New Meeting (BNM)" and v.kind == "natl_conference"
        assert v.short_name == "BNM" and p.venue_manual


async def test_summary_dialog(user: User) -> None:
    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find("summary").click()
    await user.should_see(marker="summary-text")
    text = user.find("summary-text").elements.pop()
    long = text.value
    assert long and long.splitlines()[0][0].isdigit()
    assert ": " in long.splitlines()[0]  # by kind: "3 Intl. conf.: 2 CORE A (…); …"
    user.find("summary-short").elements.pop().value = True
    cats = {
        m.removeprefix("summary-cat-").removesuffix("-list")
        for el in user.find(ui.label).elements
        if not el.is_deleted
        for m in el._markers
        if m.startswith("summary-cat-") and m.endswith("-list")
    }
    assert cats
    for key in cats:  # venues without the years
        user.find(f"summary-cat-{key}-list").click()
    assert "20" not in text.value  # no year
    assert text.value.count("\n") == long.count("\n")
    key = sorted(cats)[0]
    user.find(f"summary-cat-{key}-count").click()
    await user.should_see(marker=f"summary-cat-{key}-count", content="✅")
    user.find("summary-markdown").elements.pop().value = True
    assert text.value.startswith("- ")
    for box in user.find(ui.checkbox).elements:  # no kind selected: nothing to summarise
        if box.value:
            box.value = False
    assert text.value == ""
    # The settings are remembered.
    user.find("Close").click()
    user.find("summary").click()
    await user.should_see(marker="summary-markdown")
    assert user.find("summary-markdown").elements.pop().value is True
    assert user.find("summary-short").elements.pop().value is True
    await user.should_see(marker=f"summary-cat-{key}-count", content="✅")


async def test_matched_venue_name(user: User) -> None:
    from sci_report_analyzer.ranking.service import service

    pid = make_person("Jane Doe")
    raw = "Proc. of the Neural Computation journal 2021"
    add_source(pid, "dblp", "x/1", [pub("a", "Some paper", 2021, raw, authors=["Jane Doe"])])
    badge = await service.resolve(raw)
    await user.open(f"/person/{pid}")
    await user.should_see("Some paper")
    assert badge is not None and badge.name != raw
    await user.should_see(badge.name)  # the matched name, the original on hover


async def test_folders_on_people_page(user: User) -> None:
    from sci_report_analyzer import folders

    pid = _seed()
    fid = folders.save_folder(None, "Hiring committee")
    period = folders.add_person(fid, pid, 2020, 2024)
    await user.open(f"/?folder={fid}")
    await user.should_see("Hiring committee")
    await user.should_see("Jane Doe")
    await user.should_see("period 2020–2024")
    # The period is set in place.
    user.find(f"period-{period}").click()
    user.find(f"period-start-{period}").elements.pop().value = 2021
    user.find(f"period-save-{period}").click()
    await user.should_see("period 2021–2024")
    # Tags are set on the card, and the folder can be filtered by tag.
    user.find(f"tags-{period}").elements.pop().value = ["shortlisted"]
    assert folders.folders()[0].members[0].tags == ["shortlisted"]
    other = folders.add_person(fid, make_person("John Roe"))
    await user.open(f"/?folder={fid}")
    await user.should_see("John Roe")
    user.find(f"folder-tag-filter-{fid}").elements.pop().value = ["shortlisted"]
    await user.should_not_see("John Roe")
    await user.should_see("Jane Doe")
    assert other != period
    # The folder is sticky: the People page opens on it again.
    await user.open("/")
    await user.should_see(marker="people-title", content="Hiring committee")
    # Opening the person from the folder selects the folder period, with the folder as title.
    await user.open(f"/person/{pid}/{period}")
    await user.should_see("Deep ranking for search")
    await user.should_see(marker="folder-title")
    await user.should_not_see("A workshop contribution")  # 2019: outside 2020–2024
    # All people (cleanup) shows how many folders each person is in.
    await user.open("/?folder=0")
    table = user.find("people-table").elements.pop()
    assert [(r["name"], r["folders"]) for r in table.rows] == [
        ("Jane Doe", "Hiring committee"),
        ("John Roe", "Hiring committee"),
    ]
    await user.open(f"/?folder={fid}")
    user.find(f"hide-folder-{fid}").click()
    assert folders.folders()[0].hidden


async def test_remove_from_folder_keeping_or_deleting_data(user: User) -> None:
    from sci_report_analyzer import annotations, folders

    pid = _seed()
    other = make_person("John Roe")
    fid = folders.save_folder(None, "Hiring committee")
    period = folders.add_person(fid, pid, 2020, 2024)
    folders.add_person(fid, other)
    # From the folder: kept as one of the person's own periods.
    await user.open(f"/?folder={fid}")
    user.find(f"remove-{pid}").click()
    user.find("remove-keep").click()
    assert [m.name for m in folders.folders()[0].members] == ["John Roe"]
    assert [(p.id, p.name, p.folder_id) for p in annotations.periods(pid)] == [
        (period, "Hiring committee", None)
    ]
    # From the person's Periods tab: deleted.
    await user.open(f"/person/{other}?tab=periods")
    user.find(f"leave-{fid}").click()
    user.find("remove-delete").click()
    assert folders.folders()[0].members == [] and annotations.periods(other) == []


async def test_people_cleanup_delete(user: User) -> None:
    from sci_report_analyzer import folders

    _seed()
    other = make_person("To Remove")
    await user.open("/?folder=0")
    table = user.find("people-table").elements.pop()
    table.selected = [r for r in table.rows if r["id"] == other]
    user.find("delete-people").click()
    user.find("confirm-delete").click()
    assert [r.name for r in folders.people_rows()] == ["Jane Doe"]


async def test_author_menu_alias_and_category(user: User) -> None:
    from sci_report_analyzer.db.models import Person
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")  # authors: Jane Doe, Bob
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    await user.should_see(marker="author-1")
    user.find("author-1").click()
    user.find("new-category").click()
    user.find("category-name").type("Intl. collaborators")
    user.find("category-create").click()
    await user.should_see("with Intl. collaborators", retries=40)
    with session_scope() as s:
        cats = s.get(Person, pid).author_categories
    assert list(cats.values()) == [["Bob"]]
    # "This is the person" on another name adds an alias.
    user.find(f"pub-{pub_id}").click()
    await user.should_see(marker="author-1")
    user.find("author-1").click()
    user.find("this-is-owner").click()
    with session_scope() as s:
        assert "Bob" in s.get(Person, pid).aliases


async def test_filters_in_url(user: User, monkeypatch) -> None:
    from sci_report_analyzer.ui import panel

    pid = _seed()
    urls: list[str] = []
    monkeypatch.setattr(
        panel.ui,
        "run_javascript",
        lambda code: urls.append(json.loads(code.split("'', ", 1)[1].rstrip(")"))),
    )
    # Filters given in the URL are applied (a reload gives back the same view)...
    await user.open(f"/person/{pid}?q=workshop&nopre=1")
    await user.should_see("A workshop contribution")
    await user.should_not_see("Deep ranking for search")
    # ... and the address bar follows the current filters.
    assert urls and urls[-1] == f"/person/{pid}?q=workshop&nopre=1"


def test_source_badges_link_to_the_record() -> None:
    from sci_report_analyzer.pubview import MemberView
    from sci_report_analyzer.ui.pub_details import record_url

    def view(source: str, url: str | None) -> MemberView:
        return MemberView(
            1, source, "k", "T", None, 2020, url, None, None, False, profile_url="https://p"
        )

    assert record_url(view("dblp", "https://dblp.org/rec/x.html")) == "https://dblp.org/rec/x.html"
    assert record_url(view("hal", None)) == "https://p"
    # ORCID's declared link is often HAL's: its badge opens the ORCID profile.
    assert record_url(view("orcid", "https://hal.science/hal-1")) == "https://p"


async def test_mapping_rule_updates_the_details(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    venues.set_correction("Mapped Venue Name", None)  # creates the target venue
    with session_scope() as s:
        m = s.get(Publication, pub_id).members[0]
        mid, source = m.id, m.link.source
        target = s.scalar(select(Venue.id).where(Venue.name == "Mapped Venue Name"))
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-matching").click()
    user.find(f"mapping-{mid}").click()
    await user.should_see(marker="rule-save")
    user.find("rule-venue").elements.pop().value = target
    user.find("rule-sources").elements.pop().value = [source]
    user.find("rule-save").click()
    (rule,) = venues.venue_patterns(target)
    assert rule.sources == [source]
    # The details are rebuilt with the new venue.
    await user.should_see("Venue from the sources")
    await user.should_see(marker=f"group-venue-{target}", content="Mapped Venue Name")
    assert mid


async def test_reset_automatic_settings(user: User) -> None:
    from sqlalchemy import func, select

    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Publication, Venue, VenueText
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    venues.set_level("Some Workshop on Things", "conference", "B")
    await user.open("/settings?tab=data")
    user.find("reset-automatic").click()
    await user.should_see(marker="reset-automatic-confirm")
    user.find("reset-automatic-confirm").click()
    await user.should_see("Automatic settings cleared", retries=40)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(VenueText))  # matched again
        v = s.scalar(select(Venue).where(Venue.level_rank == "B"))
        assert v is not None  # the manual decision is kept
        assert (
            s.scalar(select(func.count()).select_from(Publication).where(Publication.hidden)) == 0
        )


async def test_paper_corrections_and_rank_note(user: User) -> None:
    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-notes").click()
    await user.should_see("paper-note")
    user.find("paper-note").elements.pop().value = "checked on the **PDF**"
    await note_saved(user, "paper-note")
    with session_scope() as s:
        assert s.get(Publication, pub_id).note == "checked on the **PDF**"
    user.find("override-year").elements.pop().value = 2018
    user.find("override-save").click()
    with session_scope() as s:
        assert s.get(Publication, pub_id).year_override == 2018
    await user.should_see("2018 · ")
    user.find("paper-type").elements.pop().value = "proceedings"
    with session_scope() as s:
        assert s.get(Publication, pub_id).kind_override == "proceedings"


async def test_norm_rules_save(user: User) -> None:
    from sci_report_analyzer.ranking.service import load_settings

    await user.open("/settings?tab=rules")
    await user.should_see("Cleaning rules")
    user.find("norm-add").click()
    await user.should_see("Custom rule 1")
    (new,) = [e for e in user.find("norm-pattern-0").elements if not e.value]
    new.value = r"^Proc\. "
    user.find("norm-save").click()
    await user.should_see("Cleaning rules saved")
    rules = [r for r in load_settings().norm_rules if r.language is None]
    assert rules[0].pattern == r"^Proc\. " and rules[0].id == "custom1"


async def test_language_cleaning_rules(user: User) -> None:
    """A language's rules are edited on their own; the general ones are left as they were."""
    from sci_report_analyzer.ranking.service import load_settings

    general = [r.id for r in load_settings().norm_rules if r.language is None]
    await user.open("/settings?tab=rules-en")
    await user.should_see("Cleaning rules · English")
    user.find("norm-en-add").click()
    await user.should_see("Custom rule 1")
    (new,) = [e for e in user.find("norm-en-pattern-0").elements if not e.value]
    new.value = r"\bannual\b"
    user.find("norm-en-save").click()
    await user.should_see("Cleaning rules saved")
    rules = load_settings().norm_rules
    assert [(r.id, r.language) for r in rules[:3]] == [
        ("custom1en", "en"),
        ("ordinalsEn", "en"),
        ("ordinalsFr", "fr"),
    ]
    assert [r.id for r in rules if r.language is None] == general


async def test_name_variants_are_in_the_sources_tab(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(pid, "hal", "jd", [pub("h1", "Some paper", 2020, "V", authors=["J. Doe", "X Y"])])
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see("Name variants · 1 to review", retries=40)
    (el,) = user.find("Name variants · 1 to review").elements
    (box,) = user.find(marker="name-variants").elements
    parents = []
    while el.parent_slot is not None:
        el = el.parent_slot.parent
        parents.append(el)
    assert box in parents  # in the Sources tab, not in the publications panel


async def test_drop_a_venue_onto_another(user: User) -> None:
    from nicegui.events import GenericEventArguments

    from sci_report_analyzer.db.models import Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")  # links publications to venues
    from sci_report_analyzer import venues

    venues.set_level("Neural Computation Letters", "journal", "Q2")
    with session_scope() as s:
        target = s.query(Venue).filter_by(name="Neural Computation").one().id
        other = s.query(Venue).filter_by(name="Neural Computation Letters").one().id
    await user.open("/venues?tab=journals")
    await user.should_see("Drag a venue onto another one")
    (table,) = [t for t in user.find(ui.table).elements if any(r["id"] == other for r in t.rows)]
    for listener in table._event_listeners.values():
        if listener.type == "venue_drop":
            args = GenericEventArguments(sender=table, client=table.client, args={})
            args.args.update(src=target, dst=other)  # the other way round, then swapped
            with table.parent_slot:  # as NiceGUI does for a browser event
                listener.handler(args)
    await user.should_see(marker="merge-direction")
    assert user.find("merge-into").elements.pop().text.startswith("Neural Computation Letters")
    user.find("venue-merge-swap").click()
    await user.should_see(marker="merge-into")
    assert user.find("merge-into").elements.pop().text.startswith("Neural Computation —")
    user.find("venue-merge-confirm").click()
    with session_scope() as s:
        assert s.get(Venue, other) is None and s.get(Venue, target) is not None
    # The list is rebuilt in place (same tab), without the merged venue.
    for _ in range(50):
        tables = [t for t in user.find(ui.table).elements if not t.is_deleted]
        if not any(r["id"] == other for t in tables for r in t.rows):
            break
        await asyncio.sleep(0.02)
    assert not any(r["id"] == other for t in tables for r in t.rows)
    assert any(r["id"] == target for t in tables for r in t.rows)


async def test_merge_while_searching_keeps_the_search(user: User) -> None:
    """A merge from the list updates its rows in place: the search text and the tab stay."""
    from nicegui.events import GenericEventArguments

    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    venues.set_level("Neural Computation Letters", "journal", "Q2")
    with session_scope() as s:
        target = s.query(Venue).filter_by(name="Neural Computation").one().id
        other = s.query(Venue).filter_by(name="Neural Computation Letters").one().id
    await user.open("/venues?tab=journals")
    await user.should_see(marker="venue-list-filter-journals")
    (filt,) = user.find("venue-list-filter-journals").elements
    (table,) = [c for c in filt.parent_slot.children if isinstance(c, ui.table)]
    user.find("venue-list-filter-journals").type("Neural Computation")
    for listener in table._event_listeners.values():
        if listener.type == "venue_drop":
            args = GenericEventArguments(sender=table, client=table.client, args={})
            args.args.update(src=other, dst=target)
            with table.parent_slot:
                listener.handler(args)
    await user.should_see(marker="venue-merge-confirm")
    user.find("venue-merge-confirm").click()
    for _ in range(50):
        if not any(r["id"] == other for r in table.rows):
            break
        await asyncio.sleep(0.02)
    # The same table and filter box (not rebuilt), the search still there.
    assert not table.is_deleted and not filt.is_deleted
    assert filt.value == "Neural Computation"
    assert not any(r["id"] == other for r in table.rows)
    assert any(r["id"] == target for r in table.rows)
    await user.should_see(marker="venue-list-all-kinds")


async def test_venue_list_search_looks_in_every_kind(user: User) -> None:
    """A venue filed under another kind is still found by the search of a tab."""
    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    venues.set_level("Neural Computation Letters", "journal", "Q2")
    with session_scope() as s:
        journal = s.query(Venue).filter_by(name="Neural Computation Letters").one().id
    await user.open("/venues?tab=conferences")
    await user.should_see(marker="venue-list-filter-conferences")
    (filt,) = user.find("venue-list-filter-conferences").elements
    (table,) = [c for c in filt.parent_slot.children if isinstance(c, ui.table)]
    assert not any(r["id"] == journal for r in table.rows)
    await user.should_not_see(marker="venue-list-all-kinds")
    user.find("venue-list-filter-conferences").type("Neural Computation Letters")
    assert any(r["id"] == journal for r in table.rows)  # the journal, shown with its kind
    await user.should_see(marker="venue-list-all-kinds")
    user.find("venue-list-filter-conferences").clear()
    assert not any(r["id"] == journal for r in table.rows)


async def test_add_a_venue(user: User) -> None:
    from sci_report_analyzer.db.models import Venue
    from sci_report_analyzer.db.session import session_scope

    await user.open("/venues")
    user.find("venue-add").click()
    await user.should_see(marker="venue-add-name")
    user.find("venue-add-name").type("Workshop on Made-up Things")
    user.find("venue-add-short").type("WMT-X")
    user.find("venue-add-kind").elements.pop().value = "intl_workshop"
    user.find("venue-add-url").type("wmt.example.org")
    user.find("venue-add-confirm").click()
    with session_scope() as s:
        v = s.query(Venue).filter_by(name="Workshop on Made-up Things").one()
        assert (v.kind, v.kind_manual, v.short_name) == ("intl_workshop", True, "WMT-X")
        assert v.url == "https://wmt.example.org"
    await user.should_see("Workshop on Made-up Things")  # listed (and opened)
    # Edited in the venue's dialog.
    await user.should_see(marker="venue-url")
    url = [e for e in user.find("venue-url").elements if not e.is_deleted].pop()
    assert url.value == "https://wmt.example.org"
    url.value = "https://wmt.example.org/2026"
    user.find("venue-save").click()
    with session_scope() as s:
        assert s.get(Venue, v.id).url == "https://wmt.example.org/2026"


async def test_doi_record_on_hover(user: User) -> None:
    from pathlib import Path

    from sci_report_analyzer.db.models import DoiRecord, utcnow
    from sci_report_analyzer.db.session import session_scope
    from sci_report_analyzer.sources import doi

    d = "10.1145/3404835.3462812"
    msg = json.loads((Path(__file__).parent / "fixtures" / "doi_crossref_sigir.json").read_text())
    with session_scope() as s:
        s.add(
            DoiRecord(
                doi=d,
                status="ok",
                origin="crossref",
                data=doi.parse(msg, "crossref"),
                raw=msg,
                fetched_at=utcnow(),
            )
        )
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "h",
        [pub("h1", "Pretrained transformers", 2021, "SIGIR", doi=d, authors=["Jane Doe"])],
    )
    pub_id = _pub_id("Pretrained transformers")
    await user.open(f"/person/{pid}")
    await user.should_see("Pretrained transformers")
    user.find(f"pub-{pub_id}").click()
    await user.should_see(marker=f"doi-link-{d}")
    await user.should_see("Andrew Yates")  # the record's authors, in the link's tooltip


async def test_orcid_suggestion_and_candidate_colours(user: User) -> None:
    from sci_report_analyzer.db.models import Person, SourceLink
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    mine = "0000-0002-1825-0097"
    with session_scope() as s:
        s.add(
            SourceLink(
                person_id=pid,
                source="hal",
                external_id="v",
                display_name="Jane Doe",
                evidence={"orcid": mine},
                score=1,
                status="validated",
            )
        )
        s.add(
            SourceLink(
                person_id=pid,
                source="dblp",
                external_id="c",
                display_name="Jane Doe",
                evidence={"orcid": mine},
                score=1,
                status="candidate",
            )
        )
        cand = s.query(SourceLink).filter_by(external_id="c").one().id
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see(marker="orcid-suggestion")
    user.find("orcid-use").click()
    await user.should_not_see(marker="orcid-suggestion")  # refreshed
    with session_scope() as s:
        assert s.get(Person, pid).orcid == mine
    await user.should_see(marker=f"candidate-orcid-{cand}")
    badges = [b for b in user.find(f"candidate-orcid-{cand}").elements if not b.is_deleted]
    badge = badges.pop()
    assert badge.props["color"] == "positive" and "✓" in badge.text


async def test_candidate_with_another_orcid_than_the_validated_profiles(user: User) -> None:
    from sci_report_analyzer.db.models import SourceLink
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")  # (no ORCID set on the person)
    with session_scope() as s:
        for ext, status, orcid in (
            ("v", "validated", "0000-0002-1825-0097"),
            ("c", "candidate", "0000-0001-0000-0001"),
        ):
            s.add(
                SourceLink(
                    person_id=pid,
                    source="hal" if status == "validated" else "dblp",
                    external_id=ext,
                    display_name="Jane Doe",
                    evidence={"orcid": orcid},
                    score=1,
                    status=status,
                )
            )
        cand = s.query(SourceLink).filter_by(person_id=pid, external_id="c").one().id
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see(marker=f"candidate-orcid-{cand}")
    badge = next(b for b in user.find(f"candidate-orcid-{cand}").elements if not b.is_deleted)
    assert badge.props["color"] == "negative" and "≠" in badge.text
    await user.should_see(marker=f"candidate-probe-{cand}")


async def test_venue_search_and_merge(user: User) -> None:
    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")  # links publications to venues
    venues.set_level("Neural Computation Letters", "journal", "Q2")
    with session_scope() as s:
        target = s.query(Venue).filter_by(name="Neural Computation").one().id
        other = s.query(Venue).filter_by(name="Neural Computation Letters").one().id
    await user.open(f"/venues?focus={target}")
    user.find("venue-tab-merge").click()
    user.find("venue-merge-search").type("letters")
    await user.should_see(marker="venue-merge-pick-0")
    user.find("venue-merge-pick-0").elements.pop().value = True
    user.find("venue-merge-selected").click()
    await user.should_see(marker="venue-merge-confirm")
    user.find("venue-merge-confirm").click()
    await user.should_see("Test a venue string")
    with session_scope() as s:
        assert s.get(Venue, other) is None
    # The venue's dialog stays, updated (the merged venue gone), on the same tab.
    for _ in range(50):
        names = [e.value for e in user.find("venue-name").elements if not e.is_deleted]
        if len(names) == 1:
            break
        await asyncio.sleep(0.02)
    assert names == ["Neural Computation"]
    await user.should_see(marker="venue-merge-search")
    await user.should_not_see(marker="venue-merge-pick-0")


async def test_purge_person_needs_the_word(user: User, monkeypatch) -> None:
    from sci_report_analyzer import sync

    started = []

    async def fake_sync(pid, **kw):
        started.append(pid)

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    monkeypatch.setattr(sync, "sync_person", fake_sync)
    monkeypatch.setattr(sync, "_running", {})  # syncs left by other tests (same person ids)
    user.find("purge-person").click()
    await user.should_see(marker="purge-confirm")
    user.find("purge-confirm").click()  # not confirmed yet
    assert _pub_id("Deep ranking for search") is not None and not started
    user.find("purge-word").type("PURGE")
    user.find("purge-confirm").click()
    await user.should_see("Papers purged")
    for _ in range(40):  # the re-sync runs in a background task
        if started:
            break
        await asyncio.sleep(0.05)
    assert _pub_id("Deep ranking for search") is None and started == [pid]


async def test_venue_opens_in_place_with_merge_suggestions(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "jd",
        [
            pub("a", "Paper A", 2020, "Workshop on Things", authors=["Jane Doe"]),
            pub("b", "Paper B", 2020, "Intl. Workshop on Things", authors=["Jane Doe"]),
        ],
    )
    a = _pub_id("Paper A")
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    user.find(f"pub-venue-link-{a}").click()  # the venue dialog, on the person's page
    await user.should_see(marker="venue-suggestions", retries=40)
    user.find("venue-suggest-merge-0").click()
    await user.should_see(marker="venue-merge-confirm")
    user.find("venue-merge-confirm").click()
    await user.should_see("Paper B")
    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    await asyncio.sleep(0.3)
    with session_scope() as s:
        va = s.get(Publication, a).venue_id
        vb = s.get(Publication, _pub_id("Paper B")).venue_id
    assert va == vb


WIDG = "Conference on Widget Processing (WIDG)"
WIDG_DEMO = "WIDG (Demonstration) Conference on Widget Processing (WIDG)"


async def test_venue_searched_and_made_its_demo_track(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, Venue, VenueKey
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    papers = [pub("a", "Paper A", 2023, WIDG, authors=["Jane Doe"])]
    papers.append(pub("d", "Paper D", 2023, WIDG_DEMO, authors=["Jane Doe"]))
    add_source(pid, "hal", "jd", papers)
    await user.open(f"/person/{pid}")
    await user.should_see("Paper D")
    with session_scope() as s:
        demo = s.get(Publication, _pub_id("Paper D")).venue_id
        main = s.get(Publication, _pub_id("Paper A")).venue_id
    await user.open(f"/venues?focus={demo}")
    user.find("venue-tab-merge").click()
    user.find("venue-merge-search").type("widget")
    await user.should_see(marker="venue-merge-relate-0")
    user.find("venue-merge-relate-0").click()
    await user.should_see(marker="venue-relate-choice")
    (select,) = user.find("venue-relate-choice").elements
    assert select.value == "~track:demo"  # guessed from "(Demonstration)"
    user.find("venue-relate-ok").click()
    for _ in range(50):
        with session_scope() as s:
            if s.get(Venue, demo) is None:
                break
        await asyncio.sleep(0.02)
    with session_scope() as s:
        assert s.get(Venue, demo) is None
        assert s.get(Publication, _pub_id("Paper D")).venue_id == main
        tracks = {k.example: k.track for k in s.query(VenueKey).filter_by(venue_id=main)}
    assert tracks[WIDG_DEMO] == "demo" and tracks[WIDG] is None


async def test_venue_marked_as_a_demo_track(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, Venue, VenueKey
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    add_source(pid, "hal", "jd", [pub("d", "Paper D", 2023, WIDG_DEMO, authors=["Jane Doe"])])
    await user.open(f"/person/{pid}")
    await user.should_see("Paper D")
    with session_scope() as s:
        demo = s.get(Publication, _pub_id("Paper D")).venue_id
    await user.open(f"/venues?focus={demo}")
    user.find("venue-tab-merge").click()
    await user.should_see(marker="venue-as-track")
    (chip,) = user.find("venue-as-track-choice").elements
    assert chip.value == "demo" and chip.text == "Demo"
    assert "#8a6fd0" in chip.style.get("background", "")  # the demo flag's colour
    user.find("venue-as-track").click()
    await user.should_see(marker="venue-as-track-name")
    (name,) = user.find("venue-as-track-name").elements
    assert name.value == WIDG  # its track part removed (to edit)
    user.find("venue-as-track-ok").click()
    await user.should_see("Marked as the Demo track")
    with session_scope() as s:
        assert s.get(Venue, demo).name == WIDG
        assert {k.track for k in s.query(VenueKey).filter_by(venue_id=demo)} == {"demo"}


async def test_variant_track_as_a_coloured_chip(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, VenueKey
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    add_source(pid, "hal", "jd", [pub("a", "Paper A", 2023, WIDG, authors=["Jane Doe"])])
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    with session_scope() as s:
        vid = s.get(Publication, _pub_id("Paper A")).venue_id
        (key,) = [k.key for k in s.query(VenueKey).filter_by(venue_id=vid)]
    await user.open(f"/venues?focus={vid}")
    user.find("venue-tab-matching").click()
    mark = f"venue-variant-track-{key}"
    await user.should_see(marker=mark)
    (chip,) = user.find(mark).elements
    assert chip.text == "no track" and "dashed" in chip.style.get("border", "")  # discreet
    user.find(f"{mark}-tutorial").click()
    await user.should_see("Track saved")
    assert chip.text == "Tutorial" and "#1a7f37" in chip.style["background"]
    with session_scope() as s:
        assert s.get(VenueKey, key).track == "tutorial"


async def test_venue_suggestion_as_a_track(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "jd",
        [
            pub("a", "Paper A", 2023, "Conference on Things (THINGS)", authors=["Jane Doe"]),
            pub("b", "Paper B", 2023, "Findings of Things: THINGS", authors=["Jane Doe"]),
        ],
    )
    a = _pub_id("Paper A")
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    user.find(f"pub-venue-link-{a}").click()
    await user.should_see(marker="venue-suggestions", retries=40)
    (select,) = user.find("venue-suggest-relation-0").elements
    assert select.value == "track:findings"
    user.find("venue-suggest-merge-0").click()
    await user.should_see(marker="venue-relate-confirm")
    user.find("venue-relate-confirm").click()
    await user.should_see("Paper B")
    from sci_report_analyzer.db.models import Publication, VenueKey
    from sci_report_analyzer.db.session import session_scope

    await asyncio.sleep(0.3)
    with session_scope() as s:
        va = s.get(Publication, a).venue_id
        assert s.get(Publication, _pub_id("Paper B")).venue_id == va
        assert {k.track for k in s.query(VenueKey).filter_by(venue_id=va)} == {None, "findings"}


async def test_propose_merges_with_a_relation(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "jd",
        [
            pub("a", "Paper A", 2023, "Conference on Things and Stuff", authors=["Jane Doe"]),
            pub(
                "b",
                "Paper B",
                2022,
                "Findings of the Conference on Things and Stuff",
                authors=["Jane Doe"],
            ),
            pub(
                "c",
                "Paper C",
                2023,
                "Findings of the Conference on Things and Stuff",
                authors=["Jane Doe"],
            ),
        ],
    )
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    await user.open("/venues")
    await user.should_see(marker="venue-propose-merges")
    user.find("venue-propose-merges").click()
    await user.should_see("Proposal 1 of 1")
    # The Findings venue has more papers, but the conference is the primary venue.
    from sci_report_analyzer.db.models import Publication, VenueKey
    from sci_report_analyzer.db.session import session_scope

    with session_scope() as s:
        va = s.get(Publication, _pub_id("Paper A")).venue_id
    await user.should_see(marker=f"proposal-primary-{va}")
    (select,) = user.find("proposal-relation").elements
    assert select.value == "track:findings"  # guessed: no merge fields, what it does instead
    await user.should_see(marker="proposal-relation-effect")
    await user.should_not_see(marker="proposal-name")
    user.find("proposal-merge").click()
    await user.should_see("No merge to propose.")
    with session_scope() as s:
        assert s.get(Publication, _pub_id("Paper C")).venue_id == va
        assert {k.track for k in s.query(VenueKey).filter_by(venue_id=va)} == {None, "findings"}


async def test_resolve_conflicts_one_at_a_time(user: User) -> None:
    from sci_report_analyzer import venues
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "jd",
        [
            pub("a", "Paper A", 2020, "Workshop on Things 2020", authors=["Jane Doe"]),
            pub("b", "Paper B", 2020, "Venue One", authors=["Jane Doe"]),
            pub("c", "Paper C", 2020, "Venue Two", authors=["Jane Doe"]),
        ],
    )
    b, c = _pub_id("Paper B"), _pub_id("Paper C")
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    with session_scope() as s:
        one, two = s.get(Publication, b).venue_id, s.get(Publication, c).venue_id
    venues.save_patterns(one, [VenuePattern(pattern="Things")])
    venues.save_patterns(two, [VenuePattern(pattern="Workshop")])
    await user.open("/venues")
    user.find("venue-conflicts").click()
    await user.should_see("Problem 1 of 1")
    user.find(f"conflict-keep-{two}").click()
    await user.should_see("No conflict left.")
    assert venues.conflicts() == []


async def test_data_dir_setting(user: User, tmp_path) -> None:
    from sci_report_analyzer import config

    await user.open("/settings?tab=data")
    await user.should_see(str(config.DATA_DIR))
    dest = tmp_path / "moved"
    user.find("data-dir-input").elements.pop().value = str(dest)
    user.find("data-dir-change").click()
    await user.should_see(marker="data-dir-confirm")
    user.find("data-dir-confirm").click()
    await user.should_see(f"From the next start: {dest.resolve()}")
    assert (dest / "sci-report-analyzer.sqlite").exists()


async def test_venue_papers_and_person_filters(user: User) -> None:
    from sci_report_analyzer import venues
    from sci_report_analyzer.annotations import set_rank_override
    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope
    from sci_report_analyzer.ui import venues_page

    pid = _seed()
    pub_id = _pub_id("A workshop contribution")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    with session_scope() as s:
        vid = s.get(Publication, pub_id).venue_id
    row = next(r for r in await venues.venue_rows() if r.id == vid)
    assert row.pub_ids == {pub_id}
    await user.open("/venues")
    with user.client:
        venues_page.papers_dialog(row)
    await user.should_see(marker=f"venue-paper-{pub_id}")
    await user.should_see(marker=f"venue-person-{pid}")

    # The person's papers at that venue, and a paper opened from its link.
    await user.open(f"/person/{pid}?venue={vid}")
    await user.should_see(marker="venue-filter")
    await user.should_see("A workshop contribution")
    await user.should_not_see("Deep ranking for search")
    await user.open(f"/person/{pid}?pub={pub_id}")
    await user.should_see("Venue matching")

    # Papers decided by hand.
    set_rank_override(pub_id, {"type": "conference", "rank": "B"}, "checked")
    await user.open(f"/person/{pid}?manual=1")
    await user.should_see(marker="manual-only")
    await user.should_see("A workshop contribution")
    await user.should_not_see("Deep ranking for search")


async def test_workshop_main_conference_editor(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    with session_scope() as s:
        wid = s.scalar(
            select(Publication.venue_id).where(Publication.title == "A workshop contribution")
        )
        host = s.scalar(
            select(Publication.venue_id).where(Publication.title == "Deep ranking for search")
        )
    await user.open(f"/venues?focus={wid}")
    await user.should_see("Main conference")
    user.find("venue-host-add").click()
    await user.should_see(marker="venue-host-select-0")
    user.find("venue-host-select-0").elements.pop().value = host
    user.find("venue-hosts-save").click()
    with session_scope() as s:
        assert s.get(Venue, wid).hosts == [{"venue_id": host, "from": None, "to": None}]
        wname, hname = s.get(Venue, wid).name, s.get(Venue, host).name

    # The main conference opens in place of the workshop, which Back reopens.
    await user.open(f"/venues?focus={wid}")
    await user.should_see(marker="venue-host-open-0")
    user.find("venue-host-open-0").click()
    await user.should_see(marker="venue-back")
    assert user.find("venue-name").elements.pop().value == hname
    user.find("venue-back").click()
    await user.should_see(marker="venue-host-open-0")
    await user.should_not_see(marker="venue-back")
    assert user.find("venue-name").elements.pop().value == wname

    # A main conference not in the list is added from there, and picked.
    user.find("venue-host-new").click()
    await user.should_see(marker="venue-add-name")
    user.find("venue-add-name").type("Symposium on Invented Venues (SIV)")
    user.find("venue-add-short").type("SIV")
    user.find("venue-add-confirm").click()
    await user.should_see(marker="venue-host-select-1")
    with session_scope() as s:
        siv = s.scalar(select(Venue.id).where(Venue.short_name == "SIV"))
    select_ = user.find("venue-host-select-1").elements.pop()
    assert select_.value == siv
    assert select_.options[siv] == "[SIV] Symposium on Invented Venues (SIV)"


async def test_doi_by_hand_and_sources_tab(user: User) -> None:
    from sci_report_analyzer.db.models import Publication, SourceLink
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    await user.should_see(marker="override-doi")
    user.find("override-doi").elements.pop().value = "https://doi.org/10.1234/ABC"
    user.find("override-doi-save").click()
    await user.should_see("DOI saved", retries=40)
    with session_scope() as s:
        assert s.get(Publication, pub_id).doi_manual == "10.1234/abc"
        assert any(ln.source == "doi" for ln in s.query(SourceLink).filter_by(person_id=pid))
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see("DOI")


async def test_publication_sources_setting(user: User) -> None:
    from sci_report_analyzer import source_settings

    await user.open("/settings?tab=sources")
    await user.should_see("Publication sources")
    user.find("use-source-scholar").elements.pop().value = False
    user.find("use-sources-save").click()
    assert source_settings.disabled() == {"scholar"}


async def test_add_a_publication_by_hand(user: User, monkeypatch) -> None:
    from test_doi import SIGIR, _registry

    _registry(monkeypatch)
    pid = _seed()
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find("add-publication").click()
    await user.should_see(marker="add-publication-ref")
    user.find("add-publication-ref").type("not a doi")
    user.find("add-publication-ok").click()
    await user.should_see("Not a DOI nor a HAL document")
    user.find("add-publication-ref").clear().type(f"https://doi.org/{SIGIR}")
    user.find("add-publication-ok").click()
    await user.should_see("Pretrained Transformers for Text Ranking", retries=40)
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see(marker="added-publications")
    user.find(f"remove-added-{SIGIR}").click()
    await user.should_not_see(marker="added-publications", retries=40)


async def test_theses_follow_the_period(user: User) -> None:
    from dataclasses import replace

    from sci_report_analyzer import annotations

    pid = make_person()
    add_source(
        pid,
        "thesesfr",
        "123456789",
        theses=[
            replace(
                thesis("a", "director", "Old thesis"),
                defence_date="2015-06-01",
                status="soutenue",
            ),
            replace(
                thesis("b", "director", "Recent thesis"),
                start_date="2022-10-01",
                status="enCours",
            ),
            replace(
                thesis("c", "rapporteur", "Reviewed thesis"),
                defence_date="2023-05-01",
                status="soutenue",
            ),
        ],
    )
    period = annotations.save_period(pid, "Recent", 2021, 2026)
    await user.open(f"/person/{pid}/{period}?tab=theses")
    await user.should_see("2 of 3 theses in Recent (2021–2026)")
    await user.should_see("Supervised (1)")
    table = user.find("theses-director").elements.pop()
    assert [(r["start"], r["end"], r["estimated"]) for r in table.rows] == [
        ("2022-10-01", "", False)
    ]
    user.find("theses-all").click()
    await user.should_see("Supervised (2)")
    table = user.find("theses-director").elements.pop()
    assert [(r["start"], r["end"], r["estimated"]) for r in table.rows] == [
        ("2022-10-01", "", False),
        ("2012", "2015-06-01", True),
    ]


async def test_contribution_roles(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Led", 2015, "V", authors=["Jane Doe", "B", "C"], author_pos=1, num_authors=3),
            pub(
                "b",
                "Helped",
                2016,
                "V",
                authors=["A", "Jane Doe", "C", "D", "E", "F"],
                author_pos=2,
                num_authors=6,
            ),
            pub(
                "c",
                "Supervised",
                2024,
                "V",
                authors=["A", "B", "Jane Doe"],
                author_pos=3,
                num_authors=3,
            ),
        ],
    )
    await user.open(f"/person/{pid}")
    await user.should_see("Led")
    chart = next(
        c
        for c in user.find(ui.echart).elements
        if c.options["title"]["text"].startswith("Contribution role")
    )
    assert chart.options["title"]["text"] == "Contribution role (3) · first 33% · last 33%"
    # All, then by years (2015–2016, …, 2024).
    counts = {s["name"]: [d["count"] for d in s["data"]] for s in chart.options["series"]}
    assert [c[0] for c in counts.values()] == [1, 1, 1]
    assert counts["Last"][-1] == 1 and counts["First"][1] == 1
    assert list(counts) == ["First", "Contributor", "Last"]


async def test_contribution_settings(user: User) -> None:
    from sci_report_analyzer import contribution

    await user.open("/settings?tab=contribution")
    await user.should_see("Roles")
    n = len(contribution.default_config().rules)
    user.find("contribution-add-role").click()
    user.find("contribution-add-rule").click()
    await user.should_see(marker=f"contribution-condition-{n}")
    user.find(f"contribution-condition-{n}").type("p=2 and n>=3")
    user.find("contribution-save").click()
    cfg = contribution.load_config()
    assert cfg.roles[-1].label == "New role"
    assert cfg.rules[-1] == contribution.Rule("involved", "p=2 and n>=3")
    # An invalid condition is not saved.
    user.find(f"contribution-condition-{n}").type(" and")
    user.find("contribution-save").click()
    await user.should_see("Rule 8: Incomplete condition")
    assert contribution.load_config() == cfg


async def test_propose_merges_one_at_a_time(user: User) -> None:
    from sci_report_analyzer import venues
    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    add_source(
        pid,
        "hal",
        "jd",
        [
            pub("a", "Paper A", 2020, "Workshop on Things", authors=["Jane Doe"]),
            pub("b", "Paper B", 2020, "Intl. Workshop on Things", authors=["Jane Doe"]),
            pub("c", "Paper C", 2021, "Workshop on Things", authors=["Jane Doe"]),
            pub("d", "Paper D", 2020, "Journal of Stuff", authors=["Jane Doe"]),
            pub("e", "Paper E", 2020, "Journal of Stuff Letters", authors=["Jane Doe"]),
        ],
    )
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    with session_scope() as s:
        keep, other, d, e = (
            s.get(Publication, _pub_id(t)).venue_id
            for t in ("Paper A", "Paper B", "Paper D", "Paper E")
        )
    await user.open("/venues")
    await user.should_see(marker="venue-propose-merges")
    user.find("venue-propose-merges").click()
    await user.should_see("Proposal 1 of 2")
    # The pair whose venue has the most papers is proposed first, that venue kept.
    await user.should_see(marker=f"proposal-primary-{keep}")
    assert user.find("proposal-name").elements.pop().value == "Workshop on Things"
    user.find(f"proposal-make-primary-{other}").click()  # the other way round...
    await user.should_see(marker=f"proposal-primary-{other}")
    assert user.find("proposal-name").elements.pop().value == "Intl. Workshop on Things"
    user.find(f"proposal-make-primary-{keep}").click()  # ... and back
    await user.should_see(marker=f"proposal-primary-{keep}")
    user.find("proposal-use-name-1").click()  # the other venue's name, to edit
    assert user.find("proposal-name").elements.pop().value == "Intl. Workshop on Things"
    user.find("proposal-name").clear().type("Things Workshop")
    user.find("proposal-short").type("WoT")
    await user.should_see(marker=f"proposal-kind-{keep}")  # the type of each venue
    await user.should_not_see(marker="proposal-kinds-differ")
    user.find("proposal-kind").elements.pop().value = "natl_workshop"
    user.find("proposal-merge").click()
    await user.should_see("Proposal 1 of 1")
    user.find("proposal-not-same").click()
    await user.should_see("No merge to propose.")
    from sci_report_analyzer.db.models import Venue

    with session_scope() as s:
        assert s.get(Publication, _pub_id("Paper B")).venue_id == keep
        assert s.get(Venue, keep).name == "Things Workshop"
        assert s.get(Venue, keep).short_name == "WoT"
        assert (s.get(Venue, keep).kind, s.get(Venue, keep).kind_manual) == ("natl_workshop", True)
    assert venues.not_same_pairs() == {(min(d, e), max(d, e))}
    assert other != keep


async def test_joint_venue_with_different_levels(user: User) -> None:
    from helpers import add_source, make_person, pub
    from sqlalchemy import select

    from sci_report_analyzer import pubview, venues
    from sci_report_analyzer.db.models import Publication, Venue
    from sci_report_analyzer.db.session import session_scope

    pid = make_person()
    add_source(
        pid,
        "hal",
        "h",
        [
            pub("a", "Alpha paper", 2023, "Conférence Alpha (ALPHA)"),
            pub("b", "Beta paper", 2023, "Conférence Beta (BETA)"),
            pub("j", "Joint paper", 2023, "ALPHA-BETA 2023 Conférence commune"),
        ],
    )
    await pubview.load_stats(pid)
    with session_scope() as s:
        ids = {
            t: s.scalar(select(Publication.venue_id).where(Publication.title == t))
            for t in ("Alpha paper", "Beta paper", "Joint paper")
        }
    alpha, beta, joint = ids.values()
    for vid, rank in ((alpha, "B"), (beta, "A")):
        venues.update_venue(vid, kind="natl_conference", level_type="conference", level_rank=rank)

    await user.open("/venues?tab=joint")
    await user.should_see(marker=f"joint-{joint}")
    user.find(f"joint-use-{joint}-{beta}").click()
    with session_scope() as s:
        assert s.get(Venue, joint).joint["use"] == beta

    await user.open(f"/venues?focus={joint}")
    await user.should_see("Joint venue")
    await user.should_see("its level is used")
    await user.should_not_see(marker="venue-tab-joint")  # decided
    await user.should_see(marker=f"venue-part-open-{alpha}")  # its conferences can be opened
    user.find(f"venue-part-use-{alpha}").click()
    with session_scope() as s:
        assert s.get(Venue, joint).joint["use"] == alpha


async def test_problems_on_folder_cards(user: User) -> None:
    from helpers import add_source, make_person, pub

    from sci_report_analyzer import folders, pubview

    pid = make_person()
    # Without an author list, every paper has a problem.
    add_source(
        pid,
        "dblp",
        "x",
        [
            pub("a", "In the period", 2021, "Unknown venue xyz"),
            pub("b", "Before the period", 2018, "Unknown venue xyz"),
        ],
    )
    fid = folders.save_folder(None, "Hiring committee")
    folders.add_person(fid, pid, 2020, 2024)
    await user.open(f"/?folder={fid}")
    await user.should_see("Jane Doe")
    await user.should_not_see(marker=f"problems-{pid}")  # not computed yet
    stats = await pubview.load_stats(pid)
    assert all(s.problems for s in stats)
    assert pubview.problem_years([pid]) == {pid: [2018, 2021]}
    assert pubview.count_in_period([2019, 2021, None], 2020, 2024) == 2  # no year: in
    await user.open(f"/?folder={fid}")
    await user.should_see(marker=f"problems-{pid}", content="1 problem(s)")
    await user.should_see(marker="folder-problems")


async def test_paper_tags_and_notes(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer import annotations
    from sci_report_analyzer.db.models import PeriodNote, PublicationTag
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    period = annotations.save_period(pid, "Recent", 2020, 2024)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    user.find("tab-notes").click()
    # A new global tag, typed on the paper.
    new = user.find("paper-new-tag").elements.pop()
    new.value = "to read"
    new.run_method("blur")
    user.find("paper-new-tag").trigger("keydown.enter")
    with session_scope() as s:
        [row] = s.scalars(select(PublicationTag)).all()
        assert row.publication_id == pub_id
    # A note within the period.
    await user.should_see("period-note")
    user.find("period-note").elements.pop().value = "good *fit*"
    await note_saved(user, "period-note")
    with session_scope() as s:
        assert s.get(PeriodNote, (period, pub_id)).text == "good *fit*"
    await user.should_see(f"has-note-{pub_id}")


async def test_tag_from_a_pasted_list(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer import annotations
    from sci_report_analyzer.db.models import PeriodTag
    from sci_report_analyzer.db.session import session_scope

    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")
    period = annotations.save_period(pid, "Report", 2018, 2024)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see("Deep ranking for search")
    user.find("tag-from-list").click()
    await user.should_see("reflist-text")
    user.find("reflist-text").elements.pop().value = (
        "1- A workshop contribution. Al, J. Doe. Some Workshop, 2019.\n"
        "2- Deep ranking for search. J. Doe, Bob. Neural Computation, 2021.\n"
        "3- Not one of hers. J. Doe. 2020. Lien : https://hal.science/hal-04000001\n"
    )
    user.find("reflist-find").click()
    await user.should_see("2 of 3 items matched")
    await user.should_see("reflist-add-2")  # the unmatched item, with its HAL id
    user.find("reflist-apply").click()
    await user.should_see("“starred” put on 2 papers")
    with session_scope() as s:
        rows = {r.publication_id: r.number for r in s.scalars(select(PeriodTag))}
    assert rows[pub_id] == 2 and sorted(rows.values()) == [1, 2]
    # The panel shows the starred papers, in the list's order.
    await user.should_see("In the order of the list")


async def test_tag_from_a_list_outside_the_period(user: User) -> None:
    """Papers of the list outside the period's years: tagged, and said not shown."""
    from test_reflist import REPORT, REPORT_PAPERS

    from sci_report_analyzer import annotations

    pid = make_person("Jane Doe")
    papers = REPORT_PAPERS
    add_source(
        pid,
        "dblp",
        "a/1",
        [pub(f"p{i}", t, y, "V", doi=r) for i, (t, r, y) in enumerate(papers) if "/" in r],
    )
    add_source(pid, "hal", "idhal:jd", [pub(r, t, y, "V") for t, r, y in papers if "/" not in r])
    period = annotations.save_period(pid, "Report", 2017, 2024)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see(papers[0][0])
    user.find("tag-from-list").click()
    await user.should_see("reflist-text")
    user.find("reflist-text").elements.pop().value = REPORT
    user.find("reflist-find").click()
    await user.should_see("10 of 10 items matched")
    user.find("reflist-apply").click()
    await user.should_see("“starred” put on 10 papers (not shown: 3 outside the years 2017–2024)")
    await user.should_see("In the order of the list")
    await user.should_see("7 publications")
    for title, _, year in papers:
        if 2017 <= year <= 2024:
            await user.should_see(title)
        else:
            await user.should_not_see(title)
    await user.should_see("Not shown: 3 outside the years 2017–2024")
    user.find("list-all-years").click()
    await user.should_see("10 publications")
    for title, _, _ in papers:
        await user.should_see(title)
    await user.should_not_see(marker="list-not-shown")


async def test_author_lookup_links(user: User) -> None:
    pid = _seed()
    pub_id = _pub_id("Deep ranking for search")  # authors: Jane Doe, Bob
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    user.find(f"pub-{pub_id}").click()
    await user.should_see(marker="author-1")
    user.find("author-1").click()
    await user.should_see(marker="lookup-Semantic Scholar")
    [web] = user.find(marker="lookup-Web").elements
    assert web.props["href"].endswith("%22Bob%22+%22Deep+ranking+for+search%22")
    assert web.props["target"] == "_blank"


async def test_venue_core_history(user: User) -> None:
    from sqlalchemy import select

    from sci_report_analyzer.db.models import Publication
    from sci_report_analyzer.db.session import session_scope
    from sci_report_analyzer.ranking.badge import core_periods

    assert core_periods({"CORE2018": "B", "CORE2023": "A", "ICORE2026": "A"}) == [
        (2018, 2018, "B"),
        (2023, 2026, "A"),
    ]
    pid = make_person("Jane Doe")
    add_source(
        pid, "dblp", "d/9", [pub("a", "Timely paper", 2019, "Symposium on Timely Rankings (STR)")]
    )
    await user.open(f"/person/{pid}")  # (venues are resolved there)
    await user.should_see("Timely paper")
    with session_scope() as s:
        vid = s.scalar(select(Publication.venue_id).where(Publication.title == "Timely paper"))
    await user.open(f"/venues?focus={vid}")
    await user.should_see(marker="venue-core-history")
    await user.should_see("(2023–2026)")
    await user.should_see("(2018)")


async def test_track_in_validation_and_kind(user: User) -> None:
    from sci_report_analyzer import venues
    from sci_report_analyzer.ranking.service import VenuePattern

    pid = make_person("Jane Doe")
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2020, "ACL", doi="10.1/x")])
    add_source(pid, "dblp", "d", [pub("b", "Paper A", 2020, "ACL Demos", doi="10.1/x")])
    add_source(pid, "hal", "h2", [pub("c", "Paper B", 2020, "ACL Demos", doi="10.1/y")])
    add_source(pid, "hal", "h3", [pub("e", "Paper C", 2020, "ACL", doi="10.1/z")])
    add_source(pid, "dblp", "d3", [pub("f", "Paper C", 2020, "ACL Findings", doi="10.1/z")])
    add_source(pid, "dblp", "d2", [pub("d", "Paper B", 2020, "ACL Findings", doi="10.1/y")])
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    acl = next(v for v, n in venues.venue_names().items() if n == "ACL")
    venues.save_patterns(
        acl,
        [
            VenuePattern(pattern="^ACL Demos$", track="demo"),
            VenuePattern(pattern="^ACL Findings$", track="findings"),
        ],
    )
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    # Demo or no track: the demo wins, as the kind step says.
    user.find(f"pub-{_pub_id('Paper A')}").click()
    user.find("tab-matching").click()
    await user.should_see("Demo track: given by DBLP; it wins over HAL")
    # Likewise the Findings.
    await user.open(f"/person/{pid}")
    await user.should_see("Paper C")
    user.find(f"pub-{_pub_id('Paper C')}").click()
    user.find("tab-matching").click()
    await user.should_see("Findings track: given by DBLP; it wins over HAL")
    # Two tracks: a choice, whose confirmation names the track.
    await user.open(f"/person/{pid}")
    await user.should_see("Paper B")
    user.find(f"pub-{_pub_id('Paper B')}").click()
    user.find("tab-matching").click()
    await user.should_see(marker=f"pick-venue-{acl}-findings")
    user.find(f"pick-venue-{acl}-findings").elements.pop().value = True
    user.find("validate-source").click()
    await user.should_see("the venue from DBLP, Findings track")


async def test_main_conference_of_the_workshop_is_minor(user: User) -> None:
    from sci_report_analyzer.db.models import SourcePub, Venue, VenueText
    from sci_report_analyzer.db.session import session_scope

    pid = make_person("Jane Doe")
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2024, "Workshop on Things", doi="10.1/w")])
    add_source(
        pid, "doi", "d", [pub("b", "Paper A", 2024, "Symposium on Timely Rankings", doi="10.1/w")]
    )
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    with session_scope() as s:
        vid = {t.raw: t.venue_id for t in s.query(VenueText)}
        ws, host = vid["Workshop on Things"], vid["Symposium on Timely Rankings"]
        s.get(Venue, ws).hosts = [{"venue_id": host, "from": None, "to": None}]
        assert s.query(SourcePub).count() == 2
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    user.find(f"pub-{_pub_id('Paper A')}").click()
    user.find("tab-matching").click()
    await user.should_see(marker=f"group-minor-{host}")
    await user.should_see("the workshop rather than its main conference")
    await user.should_not_see("The sources give different venues")


async def test_possible_author_discarded_from_the_warning(user: User) -> None:
    pid = make_person("Jane Doe")
    add_source(pid, "hal", "h", [pub("a", "Paper A", 2020, "ACL", authors=["J. Doe", "Bob"])])
    await user.open(f"/person/{pid}")
    await user.should_see("Paper A")
    user.find(f"pub-{_pub_id('Paper A')}").click()
    await user.should_see("Is the author “J. Doe” Jane Doe? (similar name)")
    user.find("possible-no-0").click()
    await user.should_see("Won't be suggested again")
    await user.should_see("the person's name is not found among the authors")
    await user.should_not_see(marker="possible-no-0")


async def test_primary_source_filters_the_panel(user: User) -> None:
    from sci_report_analyzer import folders, source_settings

    pid = _seed()  # DBLP only
    add_source(
        pid, "hal", "idhal:jd", [pub("h", "Deep ranking for search", 2021, "Neural Computation")]
    )
    await user.open("/settings")
    user.find("primary-source").elements.pop().value = "hal"
    assert source_settings.default_primary() == "hal"
    await user.open(f"/person/{pid}")
    await user.should_see("Deep ranking for search")
    await user.should_not_see("A workshop contribution")  # not in HAL: not counted
    user.find("show-outside").click()
    await user.should_see("A workshop contribution")
    await user.should_not_see("Deep ranking for search")
    # A folder without a primary source counts everything.
    fid = folders.save_folder(None, "Committee", primary_source=source_settings.NO_PRIMARY)
    period = folders.add_person(fid, pid)
    await user.open(f"/person/{pid}/{period}")
    await user.should_see("A workshop contribution")
    await user.should_see("Deep ranking for search")
