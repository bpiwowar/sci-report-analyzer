"""categories and excerpts

Revision ID: 8e1f5a2c7b90
Revises: 3b7c2d9e4a61
Create Date: 2026-10-02 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e1f5a2c7b90"
down_revision: str | None = "3b7c2d9e4a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["category.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("category", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_category_folder_id"), ["folder_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_category_parent_id"), ["parent_id"], unique=False)
    op.create_table(
        "excerpt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("publication_id", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("rects", sa.JSON(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["category.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["period_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_excerpt_category_id"), ["category_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_excerpt_period_id"), ["period_id"], unique=False)


def downgrade() -> None:
    op.drop_table("excerpt")
    op.drop_table("category")
