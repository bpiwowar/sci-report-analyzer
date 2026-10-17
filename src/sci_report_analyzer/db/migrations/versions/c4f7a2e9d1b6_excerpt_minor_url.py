"""An excerpt's marker "minor" ("mineur") and link; the categories without their years nor
the "rayonnement" one (its excerpts, and those below it, marked "influence" instead)

Revision ID: c4f7a2e9d1b6
Revises: b5d2e8f1a7c3
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a2e9d1b6"
down_revision: str | None = "b5d2e8f1a7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "WITH RECURSIVE below(id) AS (SELECT id FROM category WHERE influence"
        " UNION SELECT c.id FROM category c JOIN below b ON c.parent_id = b.id)"
        " UPDATE excerpt SET influence = 1 WHERE category_id IN (SELECT id FROM below)"
    )
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("minor", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("url", sa.Text(), nullable=True))
    with op.batch_alter_table("category") as batch:
        batch.drop_column("influence")
        batch.drop_column("start_year")
        batch.drop_column("end_year")


def downgrade() -> None:
    with op.batch_alter_table("category") as batch:
        batch.add_column(sa.Column("end_year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("start_year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("influence", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("excerpt") as batch:
        batch.drop_column("url")
        batch.drop_column("minor")
