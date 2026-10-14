"""A person's career (their CV): positions and diplomas

Revision ID: c7a9e2d4f8b1
Revises: b6d1f3a8c7e2
Create Date: 2026-10-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a9e2d4f8b1"
down_revision: str | None = "b6d1f3a8c7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "career_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("start", sa.String(10), nullable=True),
        sa.Column("end", sa.String(10), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("details", sa.String(), nullable=True),
        sa.Column("thesis_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_career_entry_person_id", "career_entry", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_career_entry_person_id", "career_entry")
    op.drop_table("career_entry")
