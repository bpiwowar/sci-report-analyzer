"""Star lists: the per-period tags are the folders' ordered lists, the other tags plain

The tags put within a period (e.g. "starred", the ★ button) become the lists of the folders'
settings (``tag.settings_id``), each with the format of its papers' numbers, whether its
papers are to be cited in the notes, and its place among them (a list's name: unique within
its settings, a tag's among the tags); the tag a folder numbered its
papers with (``citations.tag_id``) becomes such a list of its settings (its format, the
"listed" one; to be cited), the format of the others staying ``citations.format``. The global
tags lose their numbers (a list's only: ``publication_tag.number``).

Revision ID: 8f4c2a7e1b93
Revises: 5e2b7c9d1a46
Create Date: 2026-10-06 00:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f4c2a7e1b93"
down_revision: str | None = "5e2b7c9d1a46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBER_FORMAT = "**#{index}**"


def _tag_table(name: str, unique_name: bool) -> None:
    """The tag table as it is (``unique_name``: before) or becomes."""
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("colour", sa.String(16), nullable=False),
        sa.Column("per_period", sa.Boolean(), nullable=False),
        sa.Column("key", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    ]
    if unique_name:
        columns.append(sa.UniqueConstraint("name"))
    else:
        columns += [
            sa.Column(
                "settings_id",
                sa.Integer(),
                sa.ForeignKey("folder_settings.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("number_format", sa.String(), nullable=True),
            sa.Column("must_cite", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("name", "settings_id", name="uq_tag_name_settings"),
        ]
    op.create_table(name, *columns)


_COLUMNS = "id, name, colour, per_period, key"


def upgrade() -> None:
    # (recreated: a list's name unique within its settings only; the foreign keys are off)
    _tag_table("_tag_new", unique_name=False)
    op.execute(f"INSERT INTO _tag_new ({_COLUMNS}) SELECT {_COLUMNS} FROM tag")
    op.drop_table("tag")
    op.rename_table("_tag_new", "tag")
    op.create_index(
        "uq_tag_global_name",
        "tag",
        ["name"],
        unique=True,
        sqlite_where=sa.text("settings_id IS NULL"),
    )
    with op.batch_alter_table("publication_tag") as t:
        t.drop_column("number")

    conn = op.get_bind()
    per_period = {
        r.id for r in conn.execute(sa.text("SELECT id FROM tag WHERE per_period")).fetchall()
    }
    owners: dict[int, int] = {}  # (the list -> the settings numbering with it, the first)
    for sid, raw in conn.execute(
        sa.text("SELECT id, citations FROM folder_settings ORDER BY id")
    ).fetchall():
        c = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        tag_id = c.pop("tag_id", None)
        listed = c.pop("listed_format", None) or NUMBER_FORMAT
        if tag_id in per_period and tag_id not in owners:
            owners[tag_id] = sid
            conn.execute(
                sa.text(
                    "UPDATE tag SET settings_id = :sid, number_format = :fmt, must_cite = 1 "
                    "WHERE id = :id"
                ),
                {"sid": sid, "fmt": listed, "id": tag_id},
            )
        conn.execute(
            sa.text("UPDATE folder_settings SET citations = :c WHERE id = :id"),
            {"c": json.dumps(c), "id": sid},
        )
    # (the other lists: of every folder, numbered as by default)
    conn.execute(
        sa.text("UPDATE tag SET number_format = :fmt WHERE per_period AND number_format IS NULL"),
        {"fmt": NUMBER_FORMAT},
    )


def downgrade() -> None:
    with op.batch_alter_table("publication_tag") as t:
        t.add_column(sa.Column("number", sa.Integer(), nullable=True))
    op.drop_index("uq_tag_global_name", table_name="tag")
    _tag_table("_tag_old", unique_name=True)
    op.execute(f"INSERT INTO _tag_old ({_COLUMNS}) SELECT {_COLUMNS} FROM tag")
    op.drop_table("tag")
    op.rename_table("_tag_old", "tag")
