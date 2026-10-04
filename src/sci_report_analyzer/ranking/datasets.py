"""Ranking datasets (Scimago, CORE, predatory list) and JCR CSV import.

CORE ships with the app (``BUNDLED_DIR``), rebuilt by ``scripts/build_core.py`` (every
edition since 2008). Scimago (a journal's quartile in each year, so that a paper gets its
rank at publication time) is too large to ship: built by ``scripts/build_datasets.py`` into
the repository (``datasets/journals.json``), it is downloaded from GitHub into the data
directory and checked every week, each update merged into the copy there; run from the
source tree, the app reads the repository's file instead. Past Scimago years can also be
imported in the app (scimagojr.com blocks scripts: the user downloads them), kept in the
data directory (one file, each journal once) and merged on load. The predatory list is
downloaded into the data directory from its source, and refreshed when older than
``PREDATORY_MAX_AGE``.
See ``data/README.md`` for the sources and their terms, ``docs/json-formats.md`` for the
records' fields.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .. import config
from .matcher import Record
from .normalize import normalize

logger = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data"
JOURNALS_FILE = "journals.json"
# The Scimago journals, in the repository (not shipped with the app: downloaded from there).
JOURNALS_URL = (
    "https://raw.githubusercontent.com/bpiwowar/sci-report-analyzer/main/datasets/" + JOURNALS_FILE
)
JOURNALS_MAX_AGE = 7 * 24 * 3600  # seconds between checks for an update
# The repository's copy, when the app runs from the source tree (``uv run``): used as is.
SOURCE_JOURNALS: Path | None = Path(__file__).resolve().parents[3] / "datasets" / JOURNALS_FILE
# CORE: the latest edition (with each record's past ranks), and conferences only listed in
# past editions.
CORE_FILES = ("conferences.json", "conferences.past.json")
PREDATORY_FILE = "predatory.json"
# Beall's list of predatory journals and publishers, as maintained by stop-predatory-journals.
PREDATORY_BASE = (
    "https://raw.githubusercontent.com/stop-predatory-journals/"
    "stop-predatory-journals.github.io/master/_data"
)
PREDATORY_MAX_AGE = 7 * 24 * 3600  # seconds


def ranking_files() -> list[str]:
    """Ranking files, journals first (those of CORE shipped: when present)."""
    return [JOURNALS_FILE, *(f for f in CORE_FILES if (BUNDLED_DIR / f).exists())]


def load_journals() -> list[Record]:
    """The Scimago journals (see ``journals_path``), with the years imported in the app
    merged in."""
    records = _load(journals_path())
    if imported := load_imported_scimago()["journals"]:
        records = merge_journals(records, imported)
    return records


def load_file(name: str) -> list[Record]:
    return _load(BUNDLED_DIR / name)


def _load(path: Path) -> list[Record]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logger.warning("Could not load dataset %s: %s", path, e)
        return []


# ---- Scimago journals (downloaded from the repository) ---------------------------------------


def from_source_tree() -> bool:
    """Run from the source tree: the repository's journals are used (never downloaded)."""
    return SOURCE_JOURNALS is not None and SOURCE_JOURNALS.exists()


def journals_path() -> Path:
    """The Scimago journals used: the repository's (run from the source tree), else the
    copy downloaded into the data directory."""
    if from_source_tree():
        return SOURCE_JOURNALS  # type: ignore[return-value]
    return config.DATA_DIR / "datasets" / JOURNALS_FILE


def journals_updated_at() -> datetime | None:
    try:
        return datetime.fromtimestamp(journals_path().stat().st_mtime)
    except OSError:
        return None


async def refresh_journals(*, force: bool = False) -> bool:
    """Download the Scimago journals when missing, or when not checked for
    ``JOURNALS_MAX_AGE`` (or ``force``), merged into the copy there (a journal dropped
    since keeps its years); True when it changed. Only what changed is downloaded (ETag)."""
    if from_source_tree():
        return False
    path = journals_path()
    etag_path = path.with_suffix(".etag")
    if not force and path.exists() and time.time() - path.stat().st_mtime < JOURNALS_MAX_AGE:
        return False
    headers = {}
    if path.exists() and etag_path.exists():
        headers["If-None-Match"] = etag_path.read_text().strip()
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        res = await client.get(JOURNALS_URL, headers=headers)
    if res.status_code == 304:
        path.touch()  # checked
        return False
    res.raise_for_status()
    records = res.json()
    if not isinstance(records, list) or not records:
        raise ValueError("the Scimago journals downloaded are empty")
    merged = merge_journals(_load(path), records)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False))
    tmp.replace(path)
    if etag := res.headers.get("etag"):
        etag_path.write_text(etag)
    logger.info("Scimago journals updated: %d journals", len(merged))
    return True


