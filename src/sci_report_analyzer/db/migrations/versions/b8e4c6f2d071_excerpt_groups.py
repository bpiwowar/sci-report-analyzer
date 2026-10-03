"""excerpts: merged ones are grouped (the excerpt leading the group), and ordered

Revision ID: b8e4c6f2d071
Revises: f3c7d2e8a915
Create Date: 2026-10-03 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4c6f2d071"
down_revision: str | None = "f3c7d2e8a915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("group_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_index(batch_op.f("ix_excerpt_group_id"), ["group_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_excerpt_group_id", "excerpt", ["group_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.drop_constraint("fk_excerpt_group_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_excerpt_group_id"))
        batch_op.drop_column("position")
        batch_op.drop_column("group_id")
