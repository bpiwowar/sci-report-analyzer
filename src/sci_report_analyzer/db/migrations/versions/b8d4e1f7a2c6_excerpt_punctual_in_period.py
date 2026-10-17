"""Whether an excerpt is a one-off event (a moment, no duration), or happened sometime within
the person's period in the folder (no precise date)

Revision ID: b8d4e1f7a2c6
Revises: a3f8c1d6e2b7
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d4e1f7a2c6"
down_revision: str | None = "a3f8c1d6e2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("punctual", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("in_period", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("in_period")
        batch.drop_column("punctual")
