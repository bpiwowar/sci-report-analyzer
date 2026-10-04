"""cleaning rules: "Proceedings of" and a leading "The" (in the rules saved in the settings)

DOI records' venue texts are now kept as the registry gives them ("Proceedings of the 2018
Conference on …: System Demonstrations"): the front matter the DOI source removed in its
code is removed by these rules, which can be seen and edited.

Revision ID: f8b2c6d4a3e1
Revises: e7a3c5d92b18
Create Date: 2026-10-04 15:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8b2c6d4a3e1"
down_revision: str | None = "e7a3c5d92b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The rules as added (a copy: later changes of the defaults don't change this migration).
RULES = json.loads(
    r"""
[
    {
        "id": "proceedingsOf",
        "name": "Proceedings of",
        "description": "Remove a leading “Proceedings of (the)” (or “Companion proceedings of”).",
        "pattern": "^\\s*(?:companion\\s+)?proceedings\\s+of\\s+(?:the\\s+)?",
        "replacement": "",
        "ignore_case": true,
        "enabled": true,
        "sources": ["doi"],
        "language": null,
        "example": "Proceedings of the 2018 Conference on Widgets: System Demonstrations"
    },
    {
        "id": "leadingThe",
        "name": "Leading “The”",
        "description": "Remove a leading “The”.",
        "pattern": "^\\s*the\\s+",
        "replacement": "",
        "ignore_case": true,
        "enabled": true,
        "sources": ["doi"],
        "language": null,
        "example": "The Journal of Widget Studies"
    }
]
"""
)
IDS = {r["id"] for r in RULES}


def _update(change) -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM app_setting WHERE key = 'matching'")).first()
    if row is None:
        return  # the default rules: they have them
    value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if "norm_rules" not in value:
        return
    value["norm_rules"] = change(value["norm_rules"])
    bind.execute(
        sa.text("UPDATE app_setting SET value = :v WHERE key = 'matching'"),
        {"v": json.dumps(value)},
    )


def upgrade() -> None:
    def change(rules: list[dict]) -> list[dict]:
        ids = {x.get("id") for x in rules}
        return [r for r in RULES if r["id"] not in ids] + rules

    _update(change)


def downgrade() -> None:
    _update(lambda rules: [r for r in rules if r.get("id") not in IDS])
