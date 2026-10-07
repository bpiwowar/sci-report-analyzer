"""An excerpt's markers besides influence: salient ("saillant") and caveat ("bémol")

Revision ID: d8e2f4a6b1c3
Revises: a5d2e8c4f1b7
Create Date: 2026-10-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e2f4a6b1c3"
down_revision: str | None = "a5d2e8c4f1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("salient", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("caveat", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("caveat")
        batch.drop_column("salient")
