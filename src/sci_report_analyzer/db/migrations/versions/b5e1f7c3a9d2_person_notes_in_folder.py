"""period: the person's notes within the folder

The notes next to the PDFs are a person's, within the folder (one text for all their
documents and papers in it), no longer one text for the whole folder: a period's own notes.
The folder's notes stay (a short text about the folder, as before), unchanged.

Revision ID: b5e1f7c3a9d2
Revises: e2a6c9f4b871
Create Date: 2026-10-04 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e1f7c3a9d2"
down_revision: str | None = "e2a6c9f4b871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "notes" not in {c["name"] for c in inspector.get_columns("period")}:
        op.add_column("period", sa.Column("notes", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("period") as batch:
        batch.drop_column("notes")
