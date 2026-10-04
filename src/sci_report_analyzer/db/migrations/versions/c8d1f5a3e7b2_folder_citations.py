"""folders: their citation settings (numbering, templates)

A folder gets its citation settings (``folder.citations``: the tag whose papers are
numbered, the number's format, its own templates). The tag and the format are those of the
folder's reports (the first one setting them, by period): the report's tag whose papers
were numbered from a list (else its first tag), and its format (``{n}`` written
``{index}``); without, the built-in "starred" tag if some papers of the folder have it.

Revision ID: c8d1f5a3e7b2
Revises: a4c7e2f9d316
Create Date: 2026-10-05 10:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d1f5a3e7b2"
down_revision: str | None = "a4c7e2f9d316"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _numbering(bind, folder_id: int) -> dict:
    """The tag and the format of the folder's first report setting them."""
    reports = bind.execute(
        sa.text(
            "SELECT r.period_id, r.tag_ids, r.number_format FROM report r "
            "JOIN period p ON p.id = r.period_id WHERE p.folder_id = :f ORDER BY p.id"
        ),
        {"f": folder_id},
    ).all()
    for period_id, tag_ids, fmt in reports:
        tag_ids = [t for t in _json(tag_ids) or [] if isinstance(t, int)]
        if not tag_ids:
            continue
        numbered = bind.execute(
            sa.text(
                "SELECT tag_id FROM period_tag WHERE period_id = :p AND number IS NOT NULL "
                "UNION SELECT pt.tag_id FROM publication_tag pt "
                "JOIN publication pub ON pub.id = pt.publication_id "
                "JOIN period p ON p.person_id = pub.person_id "
                "WHERE p.id = :p AND pt.number IS NOT NULL"
            ),
            {"p": period_id},
        )
        listed = {t for (t,) in numbered}
        tag = next((t for t in tag_ids if t in listed), tag_ids[0])
        out = {"tag_id": tag}
        if fmt:
            out["format"] = fmt.replace("{n}", "{index}")
        return out
    starred = bind.execute(
        sa.text(
            "SELECT t.id FROM tag t JOIN period_tag pt ON pt.tag_id = t.id "
            "JOIN period p ON p.id = pt.period_id WHERE t.key = 'starred' AND p.folder_id = :f "
            "LIMIT 1"
        ),
        {"f": folder_id},
    ).first()
    return {"tag_id": starred[0]} if starred else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "citations" not in {c["name"] for c in inspector.get_columns("folder")}:
        op.add_column("folder", sa.Column("citations", sa.JSON(), nullable=True))
    folders = [f for (f,) in bind.execute(sa.text("SELECT id FROM folder WHERE citations IS NULL"))]
    for folder_id in folders:
        if numbering := _numbering(bind, folder_id):
            bind.execute(
                sa.text("UPDATE folder SET citations = :v WHERE id = :f"),
                {"v": json.dumps(numbering), "f": folder_id},
            )


def downgrade() -> None:
    op.drop_column("folder", "citations")
