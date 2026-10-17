"""A line of the CV shown with the years since (“+12”: e.g. the thesis, a grade)

Revision ID: a3f8c1e6b2d4
Revises: e9c4b2a7d5f3
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c1e6b2d4"
down_revision: str | None = "e9c4b2a7d5f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("career_entry") as batch:
        batch.add_column(
            sa.Column("elapsed", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
    # (the theses: the years since the PhD)
    op.execute("UPDATE career_entry SET elapsed = 1 WHERE thesis_id IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("career_entry") as batch:
        batch.drop_column("elapsed")
