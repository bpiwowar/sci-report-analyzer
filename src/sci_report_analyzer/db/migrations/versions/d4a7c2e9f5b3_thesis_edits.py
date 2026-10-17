"""PhD students edited by hand: their dates set over theses.fr's, hidden (with a comment),
added (a thesis without a source, of a person)

Revision ID: d4a7c2e9f5b3
Revises: a7d3f9c2e5b8
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7c2e9f5b3"
down_revision: str | None = "a7d3f9c2e5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("thesis") as batch:
        batch.alter_column("link_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("person_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_thesis_person_id", "person", ["person_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_thesis_person_id", ["person_id"])
        batch.add_column(sa.Column("start_override", sa.String(10), nullable=True))
        batch.add_column(sa.Column("end_override", sa.String(10), nullable=True))
        batch.add_column(
            sa.Column("hidden", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
        batch.add_column(sa.Column("comment", sa.String(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM thesis WHERE link_id IS NULL")
    with op.batch_alter_table("thesis") as batch:
        batch.drop_column("comment")
        batch.drop_column("hidden")
        batch.drop_column("end_override")
        batch.drop_column("start_override")
        batch.drop_index("ix_thesis_person_id")
        batch.drop_constraint("fk_thesis_person_id", type_="foreignkey")
        batch.drop_column("person_id")
        batch.alter_column("link_id", existing_type=sa.Integer(), nullable=False)
