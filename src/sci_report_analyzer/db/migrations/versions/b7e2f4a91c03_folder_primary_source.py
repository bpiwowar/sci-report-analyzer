"""folders: a primary source

Revision ID: b7e2f4a91c03
Revises: d4e8b1c6f720
Create Date: 2026-10-03 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2f4a91c03"
down_revision: str | None = "d4e8b1c6f720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.add_column(sa.Column("primary_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.drop_column("primary_source")
