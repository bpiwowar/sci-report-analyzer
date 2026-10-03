"""report: drop cite_form (the citation templates are an app setting)

Revision ID: a7e3c1f9b250
Revises: 9c4e7b2a5d18
Create Date: 2026-10-03 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e3c1f9b250"
down_revision: str | None = "9c4e7b2a5d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("report")}
    if "cite_form" in columns:
        with op.batch_alter_table("report", schema=None) as batch_op:
            batch_op.drop_column("cite_form")


def downgrade() -> None:
    pass
