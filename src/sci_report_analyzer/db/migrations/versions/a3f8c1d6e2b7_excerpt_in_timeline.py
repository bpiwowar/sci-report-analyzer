"""Whether an excerpt is shown on the person's timeline (left out of it: still kept)

Revision ID: a3f8c1d6e2b7
Revises: d4a7c2e9f5b3
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c1d6e2b7"
down_revision: str | None = "d4a7c2e9f5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("in_timeline", sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("in_timeline")
