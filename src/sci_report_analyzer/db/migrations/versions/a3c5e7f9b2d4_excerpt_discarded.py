"""An excerpt discarded from the generated text (still kept and listed)

Revision ID: a3c5e7f9b2d4
Revises: e4b7c9d2a6f1
Create Date: 2026-10-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7f9b2d4"
down_revision: str | None = "e4b7c9d2a6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("discarded", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("discarded")
