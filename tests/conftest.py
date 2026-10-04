import gzip
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="sci-report-analyzer-test-"))
os.environ["SCI_REPORT_ANALYZER_DATA"] = str(_TMP)
os.environ.setdefault("SCI_REPORT_ANALYZER_EMAIL", "test@example.org")

from sci_report_analyzer import config  # noqa: E402
from sci_report_analyzer.db import session as db_session  # noqa: E402
from sci_report_analyzer.ranking import datasets  # noqa: E402
from sci_report_analyzer.ranking.service import service  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
pytest_plugins = ["nicegui.testing.user_plugin"]


def _write_datasets(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(FIXTURES / "golden_records.json.gz", "rt") as f:
        records = json.load(f)
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


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own database; ranking data is shared (read-only)."""
    url = f"sqlite:///{tmp_path / 'test.sqlite'}"
    monkeypatch.setenv("SCI_REPORT_ANALYZER_DB_URL", url)
    # Never touch the real saved data-directory location.
    monkeypatch.setattr(config, "LOCATION_FILE", tmp_path / "config" / "location.json")
    db_session.init_engine(url)
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
    yield
