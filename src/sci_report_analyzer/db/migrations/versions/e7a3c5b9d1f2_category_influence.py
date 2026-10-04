"""category: the folder's "rayonnement" one

A category can gather the excerpts flagged "influence" of the other categories (at most one
per folder); none by default (the copied excerpts end with their own section, as before).

Revision ID: e7a3c5b9d1f2
Revises: d4b2f8a6c1e3
Create Date: 2026-10-04 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3c5b9d1f2"
down_revision: str | None = "d4b2f8a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "influence" not in {c["name"] for c in inspector.get_columns("category")}:
        op.add_column(
            "category",
            sa.Column("influence", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("category") as batch:
        batch.drop_column("influence")
