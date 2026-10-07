"""A category's id in the reports ([[excerpts#id]])

Revision ID: e4b7c9d2a6f1
Revises: d8e2f4a6b1c3
Create Date: 2026-10-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c9d2a6f1"
down_revision: str | None = "d8e2f4a6b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("category") as batch:
        batch.add_column(sa.Column("key", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("category") as batch:
        batch.drop_column("key")
