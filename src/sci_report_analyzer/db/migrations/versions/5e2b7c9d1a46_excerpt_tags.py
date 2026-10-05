"""The tags of excerpts

Revision ID: 5e2b7c9d1a46
Revises: 3d9a6e2f8b14
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e2b7c9d1a46"
down_revision: str | None = "3d9a6e2f8b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "excerpt_tag",
        sa.Column("excerpt_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["excerpt_id"], ["excerpt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("excerpt_id", "tag_id"),
    )
    op.create_index("ix_excerpt_tag_tag_id", "excerpt_tag", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_excerpt_tag_tag_id", table_name="excerpt_tag")
    op.drop_table("excerpt_tag")
