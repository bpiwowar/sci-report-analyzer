"""The embeddings of texts (a cache), and the classifiers suggesting an excerpt's category

Revision ID: b5d2e8f1a7c3
Revises: a3f8c1e6b2d4
Create Date: 2026-10-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5d2e8f1a7c3"
down_revision: str | None = "a3f8c1e6b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding",
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("model", "digest"),
    )
    op.create_table(
        "excerpt_classifier",
        sa.Column("settings_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("trained_at", sa.DateTime(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("centroids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["settings_id"], ["folder_settings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("settings_id"),
    )


def downgrade() -> None:
    op.drop_table("excerpt_classifier")
    op.drop_table("embedding")
