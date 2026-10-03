"""Build the Scimago journals, in datasets/journals.json (not shipped with the app: it
downloads them from the repository on GitHub, so pushing a rebuild updates the apps).

    uv run python scripts/build_datasets.py --scimago="scimagojr 2024.csv,scimagojr 2021.csv"

- ``--scimago``: journals.json, the Scimago journal ranking. scimagojr.com blocks scripts
  (Cloudflare): download the CSV of each year in a browser
  (https://www.scimagojr.com/journalrank.php?year=2024, "Download data") and give their
  paths, comma-separated (the year read from each file name). They are merged into the
  data already there (``--overwrite``: rebuilt from them only): a journal gets its quartile
  in each year (``sjrHistory``, a paper taking the one of its year) and its other fields
  from its latest year (``sjrYear``); one dropped since (discontinued, e.g. Springer's
  "Machine Translation") is kept, with its last year.
- ``--base-year``: the year of the data already there when it has none (older builds).

CORE has its own script (build_core.py); the predatory list is downloaded by the app.
Records are sorted and written one per line, so that a rebuild only changes the lines of
the records that did change. Terms: see src/sci_report_analyzer/data/README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sci_report_analyzer.ranking import datasets

OUT = Path(__file__).resolve().parent.parent / "datasets"


def write(name: str, records: list[dict]) -> None:
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records)
    (OUT / name).write_text(f"[\n{lines}\n]\n" if records else "[]\n")
    print(f"{name}: {len(records)} records")


def scimago(paths: str, base: list[dict] | None = None, base_year: int | None = None) -> list[dict]:
    """``base`` (the journals already there) with the Scimago exports (comma-separated
    paths) merged in."""
    years: list[tuple[int, Path]] = []
    for part in paths.split(","):
        path = Path(part.strip()).expanduser()
        if (year := datasets.scimago_year_in(path.name)) is None:
            sys.exit(f"{path}: no year in the file name (e.g. 'scimagojr 2024.csv')")
        years.append((year, path))
    out = list(base or [])
    if any("sjrYear" not in r for r in out):
        if base_year is None:
            sys.exit("The data there has no year: give it with --base-year (or --overwrite)")
        out = [
            r
            if "sjrYear" in r
            else {
                **r,
                "sjrYear": base_year,
                "sjrHistory": {str(base_year): r["quartile"]} if r.get("quartile") else {},
            }
            for r in out
        ]
    for year, path in sorted(years):
        out = datasets.merge_scimago(
            out, year, datasets.scimago_records(path.read_text(encoding="utf-8-sig"))
        )
    return sorted(out, key=lambda r: f"{r['name']} {r.get('sourceId', '')}")


def main() -> None:
    args = dict(a.removeprefix("--").split("=", 1) for a in sys.argv[1:] if "=" in a)
    overwrite = "--overwrite" in sys.argv[1:]
    base_year = int(args.pop("base-year")) if "base-year" in args else None
    if set(args) != {"scimago"}:
        sys.exit(__doc__)
    path = OUT / datasets.JOURNALS_FILE
    base = [] if overwrite or not path.exists() else json.loads(path.read_text())
    write(datasets.JOURNALS_FILE, scimago(args["scimago"], base, base_year))


if __name__ == "__main__":
    main()
