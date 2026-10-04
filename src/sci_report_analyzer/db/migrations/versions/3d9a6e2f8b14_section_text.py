"""The text of a category's section in a person's excerpts within a folder

Revision ID: 3d9a6e2f8b14
Revises: 7c1e4a9b2d30
Create Date: 2026-10-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3d9a6e2f8b14"
down_revision: str | None = "7c1e4a9b2d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "section_text",
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["category.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("period_id", "category_id"),
    )
    op.create_index("ix_section_text_category_id", "section_text", ["category_id"])


def downgrade() -> None:
    op.drop_index("ix_section_text_category_id", table_name="section_text")
    op.drop_table("section_text")
