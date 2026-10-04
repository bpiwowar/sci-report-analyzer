"""folders: nested, their settings apart (shared)

A folder can be within another one (``folder.parent_id``). Its settings (its categories,
and how its notes cite papers: ``folder.citations``) move to their own table
(``folder_settings``), which folders use (``folder_settings_use``; a folder without uses
its parent's). Each folder gets its own: its citations, and its categories (with their
excerpts) as they were.

Revision ID: f6b3d8a2c5e9
Revises: a1d6f3c8e925
Create Date: 2026-10-04 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b3d8a2c5e9"
down_revision: str | None = "a1d6f3c8e925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """(Each step only if not done yet: run again on a database upgraded already, the
    settings stay as they are.)"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "folder_settings" not in tables:
        op.create_table(
            "folder_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("citations", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if "folder_settings_use" not in tables:
        op.create_table(
            "folder_settings_use",
            sa.Column("folder_id", sa.Integer(), nullable=False),
            sa.Column("settings_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["settings_id"], ["folder_settings.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("folder_id"),
        )
        op.create_index(
            "ix_folder_settings_use_settings_id",
            "folder_settings_use",
            ["settings_id"],
            unique=False,
        )
    folder_columns = {c["name"] for c in inspector.get_columns("folder")}
    if "citations" in folder_columns:
        # Each folder: its own settings (of the same id), with its citations and categories.
        mine = "id NOT IN (SELECT folder_id FROM folder_settings_use)"
        op.execute(
            f"INSERT INTO folder_settings (id, citations) SELECT id, citations FROM folder WHERE {mine}"
        )
        op.execute(
            f"INSERT INTO folder_settings_use (folder_id, settings_id) SELECT id, id FROM folder WHERE {mine}"
        )
    if "folder_id" in {c["name"] for c in inspector.get_columns("category")}:
        with op.batch_alter_table("category") as batch:
            batch.add_column(sa.Column("settings_id", sa.Integer(), nullable=True))
        op.execute(
            "UPDATE category SET settings_id = (SELECT settings_id FROM folder_settings_use u"
            " WHERE u.folder_id = category.folder_id)"
        )
        with op.batch_alter_table("category") as batch:
            batch.drop_index("ix_category_folder_id")
            batch.drop_column("folder_id")
            batch.alter_column("settings_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                "fk_category_settings_id",
                "folder_settings",
                ["settings_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_index("ix_category_settings_id", ["settings_id"], unique=False)
    with op.batch_alter_table("folder") as batch:
        if "citations" in folder_columns:
            batch.drop_column("citations")
        if "parent_id" not in folder_columns:
            batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_folder_parent_id", "folder", ["parent_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_index("ix_folder_parent_id", ["parent_id"], unique=False)


def _effective(bind) -> dict[int, int | None]:
    """The settings each folder uses: its own, else its parent's (none: neither)."""
    parent = dict(bind.execute(sa.text("SELECT id, parent_id FROM folder")).all())
    own = dict(
        bind.execute(sa.text("SELECT folder_id, settings_id FROM folder_settings_use")).all()
    )
    out: dict[int, int | None] = {}
    for f in parent:
        seen, g = set(), f
        while g is not None and g not in own and g not in seen:
            seen.add(g)
            g = parent.get(g)
        out[f] = own.get(g) if g is not None else None
    return out


def downgrade() -> None:
    """Each folder takes back the settings it uses: their citations, and their categories
    (the first folder using them takes them; each other one, a copy with its excerpts)."""
    bind = op.get_bind()
    with op.batch_alter_table("folder") as batch:
        batch.add_column(sa.Column("citations", sa.JSON(), nullable=True))
    with op.batch_alter_table("category") as batch:
        batch.add_column(sa.Column("folder_id", sa.Integer(), nullable=True))
    effective = _effective(bind)
    users: dict[int, list[int]] = {}
    for f, sid in sorted(effective.items()):
        if sid is not None:
            users.setdefault(sid, []).append(f)
    columns = "parent_id, name, position, start_year, end_year, influence, colour"
    for sid, folders in users.items():
        bind.execute(
            sa.text(
                "UPDATE folder SET citations = (SELECT citations FROM folder_settings"
                " WHERE id = :s) WHERE id IN (" + ", ".join(map(str, folders)) + ")"
            ),
            {"s": sid},
        )
        bind.execute(
            sa.text("UPDATE category SET folder_id = :f WHERE settings_id = :s"),
            {"f": folders[0], "s": sid},
        )
        cats = bind.execute(
            sa.text(f"SELECT id, {columns} FROM category WHERE settings_id = :s ORDER BY id"),
            {"s": sid},
        ).all()
        for f in folders[1:]:
            ids: dict[int, int] = {}
            pending = list(cats)
            while pending:  # (parents first)
                row = next(r for r in pending if r[1] is None or r[1] in ids)
                pending.remove(row)
                new = bind.execute(
                    sa.text(
                        f"INSERT INTO category (settings_id, folder_id, {columns}) VALUES"
                        " (:s, :f, :parent, :name, :pos, :a, :b, :inf, :colour)"
                    ),
                    {
                        "s": sid,
                        "f": f,
                        "parent": ids.get(row[1]),
                        "name": row[2],
                        "pos": row[3],
                        "a": row[4],
                        "b": row[5],
                        "inf": row[6],
                        "colour": row[7],
                    },
                )
                ids[row[0]] = new.lastrowid
            for old, new in ids.items():
                bind.execute(
                    sa.text(
                        "UPDATE excerpt SET category_id = :n WHERE category_id = :o"
                        " AND period_id IN (SELECT id FROM period WHERE folder_id = :f)"
                    ),
                    {"n": new, "o": old, "f": f},
                )
    op.execute("DELETE FROM category WHERE folder_id IS NULL")  # (settings no folder used)
    with op.batch_alter_table("category") as batch:
        batch.drop_index("ix_category_settings_id")
        batch.drop_constraint("fk_category_settings_id", type_="foreignkey")
        batch.drop_column("settings_id")
        batch.alter_column("folder_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_category_folder_id", "folder", ["folder_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_category_folder_id", ["folder_id"], unique=False)
    with op.batch_alter_table("folder") as batch:
        batch.drop_index("ix_folder_parent_id")
        batch.drop_constraint("fk_folder_parent_id", type_="foreignkey")
        batch.drop_column("parent_id")
    op.drop_index("ix_folder_settings_use_settings_id", table_name="folder_settings_use")
    op.drop_table("folder_settings_use")
    op.drop_table("folder_settings")
