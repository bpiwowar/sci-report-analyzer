"""Shared settings: a venue's levels by year range, its former names, its decisions'
authors, its shared id; a variant's author; the venue of a text by period

Revision ID: c3e8a1f5d7b9
Revises: 9b5d3f7a2c61
Create Date: 2026-10-07 00:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f5d7b9"
down_revision: str | None = "9b5d3f7a2c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("venue") as batch:
        batch.add_column(sa.Column("levels", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("decisions", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("share_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("names", sa.JSON(), nullable=True))
    with op.batch_alter_table("venue_text") as batch:
        batch.add_column(sa.Column("periods", sa.JSON(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, level_type, level_rank FROM venue WHERE level_rank IS NOT NULL")
    )
    for vid, ltype, rank in rows.fetchall():
        if rank:
            levels = [{"type": ltype or "conference", "rank": rank}]
            conn.execute(
                sa.text("UPDATE venue SET levels = :l WHERE id = :id"),
                {"l": json.dumps(levels), "id": vid},
            )
    with op.batch_alter_table("venue") as batch:
        batch.drop_column("level_type")
        batch.drop_column("level_rank")
        batch.create_index("ix_venue_share_id", ["share_id"], unique=True)
    with op.batch_alter_table("venue_key") as batch:
        batch.add_column(sa.Column("decided_by", sa.String(), nullable=True))
        batch.add_column(sa.Column("decided_on", sa.String(10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("venue_text") as batch:
        batch.drop_column("periods")
    with op.batch_alter_table("venue_key") as batch:
        batch.drop_column("decided_on")
        batch.drop_column("decided_by")
    with op.batch_alter_table("venue") as batch:
        batch.add_column(sa.Column("level_type", sa.String(16), nullable=True))
        batch.add_column(sa.Column("level_rank", sa.String(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, levels FROM venue WHERE levels IS NOT NULL"))
    for vid, levels in rows.fetchall():
        levels = json.loads(levels) if isinstance(levels, str) else levels
        if levels:  # (the latest range only)
            last = levels[-1]
            conn.execute(
                sa.text("UPDATE venue SET level_type = :t, level_rank = :r WHERE id = :id"),
                {"t": last.get("type"), "r": last.get("rank"), "id": vid},
            )
    with op.batch_alter_table("venue") as batch:
        batch.drop_index("ix_venue_share_id")
        batch.drop_column("share_id")
        batch.drop_column("names")
        batch.drop_column("decisions")
        batch.drop_column("levels")
