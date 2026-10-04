"""Citation keys from the surname, whatever the name order; data never read dropped

The keys (``[@doe2020neural]``, see reports.citation_keys) took the last word of the first
author's name: "Doe, Jane" gave ``jane2020neural`` and "NEVEOL Aurélie" ``aurelie2020…``.
They now take its surname (authors.surname): each person's texts citing a key whose paper
gets another one are rewritten (their notes, their papers' notes, their notes within the
folders, those of their documents, their excerpts). Both ways of making the keys are frozen
here (the app's may change).

Also dropped, as written but never read: the lists tags were put from (``ui.reflist.*``),
the reports (their view merged into the notes: a text not found in its person's notes within
the folder is appended to them first) and the ``old_reports`` request, when a sync started
(``source_link.sync_started_at``), a thesis' discipline.

Revision ID: 7c1e4a9b2d30
Revises: f6b3d8a2c5e9
Create Date: 2026-10-05 00:00:00.000000
"""

import json
import re
import unicodedata
from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e4a9b2d30"
down_revision: str | None = "f6b3d8a2c5e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---- the papers' keys, as reports.citation_keys made them (frozen) ----------------------------

_STOP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "from", "towards"}
_PRIORITY = ("doi", "dblp", "hal", "openalex", "semanticscholar", "orcid", "scholar")
_EXACT_POSITION = {"dblp", "openalex", "semanticscholar"}
_ARCHIVAL = frozenset(
    [
        "corr",
        "arxiv",
        "biorxiv",
        "medrxiv",
        "chemrxiv",
        "ssrn",
        "preprint",
        "preprints",
        "researchgate",
        "zenodo",
        "figshare",
        "osf",
        "vixra",
        "psyarxiv",
        "socarxiv",
        "eartharxiv",
        "techrxiv",
        "authorea",
        "repec",
        "hal",
        "eprint",
        "eprints",
    ]
)
_COMBINING = re.compile("[̀-ͯ]")


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _old_surname(name: str) -> str:
    names = name.replace(",", " ").split()
    return _ascii(names[-1]) if names else ""


def _natural_order(name: str) -> str:
    name = " ".join(name.split())
    if name.count(",") == 1:
        last, first = (x.strip() for x in name.split(","))
        if last and first:
            return f"{first} {last}"
    words = name.split()

    def caps(w: str) -> bool:
        letters = [c for c in w if c.isalpha()]
        return len(letters) >= 2 and all(c.isupper() for c in letters) and "." not in w

    upper = [w for w in words if caps(w)]
    if upper and len(upper) < len(words):
        return " ".join([w for w in words if not caps(w)] + upper)
    return name


def _new_surname(name: str) -> str:
    return next((w for w in map(_ascii, reversed(_natural_order(name).split())) if w), "")


def _keys(papers: list[tuple[int, str, int | None, str | None]], surname: Callable) -> dict:
    """(id, first author, year, title) -> {id: key}, the later paper of a clash suffixed."""
    out: dict[int, str] = {}
    used: set[str] = set()
    for pid, author, year, title in sorted(papers):
        words = [w for w in (_ascii(w) for w in (title or "").split()) if w and w not in _STOP]
        base = key = f"{surname(author) or 'anon'}{year or 'nd'}{words[0] if words else ''}"
        i = 0
        while key in used:
            i += 1
            key = base + (chr(96 + i) if i <= 26 else str(i))
        used.add(key)
        out[pid] = key
    return out


def _is_archival(archival: bool, venue: str | None) -> bool:
    """merge.is_archival: flagged, or a preprint server's venue (its normalized first word)."""
    if archival:
        return True
    s = _COMBINING.sub("", unicodedata.normalize("NFD", (venue or "").lower()))
    s = re.sub(r"[^a-z\d]+", " ", s.replace("&", " and "), flags=re.ASCII)
    words = re.sub(r"\b(?:zth|zeme)\b", " ", s).split()
    return bool(words) and words[0] in _ARCHIVAL


