import importlib.util
import json
from pathlib import Path

from sci_report_analyzer.ranking import datasets
from sci_report_analyzer.ranking.badge import badge_from_record, sjr_periods
from sci_report_analyzer.ranking.matcher import Matcher

_spec = importlib.util.spec_from_file_location(
    "build_datasets", Path(__file__).parent.parent / "scripts" / "build_datasets.py"
)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

HEADER = "Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index\n"
Y2024 = (
    HEADER + '1;900;"Conference on Machine Translation - Proceedings";conference and proceedings;'
    '"27680983";0,752;-;23\n'
    '2;1;"Journal of Imaginary Results";journal;"12345678";18,5;Q1;120\n'
)
Y2021 = (
    HEADER + '1;800;"Machine Translation";journal;"09226567, 15730573";0,5;Q2;40\n'
    '2;1;"Journal of Imaginary Results";journal;"12345678";17,0;Q2;110\n'
)


def test_scimago_years_merged(tmp_path):
    """A journal keeps its latest year's fields and its quartile each year; one dropped since
    comes back, with its last year."""
    (tmp_path / "scimagojr 2024.csv").write_text(Y2024)
    (tmp_path / "scimagojr 2021.csv").write_text(Y2021)
    recs = build.scimago(f"{tmp_path}/scimagojr 2021.csv,{tmp_path}/scimagojr 2024.csv")
    by_name = {r["name"]: r for r in recs}
    assert len(recs) == 3
    journal = by_name["Journal of Imaginary Results"]
    assert (journal["sjr"], journal["sjrYear"]) == (18.5, 2024)
    assert journal["sjrHistory"] == {"2021": "Q2", "2024": "Q1"}
    assert by_name["Machine Translation"]["sjrYear"] == 2021
    assert by_name["Machine Translation"]["issn"] == ["09226567", "15730573"]
    # The journal, not the WMT proceedings.
    m = Matcher().load(recs).match("Machine Translation", None, "journal")
    assert (m.record["name"], m.exact) == ("Machine Translation", True)
    # A paper gets the quartile of its year (else the closest one before, else the first).
    b = badge_from_record(journal, 1.0, True)
    assert (b.quartile, b.at_year(2023).quartile, b.at_year(2022).sjrYear) == ("Q1", "Q2", 2021)
    assert b.at_year(2010).quartile == "Q2" and b.at_year(2030) == b
    assert b.at_year(2022).extra["sjrLatest"] == [2024, "Q1"]
    assert sjr_periods(journal["sjrHistory"]) == [(2021, 2021, "Q2"), (2024, 2024, "Q1")]
    # Merged into the data there, which needs its year when it has none.
    base = [{**journal, "sjrYear": None}]
    base[0].pop("sjrYear"), base[0].pop("sjrHistory")
    again = build.scimago(f"{tmp_path}/scimagojr 2021.csv", base, 2024)
    assert again[0]["sjrHistory"] == {"2021": "Q2", "2024": "Q1"}
    assert again[0]["sjrYear"] == 2024


def test_scimago_imported_year(monkeypatch, tmp_path):
    """A year imported in the app is merged into the bundled journals on load."""
    from sci_report_analyzer import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    bundled = datasets.merge_scimago([], 2024, datasets.scimago_records(Y2024))
    (tmp_path / "journals.json").write_text(json.dumps(bundled))
    monkeypatch.setattr(datasets, "SOURCE_JOURNALS", tmp_path / "journals.json")
    assert datasets.scimago_year_in("scimagojr 2021.csv") == 2021
    assert datasets.import_scimago(Y2021, 2021) == 2
    assert datasets.import_scimago(Y2021.replace(";Q2;40", ";Q3;40"), 2019) == 2
    assert datasets.imported_scimago_years() == [2019, 2021]
    # (one file, each journal once)
    stored = json.loads(datasets.scimago_import_path().read_text())
    assert [j["name"] for j in stored["journals"]] == [
        "Machine Translation",
        "Journal of Imaginary Results",
    ]
    assert stored["journals"][0]["sjrHistory"] == {"2019": "Q3", "2021": "Q2"}
    journals = {r["name"]: r for r in datasets.load_journals()}
    assert journals["Machine Translation"]["sjrYear"] == 2021
    assert journals["Journal of Imaginary Results"]["sjrHistory"] == {
        "2019": "Q2",
        "2021": "Q2",
        "2024": "Q1",
    }
    assert journals["Journal of Imaginary Results"]["sjrYear"] == 2024  # (bundled: latest)
    assert datasets.scimago_years(list(journals.values())) == {2019, 2021, 2024}


def test_scimago_missing_years(monkeypatch):
    """A paper's year can be imported when published by Scimago and not loaded."""
    from sci_report_analyzer.ranking.service import RankingService

    svc = RankingService()
    monkeypatch.setattr(svc, "_matcher", Matcher())
    svc._sjr_years = {2021, 2024}
    assert svc.scimago_missing(2022)
    assert not svc.scimago_missing(2021) and not svc.scimago_missing(None)
    assert not svc.scimago_missing(1990) and not svc.scimago_missing(2025)
