"""excerpts: their years, and a "rayonnement" flag

Revision ID: c2d8f4a61e37
Revises: a7e3c1f9b250
Create Date: 2026-10-03 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d8f4a61e37"
down_revision: str | None = "a7e3c1f9b250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("start_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("end_year", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("rayonnement", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.drop_column("rayonnement")
        batch_op.drop_column("end_year")
        batch_op.drop_column("start_year")
