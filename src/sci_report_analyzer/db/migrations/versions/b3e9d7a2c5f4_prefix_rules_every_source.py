"""cleaning rules: "Proceedings of" and a leading "The" for every source

They applied to the DOI texts only; "Proceedings of" is now removed before a conference's
name only (not "Proceedings of the IEEE"); English rules (Settings, by language). The saved rules as added by f8b2c6d4a3e1 (not
edited) are replaced.

Revision ID: b3e9d7a2c5f4
Revises: f8b2c6d4a3e1
Create Date: 2026-10-04 16:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e9d7a2c5f4"
down_revision: str | None = "f8b2c6d4a3e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The rules as changed (a copy: later changes of the defaults don't change this migration).
RULES = {
    r["id"]: r
    for r in json.loads(
        r"""
[
    {
        "id": "proceedingsOf",
        "name": "Proceedings of",
        "description": "Remove a leading “Proceedings of (the)” (or “Companion proceedings of”) before a conference's name: followed by a year, an ordinal or an edition (“COLING 2012”), or naming a conference, workshop, symposium, meeting… (“Proceedings of the IEEE” is kept).",
        "pattern": "^\\s*(?:companion\\s+)?proceedings\\s+of\\s+(?:the\\s+)?(?=(?:(?:19|20)\\d{2}|\\d+(?:st|nd|rd|th)|{ordinals:en})\\b|\\S+\\s+(?:19|20)\\d{2}\\b|.*\\b(?:conferences?|workshops?|symposium|meeting|congress|colloquium|forum|summit)\\b)",
        "replacement": "",
        "ignore_case": true,
        "enabled": true,
        "sources": [],
        "language": "en",
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
        "sources": [],
        "language": "en",
        "example": "The Journal of Widget Studies"
    }
]
"""
    )
}

# The rules as f8b2c6d4a3e1 added them.
OLD = {
    r["id"]: r
    for r in json.loads(
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
        "sources": [
            "doi"
        ],
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
        "sources": [
            "doi"
        ],
        "language": null,
        "example": "The Journal of Widget Studies"
    }
]
"""
    )
}


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


def _replace(rules: list[dict], old: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    def unchanged(r: dict) -> bool:
        o = old.get(r.get("id"))
        return o is not None and all(r.get(k, v) == v for k, v in o.items())

    return [new[r["id"]] if unchanged(r) else r for r in rules]


def upgrade() -> None:
    _update(lambda rules: _replace(rules, OLD, RULES))


def downgrade() -> None:
    _update(lambda rules: _replace(rules, RULES, OLD))
