"""citations: templates back to Pandoc's classes (.short-venue (.year))

c3a8e6f1d4b9 converted, for a while, how a paper is cited (within the braces after
``[@key]``) to Python's style: names (``{notes tags}``) or fields (``{**#{index}**
({short-venue} {year})}``). Back to classes: ``{.notes .tags}``, ``{**#.index**
(.short-venue .year)}``, in the citations of the notes (and of the old reports) and the
saved templates (general, and the folders'). Those already with classes are kept.

Revision ID: d4b2f8a6c1e3
Revises: c3a8e6f1d4b9
Create Date: 2026-10-04 17:00:00.000000
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b2f8a6c1e3"
down_revision: str | None = "c3a8e6f1d4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = r"\w+(?:[:.#$%&+?<>~/-]\w+)*"
# (a citation, its braces with fields within: as converted)
_CITE = re.compile(
    r"(?P<cite>\[[^\[\]]*?(?<![\w@])-?@"
    + _KEY
    + r"[^\[\]]*\])\{(?P<attrs>(?:[^{}\n]|\{[^{}\n]*\})*)\}"
)
_FIELD = re.compile(r"\{([A-Za-z][\w-]*)\}")
_NAMES = re.compile(r"\s*[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)*\s*")

# (table, key column, text column)
TEXTS = [
    ("publication", "id", "note"),
    ("period", "id", "notes"),
    ("folder", "id", "notes"),
    ("period_document", "id", "note"),
    ("period_note", "rowid", "text"),
    ("report", "period_id", "text"),
]


def convert(inner: str) -> str:
    """Within the braces: ``notes tags`` → ``.notes .tags``, ``{short-venue} ({year})`` →
    ``.short-venue (.year)``; classes kept."""
    if _NAMES.fullmatch(inner):
        return " ".join("." + n for n in inner.split())
    return _FIELD.sub(r".\1", inner)


def convert_text(text: str) -> str:
    return _CITE.sub(lambda m: m.group("cite") + "{" + convert(m.group("attrs")) + "}", text)


def convert_template(attrs: str) -> str:
    a = (attrs or "").strip()
    if a.startswith("{") and a.endswith("}"):
        return "{" + convert(a[1:-1]) + "}"
    return attrs


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table, key, column in TEXTS:
        if table not in tables:
            continue
        rows = bind.execute(
            sa.text(f"SELECT {key}, {column} FROM {table} WHERE {column} LIKE '%@%'")
        ).all()
        for k, text in rows:
            if text and (new := convert_text(text)) != text:
                bind.execute(
                    sa.text(f"UPDATE {table} SET {column} = :v WHERE {key} = :k"),
                    {"v": new, "k": k},
                )
    row = bind.execute(
        sa.text("SELECT value FROM app_setting WHERE key = 'report_templates'")
    ).first()
    if row and (value := _json(row[0])):
        for t in value.get("items") or []:
            t["attrs"] = convert_template(t.get("attrs", ""))
        if "default" in value:
            value["default"] = convert_template(value["default"])
        bind.execute(
            sa.text("UPDATE app_setting SET value = :v WHERE key = 'report_templates'"),
            {"v": json.dumps(value)},
        )
    for fid, citations in bind.execute(
        sa.text("SELECT id, citations FROM folder WHERE citations IS NOT NULL")
    ).all():
        c = _json(citations) or {}
        if c.get("templates"):
            for t in c["templates"]:
                t["attrs"] = convert_template(t.get("attrs", ""))
            bind.execute(
                sa.text("UPDATE folder SET citations = :v WHERE id = :f"),
                {"v": json.dumps(c), "f": fid},
            )


def downgrade() -> None:
    pass
