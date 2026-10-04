"""reports: into the folders' notes (their view is gone)

The reports' texts and their papers (with their notes) are appended to the notes of the
people in their folders (b5e1f7c3a9d2; at first, of the folders) by the app, once it runs (it renders the citations with the papers as loaded: see
old_reports.py); this revision only asks for it (the ``old_reports`` app setting), when
there are folders. The report table is kept.

Revision ID: e2a6c9f4b871
Revises: c8d1f5a3e7b2
Create Date: 2026-10-05 10:30:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a6c9f4b871"
down_revision: str | None = "c8d1f5a3e7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING_KEY = "old_reports"  # (a copy: old_reports.PENDING_KEY)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT 1 FROM app_setting WHERE key = :k"), {"k": PENDING_KEY}
    ).first():
        return  # (asked already, or done)
    if bind.execute(sa.text("SELECT 1 FROM folder LIMIT 1")).first():
        bind.execute(
            sa.text("INSERT INTO app_setting (key, value) VALUES (:k, :v)"),
            {"k": PENDING_KEY, "v": json.dumps({"pending": True, "done": []})},
        )


def downgrade() -> None:
    pass  # (the notes keep what was appended)
