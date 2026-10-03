"""period documents, bookmarks

Revision ID: 3b7c2d9e4a61
Revises: f14fa8f18280
Create Date: 2026-10-02 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b7c2d9e4a61"
down_revision: str | None = "f14fa8f18280"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "period_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("bookmarks", sa.JSON(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("period_document", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_period_document_period_id"), ["period_id"], unique=False
        )

    with op.batch_alter_table("publication_pdf", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bookmarks", sa.JSON(), server_default="[]", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("publication_pdf", schema=None) as batch_op:
        batch_op.drop_column("bookmarks")
    with op.batch_alter_table("period_document", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_period_document_period_id"))
    op.drop_table("period_document")
