"""excerpts: the "rayonnement" flag named in English ("influence")

Revision ID: e5b1a9d73c42
Revises: c2d8f4a61e37
Create Date: 2026-10-03 17:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5b1a9d73c42"
down_revision: str | None = "c2d8f4a61e37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.alter_column("rayonnement", new_column_name="influence")


def downgrade() -> None:
    with op.batch_alter_table("excerpt", schema=None) as batch_op:
        batch_op.alter_column("influence", new_column_name="rayonnement")
