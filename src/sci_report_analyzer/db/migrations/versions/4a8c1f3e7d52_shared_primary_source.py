"""The primary source: one of the folders' settings (shared), no longer of each folder

A folder's primary source (``folder.primary_source``) moves to the settings it uses
(``folder_settings.primary_source``: its own, else those of the first folder above it having
some; none: new ones, of the top folder). Folders sharing settings but not their primary
source: the one set wins (the first folder's, by id), over the default.

Revision ID: 4a8c1f3e7d52
Revises: 2b7d9e4f6a18
Create Date: 2026-10-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a8c1f3e7d52"
down_revision: str | None = "2b7d9e4f6a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _settings_of(conn, parent: dict, own: dict, folder_id: int) -> int:
    """The settings a folder uses (see folders.settings_id): new ones, of the top folder,
    if none."""
    f, seen = folder_id, set()
    while True:
        if f in own:
            return own[f]
        seen.add(f)
        if parent.get(f) is None or parent[f] in seen:
            break
        f = parent[f]
    sid = conn.execute(sa.text("INSERT INTO folder_settings (citations) VALUES (NULL)")).lastrowid
    conn.execute(
        sa.text("INSERT INTO folder_settings_use (folder_id, settings_id) VALUES (:f, :s)"),
        {"f": f, "s": sid},
    )
    own[f] = sid
    return sid


def upgrade() -> None:
    with op.batch_alter_table("folder_settings") as batch:
        batch.add_column(sa.Column("primary_source", sa.String(32), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, parent_id, primary_source FROM folder")).all()
    parent = {i: p for i, p, _src in rows}
    own = {
        k: v
        for k, v in conn.execute(sa.text("SELECT folder_id, settings_id FROM folder_settings_use"))
    }
    chosen: dict[int, str] = {}
    for folder_id, _p, source in sorted(rows):
        if source is not None:
            chosen.setdefault(_settings_of(conn, parent, own, folder_id), source)
    for sid, source in chosen.items():
        conn.execute(
            sa.text("UPDATE folder_settings SET primary_source = :src WHERE id = :s"),
            {"src": source, "s": sid},
        )
    with op.batch_alter_table("folder") as batch:
        batch.drop_column("primary_source")


def downgrade() -> None:
    with op.batch_alter_table("folder") as batch:
        batch.add_column(sa.Column("primary_source", sa.String(32), nullable=True))
    conn = op.get_bind()
    parent = {k: v for k, v in conn.execute(sa.text("SELECT id, parent_id FROM folder"))}
    own = {
        k: v
        for k, v in conn.execute(sa.text("SELECT folder_id, settings_id FROM folder_settings_use"))
    }
    sources = {
        k: v for k, v in conn.execute(sa.text("SELECT id, primary_source FROM folder_settings"))
    }
    for folder_id in parent:
        f, seen = folder_id, set()
        while f is not None and f not in seen and f not in own:
            seen.add(f)
            f = parent.get(f)
        if f in own and sources.get(own[f]) is not None:
            conn.execute(
                sa.text("UPDATE folder SET primary_source = :src WHERE id = :f"),
                {"src": sources[own[f]], "f": folder_id},
            )
    with op.batch_alter_table("folder_settings") as batch:
        batch.drop_column("primary_source")
