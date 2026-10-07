"""A person's primary source in a folder (overriding the folder's)

Revision ID: a5d2e8c4f1b7
Revises: c3e8a1f5d7b9
Create Date: 2026-10-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5d2e8c4f1b7"
down_revision: str | None = "c3e8a1f5d7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("period") as batch:
        batch.add_column(sa.Column("primary_source", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("period") as batch:
        batch.drop_column("primary_source")
