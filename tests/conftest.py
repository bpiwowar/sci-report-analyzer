import contextlib
import functools
import gc
import json
import os
import shutil
import sys
import tempfile
import weakref
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="sci-report-analyzer-test-"))
os.environ["SCI_REPORT_ANALYZER_DATA"] = str(_TMP)
os.environ.setdefault("SCI_REPORT_ANALYZER_EMAIL", "test@example.org")

from helpers import load_fixture  # noqa: E402

from sci_report_analyzer import config  # noqa: E402
from sci_report_analyzer.db import session as db_session  # noqa: E402
from sci_report_analyzer.ranking import datasets  # noqa: E402
from sci_report_analyzer.ranking.service import service  # noqa: E402

pytest_plugins = ["nicegui.testing.user_plugin"]


def pytest_sessionfinish(session, exitstatus) -> None:
    """The session's data directory (ranking records, settings…) is removed at the end."""
    shutil.rmtree(_TMP, ignore_errors=True)
    _detach_dialog_finalizers()


def _detach_dialog_finalizers() -> None:
    """A NiceGUI dialog deletes itself once a canary element is collected; at exit, those of
    the test clients (deleted by then) would raise "The client this element belongs to has
    been deleted": their finalizers are detached."""
    for f in [o for o in gc.get_objects() if isinstance(o, weakref.finalize)]:
        info = f.peek()
        if info and info[1].__qualname__.startswith("Dialog.__init__"):
            f.detach()


