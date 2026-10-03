"""edited volumes without a venue: their title names it

Revision ID: 5d2a9c4e1f73
Revises: 8e1f5a2c7b90
Create Date: 2026-10-02 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5d2a9c4e1f73"
down_revision: str | None = "8e1f5a2c7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The document types of edited volumes (ranking.kinds), as stored by the sources.
_TYPES = ("Editorship", "DOUV", "proceedings", "edited-book")


def upgrade() -> None:
    types = ", ".join(f"'{t}'" for t in _TYPES)
    op.execute(
        "UPDATE source_pub SET venue = title"
        " WHERE (venue IS NULL OR venue = '') AND title IS NOT NULL"
        f" AND doc_type IN ({types})"
    )


def downgrade() -> None:
    pass