# ---- Scimago, year by year ------------------------------------------------------------------

# The first year of Scimago's ranking.
SCIMAGO_FIRST_YEAR = 1999


def scimago_download_url(year: int) -> str:
    return f"https://www.scimagojr.com/journalrank.php?year={year}&out=xls"


def scimago_year_in(name: str) -> int | None:
    """The year of a Scimago export, from its file name ("scimagojr 2021.csv")."""
    m = re.search(r"(?<!\d)(?:19|20)\d\d(?!\d)", name)
    return int(m[0]) if m else None


def scimago_records(text: str) -> list[Record]:
    """The journals of a Scimago export of one year (its "Download data" CSV)."""
    out: list[Record] = []
    for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff")), delimiter=";"):
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not (name := r.get("title")):
            continue
        try:
            sjr: float | None = float(r.get("sjr", "").replace(",", "."))
        except ValueError:
            sjr = None
        try:
            hindex = int(r.get("h index", "")) or None
        except ValueError:
            hindex = None
        rec: Record = {
            "name": name,
            "source": "scimago",
            "type": "journal",
            "sjr": sjr,
            "quartile": r.get("sjr best quartile") or None,
            "hindex": hindex,
        }
        if issn := [s.strip() for s in r.get("issn", "").split(",") if s.strip()]:
            rec["issn"] = issn
        if source_id := r.get("sourceid"):
            rec["sourceId"] = source_id
        out.append(rec)
    return out


def _scimago_key(rec: Record) -> str:
    return rec.get("sourceId") or normalize(rec["name"])


def scimago_years(records: list[Record]) -> set[int]:
    """The years the journals' quartiles are known for."""
    return {int(y) for r in records for y in r.get("sjrHistory") or ()}


def merge_journals(base: list[Record], extra: list[Record]) -> list[Record]:
    """``base`` with the journals of ``extra``, each journal once: its quartiles of both
    (``sjrHistory``), its other fields from its latest year (``sjrYear``; a journal
    without one is taken as more recent). Those not in ``base`` are added."""
    out = list(base)
    index = {_scimago_key(r): i for i, r in enumerate(out)}
    for rec in extra:
        key = _scimago_key(rec)
        if (i := index.get(key)) is None:
            index[key] = len(out)
            out.append(rec)
            continue
        old = out[i]
        history = {**(old.get("sjrHistory") or {}), **(rec.get("sjrHistory") or {})}
        latest, year = old.get("sjrYear"), rec.get("sjrYear")
        if latest is not None and year is not None and year >= latest:
            old = rec
        out[i] = {**old, "sjrHistory": dict(sorted(history.items()))}
    return out


def merge_scimago(base: list[Record], year: int, records: list[Record]) -> list[Record]:
    """``base`` with the journals of the Scimago export of ``year`` (see ``merge_journals``)."""
    dated = [
        {
            **r,
            "sjrYear": year,
            "sjrHistory": {str(year): r["quartile"]} if r.get("quartile") else {},
        }
        for r in records
    ]
    return merge_journals(base, dated)


def scimago_import_path() -> Path:
    return config.DATA_DIR / "datasets" / "scimago.json"


def load_imported_scimago() -> dict[str, Any]:
    """The Scimago years imported in the app: ``{"years": [...], "journals": [...]}``, each
    journal once (as merged by ``merge_scimago``)."""
    try:
        data = json.loads(scimago_import_path().read_text())
    except FileNotFoundError:
        return {"years": [], "journals": []}
    except (OSError, ValueError) as e:
        logger.warning("Could not load the imported Scimago years: %s", e)
        return {"years": [], "journals": []}
    return {"years": data.get("years") or [], "journals": data.get("journals") or []}


def imported_scimago_years() -> list[int]:
    return sorted(load_imported_scimago()["years"])


