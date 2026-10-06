"""Each star list's citing format: the template [@key] cites its papers with

A star list gets the template its papers are cited with (``tag.cite_template``; none: their
number, its reference format): a folder's own template of the same name as one of its
settings' lists moves there (out of the folder's templates), and the citations using it
(``[@key]{.starred}``) lose it in the texts (the notes: implicit now).

Revision ID: 2b7d9e4f6a18
Revises: 8f4c2a7e1b93
Create Date: 2026-10-06 00:00:00.000000
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b7d9e4f6a18"
down_revision: str | None = "8f4c2a7e1b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The texts citing papers: (table, column).
_TEXTS = [
    ("period", "notes"),
    ("period_document", "note"),
    ("section_text", "text"),
    ("publication", "note"),
    ("period_note", "text"),
    ("folder", "notes"),
    ("person", "notes"),
]


def _drop_template(conn, names: set[str]) -> None:
    """``{.name}`` after a citation (``[@key]{.name}``, ``@key{.name}``) removed."""
    pattern = re.compile(r"(?<=[\]\w])\{\.(" + "|".join(map(re.escape, sorted(names))) + r")\}")
    for table, column in _TEXTS:
        rows = conn.execute(
            sa.text(f"SELECT rowid, {column} FROM {table} WHERE {column} LIKE '%{{.%'")
        ).fetchall()
        for rowid, text in rows:
            new = pattern.sub("", text)
            if new != text:
                conn.execute(
                    sa.text(f"UPDATE {table} SET {column} = :t WHERE rowid = :r"),
                    {"t": new, "r": rowid},
                )


def upgrade() -> None:
    op.add_column("tag", sa.Column("cite_template", sa.String(), nullable=True))
    conn = op.get_bind()
    moved: set[str] = set()
    for sid, raw in conn.execute(sa.text("SELECT id, citations FROM folder_settings")).fetchall():
        c = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        templates = c.get("templates") or []
        kept = []
        for t in templates:
            found = conn.execute(
                sa.text(
                    "SELECT id FROM tag WHERE per_period AND name = :name "
                    "AND cite_template IS NULL AND settings_id = :sid"
                ),
                {"name": t.get("name") or "", "sid": sid},
            ).scalar()
            if found is None:
                kept.append(t)
            else:
                conn.execute(
                    sa.text("UPDATE tag SET cite_template = :attrs WHERE id = :id"),
                    {"attrs": t.get("attrs") or "", "id": found},
                )
                moved.add(t["name"])
        if len(kept) != len(templates):
            c["templates"] = kept
            conn.execute(
                sa.text("UPDATE folder_settings SET citations = :c WHERE id = :id"),
                {"c": json.dumps(c), "id": sid},
            )
    if moved:
        _drop_template(conn, moved)


def downgrade() -> None:
    op.drop_column("tag", "cite_template")
