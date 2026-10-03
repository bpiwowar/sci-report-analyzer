"""excerpts: their text as selected (kept when edited)

Revision ID: d4e8b1c6f720
Revises: c2d9a7e4b153
Create Date: 2026-10-04 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8b1c6f720"
down_revision: str | None = "c2d9a7e4b153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("original", sa.Text(), nullable=True))
    op.execute("UPDATE excerpt SET original = text")


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.drop_column("original")
