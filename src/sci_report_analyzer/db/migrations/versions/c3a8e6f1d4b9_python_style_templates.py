"""citations: templates in Python's style (reverted: see d4b2f8a6c1e3)

Converted the citations and templates to ``{short-venue} ({year})``; reverted to Pandoc's
classes (``.short-venue (.year)``), it does nothing now (the next revision converts back
the databases it converted).

Revision ID: c3a8e6f1d4b9
Revises: b5e1f7c3a9d2
Create Date: 2026-10-04 16:00:00.000000
"""

from collections.abc import Sequence

revision: str = "c3a8e6f1d4b9"
down_revision: str | None = "b5e1f7c3a9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
