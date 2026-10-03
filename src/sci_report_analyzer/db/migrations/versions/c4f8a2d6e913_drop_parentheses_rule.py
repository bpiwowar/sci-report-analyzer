"""cleaning rules: no "Parentheses" rule (in the rules saved in the settings)

It removed any parenthesised text, tracks too ("COLING (Demonstrations): ..."); dropped if
it was not edited. Parenthesised acronyms are still removed (their own rule).

Revision ID: c4f8a2d6e913
Revises: a9c3e5f17d28
Create Date: 2026-10-03 23:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a2d6e913"
down_revision: str | None = "a9c3e5f17d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The rule as it was by default.
OLD = {
    "id": "parentheses",
    "name": "Parentheses",
    "description": "Remove parenthesised text (one level of nesting).",
    "pattern": r"\((?:[^()]|\([^()]*\))*\)",
    "replacement": " ",
    "ignore_case": False,
    "enabled": True,
    "sources": [],
    "example": "Neural Information Processing Systems (NeurIPS)",
}


def _unchanged(rule: dict) -> bool:
    return rule.get("id") == "parentheses" and all(rule.get(k, v) == v for k, v in OLD.items())


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
    _update(lambda rules: [r for r in rules if not _unchanged(r)])


def downgrade() -> None:
    def change(rules: list[dict]) -> list[dict]:
        if any(r.get("id") == "parentheses" for r in rules):
            return rules
        at = next((i for i, r in enumerate(rules) if r.get("id") == "parenAcronym"), 0)
        return [*rules[:at], OLD, *rules[at:]]

    _update(change)
