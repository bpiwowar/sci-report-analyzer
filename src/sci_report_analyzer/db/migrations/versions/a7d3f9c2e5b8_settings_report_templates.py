"""Report templates per named settings (with their categories, which their blocks name)

The general report templates (the AppSetting ``report_skeletons``) are copied into every
settings set (``folder_settings.report_templates``), then dropped; back, those of the
default settings become the general ones.

Revision ID: a7d3f9c2e5b8
Revises: e2a6c8f4b1d9
Create Date: 2026-10-17 18:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f9c2e5b8"
down_revision: str | None = "e2a6c8f4b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "report_skeletons"


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def upgrade() -> None:
    with op.batch_alter_table("folder_settings") as batch:
        batch.add_column(sa.Column("report_templates", sa.JSON(), nullable=True))
    conn = op.get_bind()
    saved = conn.execute(
        sa.text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY}
    ).scalar()
    if saved is not None:
        items = (_json(saved) or {}).get("items") or []
        conn.execute(
            sa.text("UPDATE folder_settings SET report_templates = :t"), {"t": json.dumps(items)}
        )
        conn.execute(sa.text("DELETE FROM app_setting WHERE key = :k"), {"k": KEY})


def downgrade() -> None:
    conn = op.get_bind()
    items = conn.execute(
        sa.text("SELECT report_templates FROM folder_settings WHERE is_default")
    ).scalar()
    if items is not None:
        conn.execute(
            sa.text("INSERT OR REPLACE INTO app_setting (key, value) VALUES (:k, :v)"),
            {"k": KEY, "v": json.dumps({"items": _json(items)})},
        )
    with op.batch_alter_table("folder_settings") as batch:
        batch.drop_column("report_templates")
