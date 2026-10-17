"""Named folder settings: each folder chooses which it uses (none: the default one)

Each folder now uses the settings it used (its own, else those of the first folder above it
having some: ``folder.settings_id``); the one used by most folders is the default (one is
made if there is none; the folders using it, and those that used none: ``NULL``). Each
settings set is named after the folder using it closest to the top (else "Default", or
"Unused"). ``folder_settings_use`` is dropped.

Revision ID: e2a6c8f4b1d9
Revises: c4f7a2e9d1b6
Create Date: 2026-10-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a6c8f4b1d9"
down_revision: str | None = "c4f7a2e9d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _above(parent: dict, folder_id: int) -> list[int]:
    """A folder, then those it is in, up to the top."""
    out, f = [], folder_id
    while f is not None and f not in out:
        out.append(f)
        f = parent.get(f)
    return out


def upgrade() -> None:
    with op.batch_alter_table("folder_settings") as batch:
        batch.add_column(sa.Column("name", sa.String(), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("folder") as batch:
        batch.add_column(sa.Column("settings_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_folder_settings_id",
            "folder_settings",
            ["settings_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_folder_settings_id", ["settings_id"])
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, parent_id FROM folder")).all()
    parent = {i: p for i, _n, p in rows}
    names = {i: n for i, n, _p in rows}
    own = dict(
        conn.execute(sa.text("SELECT folder_id, settings_id FROM folder_settings_use")).all()
    )
    users: dict[int, list[int]] = {}
    for f in parent:
        sid = next((own[g] for g in _above(parent, f) if g in own), None)
        if sid is not None:
            users.setdefault(sid, []).append(f)
            conn.execute(
                sa.text("UPDATE folder SET settings_id = :s WHERE id = :f"), {"s": sid, "f": f}
            )
    settings = [i for (i,) in conn.execute(sa.text("SELECT id FROM folder_settings ORDER BY id"))]
    if not settings:
        settings = [
            conn.execute(
                sa.text("INSERT INTO folder_settings (name, citations) VALUES ('Default', NULL)")
            ).lastrowid
        ]
    default = max(settings, key=lambda s: (len(users.get(s, [])), -s))
    taken: set[str] = set()
    for sid in settings:
        if folders := users.get(sid):
            top = min(folders, key=lambda f: (len(_above(parent, f)), f))
            name = " › ".join(names[g] for g in reversed(_above(parent, top)))
        else:
            name = "Default" if sid == default else "Unused"
        base, k = name, 1
        while name in taken:
            k += 1
            name = f"{base} ({k})"
        taken.add(name)
        conn.execute(
            sa.text("UPDATE folder_settings SET name = :n WHERE id = :s"), {"n": name, "s": sid}
        )
    conn.execute(sa.text("UPDATE folder_settings SET is_default = 1 WHERE id = :s"), {"s": default})
    # (those using the default ones: none chosen)
    conn.execute(
        sa.text("UPDATE folder SET settings_id = NULL WHERE settings_id = :s"), {"s": default}
    )
    with op.batch_alter_table("folder_settings") as batch:
        batch.create_index(
            "uq_folder_settings_default",
            ["is_default"],
            unique=True,
            sqlite_where=sa.text("is_default"),
        )
    op.drop_index("ix_folder_settings_use_settings_id", table_name="folder_settings_use")
    op.drop_table("folder_settings_use")


def downgrade() -> None:
    op.create_table(
        "folder_settings_use",
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("settings_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["settings_id"], ["folder_settings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("folder_id"),
    )
    op.create_index(
        "ix_folder_settings_use_settings_id", "folder_settings_use", ["settings_id"], unique=False
    )
    conn = op.get_bind()
    default = conn.execute(sa.text("SELECT id FROM folder_settings WHERE is_default")).scalar()
    rows = conn.execute(sa.text("SELECT id, parent_id, settings_id FROM folder")).all()
    parent = {i: p for i, p, _s in rows}
    used = {i: s or default for i, _p, s in rows}
    for f, sid in used.items():
        # (a folder using the same as its parent: its parent's, inherited)
        if sid is not None and (parent[f] not in used or used[parent[f]] != sid):
            conn.execute(
                sa.text("INSERT INTO folder_settings_use (folder_id, settings_id) VALUES (:f, :s)"),
                {"f": f, "s": sid},
            )
    with op.batch_alter_table("folder_settings") as batch:
        batch.drop_index("uq_folder_settings_default")
        batch.drop_column("is_default")
        batch.drop_column("name")
    with op.batch_alter_table("folder") as batch:
        batch.drop_index("ix_folder_settings_id")
        batch.drop_constraint("fk_folder_settings_id", type_="foreignkey")
        batch.drop_column("settings_id")
