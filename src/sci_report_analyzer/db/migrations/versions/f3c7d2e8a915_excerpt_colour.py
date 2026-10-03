"""excerpts: a colour

Revision ID: f3c7d2e8a915
Revises: e5b1a9d73c42
Create Date: 2026-10-03 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c7d2e8a915"
down_revision: str | None = "e5b1a9d73c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("colour", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.drop_column("colour")
