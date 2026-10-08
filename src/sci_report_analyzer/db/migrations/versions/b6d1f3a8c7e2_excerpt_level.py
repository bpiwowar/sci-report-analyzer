"""An excerpt's level (e.g. local, national, international: see reports.levels)

Revision ID: b6d1f3a8c7e2
Revises: a3c5e7f9b2d4
Create Date: 2026-10-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d1f3a8c7e2"
down_revision: str | None = "a3c5e7f9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("level", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("level")
