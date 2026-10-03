"""report: how a paper is cited when inserted (superseded: an app setting, see the next)

Revision ID: 9c4e7b2a5d18
Revises: 5d2a9c4e1f73
Create Date: 2026-10-03 10:00:00.000000
"""

from collections.abc import Sequence

revision: str = "9c4e7b2a5d18"
down_revision: str | None = "5d2a9c4e1f73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass  # (it added report.cite_form, dropped by the next revision)


def downgrade() -> None:
    pass
