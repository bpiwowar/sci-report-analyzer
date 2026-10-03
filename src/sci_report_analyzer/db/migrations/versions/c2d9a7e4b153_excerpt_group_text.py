"""excerpts: merged as a reference only (not quoted); a group's own text

Revision ID: c2d9a7e4b153
Revises: b8e4c6f2d071
Create Date: 2026-10-03 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d9a7e4b153"
down_revision: str | None = "b8e4c6f2d071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ref_only", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("group_text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.drop_column("group_text")
        batch_op.drop_column("ref_only")
