"""A section of excerpts summarised by its text only (its excerpts hidden)

Revision ID: 9b5d3f7a2c61
Revises: 4a8c1f3e7d52
Create Date: 2026-10-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b5d3f7a2c61"
down_revision: str | None = "4a8c1f3e7d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("section_text") as batch:
        batch.add_column(
            sa.Column("summary_only", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("section_text") as batch:
        batch.drop_column("summary_only")