def _write_datasets(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    records = load_fixture("golden_records.json.gz")
    journals = [r for r in records if r["type"] == "journal"]
    confs = [r for r in records if r["type"] == "conference"]
    # Ranks so that categories are exercised.
    for r in journals:
        r.setdefault("quartile", "Q2")
    for r in confs:
        r.setdefault("coreRank", "A")
    # A conference whose rank changed across CORE editions.
    confs.append(
        {
            "name": "Symposium on Timely Rankings",
            "source": "core",
            "type": "conference",
            "coreRank": "A",
            "coreEdition": "ICORE2026",
            "coreId": "9001",
            "aliases": ["STR"],
            "coreHistory": {"CORE2018": "B", "CORE2023": "A", "ICORE2026": "A"},
        }
    )
    (d / "journals.json").write_text(json.dumps(journals))
    (d / "conferences.json").write_text(json.dumps(confs))
    (d / "predatory.json").write_text(
        json.dumps(
            [
                {
                    "name": "Journal of Totally Legit Science",
                    "source": "predatory",
                    "type": "journal",
                    "predatory": True,
                }
            ]
        )
    )


# The test ranking records, instead of the ones shipped with the app.
datasets.BUNDLED_DIR = _TMP / "datasets"
_write_datasets(datasets.BUNDLED_DIR)
datasets.SOURCE_JOURNALS = datasets.BUNDLED_DIR / "journals.json"


def _make_template_db() -> Path:
    """An empty database brought to the head revision, once per session (migrating takes
    a second or two: each test gets a copy instead)."""
    db = _TMP / "template.sqlite"
    engine = db_session.init_engine(f"sqlite:///{db}")
    # The ranking records too, loaded now (no JCR record: an empty database), rather than by
    # the first test needing them, to be out of the garbage collector's way as well (below).
    _ = service.matcher, service.predatory
    with engine.connect() as conn:
        # Everything in the file itself (no -wal next to it), so that a copy is complete.
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
    engine.dispose()
    return db


_TEMPLATE_DB = _make_template_db()

# All of the app's modules, imported now (the UI tests' main file imports them anyway), as
# well as Alembic's (above): what is loaded by then stays for the whole session, so the
# garbage collector is told to leave it alone. NiceGUI's user fixture runs a full collection
# twice per test, which otherwise traverses those hundreds of thousands of objects each time.
import sci_report_analyzer.main  # noqa: E402, F401

gc.collect()
gc.freeze()


_APP_PACKAGE = "sci_report_analyzer"


def _app_modules() -> dict:
    return {
        k: m
        for k, m in sys.modules.items()
        if k == _APP_PACKAGE or k.startswith(f"{_APP_PACKAGE}.")
    }


@pytest.fixture(autouse=True)
def keep_app_modules():
    """The app's modules stay imported across tests.

    At teardown, NiceGUI's user fixture removes from ``sys.modules`` the module of each page
    (and its parent packages), so that a main file declaring pages at import time declares
    them again. Here the pages are declared by ``setup()``: they are put back instead of
    being imported again by the next test (which took time, and left a new copy of those
    modules, the package's ``__init__`` included, per test)."""
    before = _app_modules()
    yield
    for name, module in before.items():
        sys.modules.setdefault(name, module)


# How long ``should_see`` / ``should_not_see`` wait at least for the page to be as expected
# (NiceGUI's default: 3 tries, 0.1 s apart, which a busy machine misses now and then).
PATIENCE = 5.0


def _patient(method, interval: float):
    """``method`` (should_see or should_not_see, which try every ``interval`` seconds),
    trying for ``PATIENCE`` seconds at least: it still returns as soon as the page is as
    expected, so only what would have failed takes longer."""

    @functools.wraps(method)
    async def patient(*args, retries: int = 3, **kwargs):
        retries = max(retries, round(PATIENCE / interval))
        return await method(*args, retries=retries, **kwargs)

    return patient


@pytest.fixture
async def user(user):
    """NiceGUI's simulated user; its pages are deleted at the end of the test.

    NiceGUI's own fixture only forgets them (``Client.instances.clear()`` when the next test
    starts): a refreshable declared at module level (``person.sources_list``…) then kept every
    page of every earlier test as one of its targets, so that each refresh re-rendered them
    all (against the database of the current test), a little slower each time; and they
    were never freed, which made every full garbage collection slower too."""
    from nicegui import Client

    user.should_see = _patient(user.should_see, 0.1)
    user.should_not_see = _patient(user.should_not_see, 0.05)
    yield user
    for client in list(Client.instances.values()):
        with contextlib.suppress(KeyError):
            client.delete()


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own database (a copy of the migrated template); ranking data is
    shared (read-only)."""
    db = tmp_path / "test.sqlite"
    shutil.copyfile(_TEMPLATE_DB, db)
    url = f"sqlite:///{db}"
    monkeypatch.setenv("SCI_REPORT_ANALYZER_DB_URL", url)
    # Never touch the real saved data-directory location.
    monkeypatch.setattr(config, "LOCATION_FILE", tmp_path / "config" / "location.json")
    engine = db_session.init_engine(url)
    from sci_report_analyzer import source_settings

    source_settings.reset_cache()
    service.invalidate(clear_cache=False)
    service._cache = None
    # No network in unit tests: the OpenAlex venue fallback is disabled.
    from sci_report_analyzer.ranking.service import load_settings, save_settings

    st = load_settings()
    st.sources["openalex"] = False
    save_settings(st)
    # Nor DOI registries: DOIs are unknown unless a test says otherwise.
    from sci_report_analyzer.sources import doi

    async def no_doi(d, **kw):
        return "not_found", None, None, None

    async def no_batch(dois):
        return {}

    monkeypatch.setattr(doi, "fetch_record", no_doi)
    monkeypatch.setattr(doi, "fetch_crossref_batch", no_batch)
    # Stored PDFs: in the test's directory, and no open-access lookups.
    from sci_report_analyzer import pdfs

    async def no_unpaywall(d):
        return []

    monkeypatch.setattr(pdfs, "ROOT", tmp_path / "pdfs")
    monkeypatch.setattr(pdfs, "unpaywall", no_unpaywall)
    # Notes saved sooner once typing pauses (still after a tick: "Editing…" is seen).
    from sci_report_analyzer.ui import tags

    monkeypatch.setattr(tags, "NOTE_IDLE", 0.2)
    monkeypatch.setattr(tags, "NOTE_TICK", 0.05)
    yield
    # Their connections closed now (not whenever the garbage collector gets to the engines).
    for e in {engine, db_session._engine}:  # (a UI test's app makes its own)
        if e is not None:
            e.dispose()


@pytest.fixture
def fake_viewer(monkeypatch, tmp_path) -> None:
    """A stand-in for PDF.js (not downloaded in the tests): an empty viewer page."""
    from sci_report_analyzer import pdfs

    viewer = tmp_path / "pdfjs"
    (viewer / "web").mkdir(parents=True)
    (viewer / "web" / "viewer.html").write_text("<html></html>")
    monkeypatch.setattr(pdfs, "viewer_dir", lambda: viewer)
