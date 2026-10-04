"""category: a colour (that of its excerpts), instead of one per excerpt

Each category takes the colour most of its excerpts had (else the next one of the default
palette, in the folder's order); the excerpts' own colours are dropped.

Revision ID: a1d6f3c8e925
Revises: e7a3c5b9d1f2
Create Date: 2026-10-04 20:00:00.000000
"""

from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d6f3c8e925"
down_revision: str | None = "e7a3c5b9d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (categories.PALETTE, as of this revision)
_PALETTE = ["#ffc107", "#4caf50", "#2196f3", "#e91e63", "#9c27b0", "#ff5722", "#009688", "#795548"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "colour" not in {c["name"] for c in inspector.get_columns("category")}:
        with op.batch_alter_table("category") as batch:
            batch.add_column(sa.Column("colour", sa.String(length=16), nullable=True))
    excerpt_columns = {c["name"] for c in inspector.get_columns("excerpt")}
    used: dict[int, Counter] = {}
    if "colour" in excerpt_columns:
        for cat, colour in bind.execute(
            sa.text("SELECT category_id, colour FROM excerpt WHERE colour IS NOT NULL")
        ):
            used.setdefault(cat, Counter())[colour] += 1
    counts: dict[int, int] = {}
    for cat, folder in bind.execute(
        sa.text("SELECT id, folder_id FROM category WHERE colour IS NULL ORDER BY folder_id, id")
    ):
        k = counts.get(folder, 0)
        counts[folder] = k + 1
        colour = used[cat].most_common(1)[0][0] if cat in used else _PALETTE[k % len(_PALETTE)]
        bind.execute(
            sa.text("UPDATE category SET colour = :c WHERE id = :i"), {"c": colour, "i": cat}
        )
    if "colour" in excerpt_columns:
        with op.batch_alter_table("excerpt") as batch:
            batch.drop_column("colour")


def downgrade() -> None:
    with op.batch_alter_table("excerpt") as batch:
        batch.add_column(sa.Column("colour", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE excerpt SET colour = (SELECT colour FROM category"
        " WHERE category.id = excerpt.category_id)"
    )
    with op.batch_alter_table("category") as batch:
        batch.drop_column("colour")
