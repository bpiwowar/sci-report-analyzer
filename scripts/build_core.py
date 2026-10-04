"""Build the CORE conference rankings shipped with the app.

    uv run python scripts/build_core.py [--edition=ICORE2026]

Fetches every CORE edition from the ICORE portal and writes, in src/sci_report_analyzer/data/:
- conferences.json: the conferences of the latest edition, with their rank in each edition
  (``coreHistory``, linked across editions by the CORE id);
- conferences.past.json: the conferences only listed in past editions (dropped, merged),
  so that older papers still get their rank at the time.

The editions are ``ranking.badge.CORE_EDITIONS``: add a new one there when CORE publishes it.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import httpx

from sci_report_analyzer.ranking.badge import CORE_EDITIONS

OUT = Path(__file__).resolve().parent.parent / "src" / "sci_report_analyzer" / "data"
# The portal rejects requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def core_url(edition: str) -> str:
    return (
        "https://portal.core.edu.au/conf-ranks/?search=&by=all"
        f"&source={edition}&sort=arank&page=1&do=Export"
    )


def fetch_edition(client: httpx.Client, edition: str) -> list[dict[str, str]]:
    print(f"Fetching CORE ({edition})…")
    res = client.get(core_url(edition))
    res.raise_for_status()
    out = []
    # Export columns: id, title, acronym, source (edition), rank, ...
    for r in csv.reader(io.StringIO(res.text)):
        r = [c.strip() for c in r] + [""] * 5
        cid, title, acronym, _, rank = r[:5]
        if title and rank:
            out.append({"id": cid, "title": title, "acronym": acronym, "rank": rank})
    print(f"  {len(out)} conferences")
    return out


def build(latest: str) -> tuple[list[dict], list[dict]]:
    """Records of the latest edition, and of the conferences only in past editions."""
    editions = CORE_EDITIONS[: CORE_EDITIONS.index(latest) + 1]
    by_id: dict[str, dict] = {}  # its latest listing
    history: dict[str, dict[str, str]] = {}
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client:
        for edition in editions:
            for row in fetch_edition(client, edition):
                key = row["id"] or f"{row['title']} {row['acronym']}"
                by_id[key] = {**row, "edition": edition}
                history.setdefault(key, {})[edition] = row["rank"]
    current, past = [], []
    for key, row in by_id.items():
        record = {
            "name": row["title"],
            "source": "core",
            "type": "conference",
            "coreRank": row["rank"],
            "coreEdition": row["edition"],
            **({"aliases": [row["acronym"]]} if row["acronym"] else {}),
            **({"coreId": row["id"]} if row["id"] else {}),
            "coreHistory": history[key],
        }
        (current if row["edition"] == latest else past).append(record)
    print(f"  {len(current)} current conferences, {len(past)} from past editions")
    return current, past


def write(name: str, records: list[dict]) -> None:
    # Stable order and one record per line: rebuilds give small diffs.
    def key(r: dict) -> str:
        return "\0".join((r["name"], r["coreEdition"], (r.get("aliases") or [""])[0]))

    lines = [
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in sorted(records, key=key)
    ]
    (OUT / name).write_text("[\n" + ",\n".join(lines) + "\n]\n" if lines else "[]\n")


def main() -> None:
    latest = next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--edition=")),
        CORE_EDITIONS[-1],
    )
    current, past = build(latest)
    OUT.mkdir(parents=True, exist_ok=True)
    write("conferences.json", current)
    write("conferences.past.json", past)
    print(f"Done: written to {OUT}")


if __name__ == "__main__":
    main()
