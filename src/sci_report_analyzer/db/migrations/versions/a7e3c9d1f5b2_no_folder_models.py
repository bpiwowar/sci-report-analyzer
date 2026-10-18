"""No more models chosen by the folder settings (their citations' llm_model, embed_model)

Revision ID: a7e3c9d1f5b2
Revises: f2c8a4d6e1b9
Create Date: 2026-10-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e3c9d1f5b2"
down_revision: str | None = "f2c8a4d6e1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE folder_settings SET citations = "
            "json_remove(citations, '$.llm_model', '$.embed_model') "
            "WHERE citations IS NOT NULL AND json_valid(citations)"
        )
    )


def downgrade() -> None:
    pass  # (the models chosen are gone: the default ones)
