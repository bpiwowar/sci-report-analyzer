"""venues: an acronym set by hand (or none) vs inferred

The acronyms so far were all set by hand.

Revision ID: e7a3c5d92b18
Revises: d2b7e4c91a56
Create Date: 2026-10-04 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3c5d92b18"
down_revision: str | None = "d2b7e4c91a56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("short_manual", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute("UPDATE venue SET short_manual = 1 WHERE short_name IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE venue SET short_name = NULL WHERE NOT short_manual")
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("short_manual")
