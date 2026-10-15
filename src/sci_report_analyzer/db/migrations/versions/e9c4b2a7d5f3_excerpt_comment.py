"""A commentary on an excerpt (Markdown, a comment after its item in the notes)

Revision ID: e9c4b2a7d5f3
Revises: c7a9e2d4f8b1
Create Date: 2026-10-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9c4b2a7d5f3"
down_revision: str | None = "c7a9e2d4f8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("comment", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("comment")