def _first_author(members: list[dict]) -> str:
    """pubview._authorship: the first author of the list it uses."""
    members.sort(
        key=lambda m: (
            _is_archival(m["archival"], m["venue"]),
            _PRIORITY.index(m["source"]) if m["source"] in _PRIORITY else len(_PRIORITY),
            m["id"],
        )
    )
    most = max((len(m["authors"]) for m in members if m["authors"]), default=0)
    with_authors = sorted(
        (m for m in members if m["authors"]),
        key=lambda m: (
            m["source"] != "doi" or m["archival"] or len(m["authors"]) < most,
            m["source"] not in _EXACT_POSITION,
            -len(m["authors"]),
        ),
    )
    return with_authors[0]["authors"][0] if with_authors else ""


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _renamed_keys(conn) -> dict[int, dict[str, str]]:
    """Person -> {old key: new key} (the keys that change)."""
    row = conn.execute(sa.text("SELECT value FROM app_setting WHERE key = 'enabled_sources'"))
    disabled = set((_json(v) or {}).get("disabled", [])) if (v := row.scalar()) else set()
    members: dict[int, list[dict]] = {}
    for r in conn.execute(
        sa.text(
            "SELECT sp.id, sp.publication_id, sp.authors, sp.archival, sp.venue, l.source "
            "FROM source_pub sp JOIN source_link l ON l.id = sp.link_id "
            "WHERE l.status = 'validated' AND sp.publication_id IS NOT NULL"
        )
    ):
        if r.source not in disabled:
            members.setdefault(r.publication_id, []).append(
                {
                    "id": r.id,
                    "authors": list(_json(r.authors) or []),
                    "archival": bool(r.archival),
                    "venue": r.venue,
                    "source": r.source,
                }
            )
    papers: dict[int, list] = {}
    for r in conn.execute(
        sa.text("SELECT id, person_id, title, year, year_override, missing FROM publication")
    ):
        if r.id in members or r.missing:
            author = _first_author(members.get(r.id, []))
            papers.setdefault(r.person_id, []).append(
                (r.id, author, r.year_override or r.year, r.title)
            )
    out = {}
    for person, rows in papers.items():
        old, new = _keys(rows, _old_surname), _keys(rows, _new_surname)
        if renamed := {old[i]: new[i] for i in old if old[i] != new[i]}:
            out[person] = renamed
    return out


# A person's texts that may cite their papers: (table, key column, text column, the person).
_TEXTS = (
    ("person", "id", "notes", "id"),
    ("publication", "id", "note", "person_id"),
    ("period", "id", "notes", "person_id"),
    *(
        (table, key, column, "(SELECT person_id FROM period WHERE period.id = period_id)")
        for table, key, column in (
            ("period_note", "rowid", "text"),
            ("period_document", "id", "note"),
            ("excerpt", "id", "text"),
            ("excerpt", "id", "group_text"),
        )
    ),
)


def _rewrite_keys(conn) -> None:
    renamed = _renamed_keys(conn)
    if not renamed:
        return
    for person, mapping in renamed.items():
        keys = "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
        # A whole key: not within a word or an email, not followed by more of a key.
        rx = re.compile(rf"(?<![\w@])@({keys})(?![\w]|[:.#$%&+?<>~/-]\w)")
        for table, key, column, owner in _TEXTS:
            rows = conn.execute(
                sa.text(
                    f"SELECT {key} AS k, {column} AS t FROM {table} "
                    f"WHERE {owner} = :p AND {column} LIKE '%@%'"
                ),
                {"p": person},
            )
            for k, text in list(rows):
                new = rx.sub(lambda m, mapping=mapping: "@" + mapping[m.group(1)], text)
                if new != text:
                    conn.execute(
                        sa.text(f"UPDATE {table} SET {column} = :t WHERE {key} = :k"),
                        {"t": new, "k": k},
                    )


def _keep_reports(conn) -> None:
    """The text of a report not found in its person's notes within the folder (where the
    old reports went, at first into the folder's own notes, since emptied): appended to
    them, before the reports are dropped."""
    rows = conn.execute(
        sa.text(
            "SELECT r.period_id, r.text, p.notes FROM report r "
            "JOIN period p ON p.id = r.period_id WHERE trim(r.text) != ''"
        )
    )
    for period_id, text, notes in list(rows):
        if text.strip() in (notes or ""):
            continue
        notes = (notes or "").rstrip()
        conn.execute(
            sa.text("UPDATE period SET notes = :n WHERE id = :p"),
            {
                "n": (notes + "\n\n" if notes else "") + f"## Report\n\n{text.strip()}\n",
                "p": period_id,
            },
        )


def upgrade() -> None:
    conn = op.get_bind()
    _keep_reports(conn)
    _rewrite_keys(conn)
    # Written, never read: the lists tags were put from (annotations.tag_list), the reports
    # (merged into the notes) and the request to merge them, when a sync started, a thesis'
    # discipline.
    conn.execute(sa.text("DELETE FROM app_setting WHERE key LIKE 'ui.reflist.%'"))
    conn.execute(sa.text("DELETE FROM app_setting WHERE key = 'old_reports'"))
    op.drop_table("report")
    with op.batch_alter_table("source_link") as batch:
        batch.drop_column("sync_started_at")
    with op.batch_alter_table("thesis") as batch:
        batch.drop_column("discipline")


def downgrade() -> None:
    # (the keys, the notes and the settings stay as they are)
    with op.batch_alter_table("thesis") as batch:
        batch.add_column(sa.Column("discipline", sa.String(), nullable=True))
    with op.batch_alter_table("source_link") as batch:
        batch.add_column(sa.Column("sync_started_at", sa.DateTime(), nullable=True))
    op.create_table(
        "report",
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("tag_ids", sa.JSON(), nullable=False),
        sa.Column("number_format", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("period_id"),
    )
