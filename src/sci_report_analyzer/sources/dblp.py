"""DBLP via its SPARQL endpoint (https://sparql.dblp.org)."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..authors import name_key
from ..i18n import _
from ..ranking.badge import detect_track
from ..ranking.kinds import WORKSHOP_RE
from ..ranking.normalize import is_non_venue, normalize
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    SourceError,
    get_json,
    normalize_doi,
    to_int,
    to_year,
)

ENDPOINT = "https://sparql.dblp.org/sparql"
PREFIX = "PREFIX dblp: <https://dblp.org/rdf/schema#>\n"
_ARXIV_KEY = re.compile(r"journals/corr/abs-(\d{4})-(\d{4,5})")
_PID_RE = re.compile(r"(?:dblp\.org/pid/|dblp\.uni-trier\.de/pid/|^)([a-z0-9]+/[a-z0-9-]+)", re.I)


def _lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _val(b: dict[str, Any], k: str) -> str | None:
    v = b.get(k)
    return v["value"] if v else None


async def sparql(query: str) -> list[dict[str, Any]]:
    data = await get_json(
        ENDPOINT,
        method="POST",
        data={"query": PREFIX + query},
        headers={"Accept": "application/sparql-results+json"},
    )
    return data["results"]["bindings"]


# Book series titles, for records stored before stream URIs were kept.
_SERIES = re.compile(
    r"^(lecture notes in|communications in computer and information science"
    r"|ceur\b|proceedings of machine learning research|studies in health technology"
    r"|revue des nouvelles technologies|ifip advances|advances in intelligent systems"
    r"|smart innovation|studies in computational intelligence|frontiers in artificial"
    r"|acm international conference proceeding series|leibniz international proceedings)",
    re.I,
)


def pick_stream(published_in: str | None, streams: list[str], series: list[str]) -> str | None:
    """The stream naming the venue itself.

    A paper is often in several streams: its conference or journal, and the book series
    that printed it ("Lecture Notes in Computer Science (LNCS)"). Series are only used when
    nothing else is known; among the others, the one whose acronym is the venue's short
    name ("AIME (1)" → "... in Medicine (AIME)") wins.
    """
    venues = [s for s in streams if s not in series and not _SERIES.search(s)] or streams
    if not venues:
        return None
    if published_in and (m := re.match(r"[\w-]+", published_in)):
        short = m.group(0).lower()
        for s in venues:
            if (a := re.search(r"\(([^()]+)\)\s*$", s)) and a.group(1).lower() == short:
                return s
    return venues[0]


def _is_the_event(published_in: str, stream: str) -> bool:
    """Whether the stream is the satellite event itself: "BioNLP@ACL" in the stream
    "Workshop on Biomedical NLP (BioNLP)" (not "Trustworthy AI @ ACM Multimedia" in
    "ACM International Conference on Multimedia (MM)")."""
    if detect_track(stream) or WORKSHOP_RE.search(stream):
        return True
    event = published_in.split("@", 1)[0].strip().lower() if "@" in published_in else None
    acronym = re.search(r"\(([^()]+)\)\s*$", stream)
    return bool(event and acronym and acronym.group(1).lower() == event)


def dblp_venue(published_in: str | None, streams: list[str], series: list[str]) -> str | None:
    stream = pick_stream(published_in, streams, series)
    # Full stream titles match rankings best, but keep the short venue when it names a
    # satellite track (workshop, demo...) the series title would hide.
    satellite = published_in and (
        detect_track(published_in)
        or WORKSHOP_RE.search(published_in)
        or re.search(r"\bfindings\b", published_in, re.I)
    )
    if satellite and stream and _is_the_event(published_in, stream):
        satellite = False
    venue = f"{published_in}: {stream}" if satellite and stream else stream or published_in
    if published_in and is_non_venue(normalize(published_in)):
        venue = published_in  # "CoRR", not "Computing Research Repository (CoRR)"
    return venue


class DblpAdapter(SourceAdapter):
    name = "dblp"
    label = "DBLP"
    exact_position = True

    def profile_url(self, external_id: str) -> str:
        return f"https://dblp.org/pid/{external_id}.html"

    def parse_url(self, url: str) -> str | None:
        m = _PID_RE.search(url.strip().removesuffix(".html"))
        return m.group(1) if m else None

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        surname, _given = name_key(name)
        if not surname:
            return []
        rows = await sparql(
            f"""SELECT ?p (SAMPLE(?name) AS ?n) (SAMPLE(?aff) AS ?a) (SAMPLE(?orcid) AS ?o)
            (COUNT(DISTINCT ?pub) AS ?count) WHERE {{
              ?p a dblp:Person ; dblp:creatorName ?name .
              FILTER(CONTAINS(LCASE(STR(?name)), {_lit(surname)}))
              OPTIONAL {{ ?p dblp:affiliation ?aff }}
              OPTIONAL {{ ?p dblp:orcid ?orcid }}
              OPTIONAL {{ ?pub dblp:authoredBy ?p }}
            }} GROUP BY ?p LIMIT 50"""
        )
        out = []
        for b in rows:
            pid = (_val(b, "p") or "").removeprefix("https://dblp.org/pid/")
            if not pid:
                continue
            orcid = _val(b, "o")
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=pid,
                    display_name=_val(b, "n") or pid,
                    url=self.profile_url(pid),
                    affiliation=_val(b, "a"),
                    works_count=to_int(_val(b, "count")),
                    orcid=orcid.removeprefix("https://orcid.org/") if orcid else None,
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        pid_uri = f"<https://dblp.org/pid/{external_id}>"
        pubs = await sparql(
            f"""SELECT ?pub ?title ?year ?type ?doi ?publishedIn ?s ?stream ?ord ?nc WHERE {{
              ?pub dblp:authoredBy {pid_uri} ; dblp:title ?title .
              OPTIONAL {{ ?pub dblp:yearOfPublication ?year }}
              OPTIONAL {{ ?pub a ?type . FILTER(?type != dblp:Publication) }}
              OPTIONAL {{ ?pub dblp:doi ?doi }}
              OPTIONAL {{ ?pub dblp:publishedIn ?publishedIn }}
              OPTIONAL {{ ?pub dblp:publishedInStream ?s . ?s dblp:streamTitle ?stream }}
              OPTIONAL {{ ?pub dblp:numberOfCreators ?nc }}
              OPTIONAL {{ ?pub dblp:hasSignature ?sig .
                          ?sig dblp:signatureCreator {pid_uri} ; dblp:signatureOrdinal ?ord }}
            }}"""
        )
        if not pubs:
            # Distinguish "no publications" from a wrong pid.
            exists = await sparql(f"SELECT ?n WHERE {{ {pid_uri} dblp:creatorName ?n }} LIMIT 1")
            if not exists:
                raise SourceError(_("unknown DBLP pid {pid}").format(pid=external_id))
            return FetchResult()
        sigs = await sparql(
            f"""SELECT ?pub ?o ?n WHERE {{
              ?pub dblp:authoredBy {pid_uri} ; dblp:hasSignature ?sig .
              ?sig dblp:signatureOrdinal ?o ; dblp:signatureDblpName ?n }}"""
        )
        authors: dict[str, dict[int, str]] = defaultdict(dict)
        for b in sigs:
            o = to_int(_val(b, "o"))
            if o is not None:
                # Strip DBLP homonym suffixes ("Jane Doe 0002").
                authors[_val(b, "pub") or ""][o] = re.sub(r"\s+\d{4}$", "", _val(b, "n") or "")

        by_pub: dict[str, dict[str, Any]] = {}
        for b in pubs:
            uri = _val(b, "pub") or ""
            p = by_pub.setdefault(uri, {"streams": [], "series": [], "types": set()})
            for k in ("title", "year", "doi", "publishedIn", "ord", "nc"):
                if (v := _val(b, k)) and k not in p:
                    p[k] = v
            if (s := _val(b, "stream")) and s not in p["streams"]:
                p["streams"].append(s)
                if "/streams/series/" in (_val(b, "s") or ""):
                    p["series"].append(s)
            if t := _val(b, "type"):
                p["types"].add(t.rsplit("#", 1)[-1])

        out = []
        for uri, p in by_pub.items():
            key = uri.removeprefix("https://dblp.org/rec/")
            types = p["types"]
            doc_type = next(iter(sorted(types)), None)
            venue_type = (
                "conference"
                if "Inproceedings" in types
                else "journal"
                if "Article" in types
                else None
            )
            published_in = p.get("publishedIn")
            venue = dblp_venue(published_in, p["streams"], p["series"])
            names = authors.get(uri, {})
            ordered = [names[i] for i in sorted(names)]
            title = (p.get("title") or "").rstrip(".")
            arxiv = _ARXIV_KEY.match(key)
            arxiv_id = f"{arxiv.group(1)}.{arxiv.group(2)}" if arxiv else None
            out.append(
                FetchedPub(
                    external_key=key,
                    title=title,
                    year=to_year(p.get("year")),
                    venue=venue,
                    authors=ordered,
                    author_pos=to_int(p.get("ord")),
                    num_authors=to_int(p.get("nc")) or (len(ordered) or None),
                    venue_type=venue_type,
                    doi=normalize_doi(p.get("doi")),
                    url=f"https://dblp.org/rec/{key}.html",
                    doc_type=doc_type,
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
                    archival=bool(arxiv),
                    raw={
                        "publishedIn": published_in,
                        "streams": p["streams"],
                        "series": p["series"],
                    },
                )
            )
        return FetchResult(publications=out)
