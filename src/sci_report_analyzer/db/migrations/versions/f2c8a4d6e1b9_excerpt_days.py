"""How many days a year an excerpt's activity takes (its weight on the timeline)

Revision ID: f2c8a4d6e1b9
Revises: b8d4e1f7a2c6
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c8a4d6e1b9"
down_revision: str | None = "b8d4e1f7a2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("days", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("days")