def import_scimago(text: str, year: int) -> int:
    """Merge the Scimago export of ``year`` into the years imported (kept: past years don't
    change); the number of its journals."""
    records = scimago_records(text)
    if not records:
        raise ValueError("no journal found: a Scimago export (“Download data”) is needed")
    data = load_imported_scimago()
    journals = merge_scimago(data["journals"], year, records)
    path = scimago_import_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    years = sorted({*data["years"], year})
    tmp.write_text(json.dumps({"years": years, "journals": journals}, ensure_ascii=False))
    tmp.replace(path)
    return len(records)


# ---- predatory list (downloaded) -------------------------------------------------------------


def predatory_path() -> Path:
    return config.DATA_DIR / "datasets" / PREDATORY_FILE


def load_predatory() -> list[Record]:
    try:
        return json.loads(predatory_path().read_text())
    except (OSError, ValueError):
        return []


def predatory_updated_at() -> datetime | None:
    try:
        return datetime.fromtimestamp(predatory_path().stat().st_mtime)
    except OSError:
        return None


def predatory_records(csv_text: str) -> list[Record]:
    """Records from one of the list's CSV files (``url,name,abbr``)."""
    out = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not (name := r.get("name")):
            continue
        rec: Record = {"name": name, "source": "predatory", "type": "journal", "predatory": True}
        if url := r.get("url"):
            rec["url"] = url
        if abbr := r.get("abbr"):
            rec["aliases"] = [abbr]
        out.append(rec)
    return out


async def refresh_predatory(*, force: bool = False) -> bool:
    """Download the predatory list when missing or out of date (or ``force``); True when
    it was replaced (the file is only replaced once both downloads succeeded)."""
    path = predatory_path()
    if not force and path.exists() and time.time() - path.stat().st_mtime < PREDATORY_MAX_AGE:
        return False
    records: list[Record] = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for kind in ("journals", "publishers"):
            res = await client.get(f"{PREDATORY_BASE}/{kind}.csv")
            res.raise_for_status()
            records += predatory_records(res.text)
    if not records:
        raise ValueError("the predatory list is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records))
    tmp.replace(path)
    logger.info("Predatory list updated: %d entries", len(records))
    return True


async def keep_datasets_fresh() -> None:
    """At startup, then daily: refresh the downloaded datasets (Scimago journals, predatory
    list) when out of date."""
    from .service import service

    while True:
        for what, refresh in (
            ("Scimago journals", refresh_journals),
            ("predatory list", refresh_predatory),
        ):
            try:
                if await refresh():
                    service.invalidate(data=True, clear_cache=True)
            except Exception as e:
                logger.warning("Could not update the %s: %s", what, e)
        await asyncio.sleep(24 * 3600)


# ---- JCR CSV import (port of jcrRowsFromCsv) -------------------------------------------------


def _find(headers: list[str], *patterns: str) -> int | None:
    for p in patterns:
        rx = re.compile(p, re.I)
        for i, h in enumerate(headers):
            if rx.search(h):
                return i
    return None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    v = v.strip().replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def jcr_rows_from_csv(text: str) -> list[dict[str, Any]]:
    """Parse a JCR export, guessing columns from header names."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # Skip leading banner lines until a header that names the journal.
    start = next(
        (
            i
            for i, r in enumerate(rows)
            if any(re.search(r"journal|title|name", c, re.I) for c in r)
        ),
        0,
    )
    headers = [h.strip() for h in rows[start]]
    i_name = _find(headers, r"^journal name$", r"journal", r"title", r"name")
    i_issn = _find(headers, r"^issn$", r"\bissn\b")
    i_eissn = _find(headers, r"eissn", r"e-issn")
    i_if = _find(headers, r"impact factor", r"\bjif\b", r"\bif\b")
    i_q = _find(headers, r"quartile")
    out: list[dict[str, Any]] = []
    for r in rows[start + 1 :]:
        if i_name is None or i_name >= len(r) or not r[i_name].strip():
            continue
        issns = [
            r[i].strip()
            for i in (i_issn, i_eissn)
            if i is not None and i < len(r) and r[i].strip() and r[i].strip() != "N/A"
        ]
        q = r[i_q].strip().upper() if i_q is not None and i_q < len(r) else ""
        out.append(
            {
                "name": r[i_name].strip(),
                "source": "jcr",
                "type": "journal",
                "impactFactor": _num(r[i_if]) if i_if is not None and i_if < len(r) else None,
                "quartile": q if re.fullmatch(r"Q[1-4]", q) else None,
                "issn": issns,
            }
        )
    return out
