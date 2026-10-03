"""cleaning rules: spelled ordinals, by language (added to the rules saved in the settings)

Revision ID: a9c3e5f17d28
Revises: b7e2f4a91c03
Create Date: 2026-10-03 22:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c3e5f17d28"
down_revision: str | None = "b7e2f4a91c03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The rules as added (a copy: later changes of the defaults don't change this migration).
RULES = json.loads(
    r"""
[
    {
        "id": "ordinalWordsEn",
        "name": "Spelled ordinals",
        "description": "Remove ordinals in words (…th, first, second, third) before an event word (Annual, Conference, Workshop…).",
        "pattern": "\\b(?:[a-z]+-)?(?:first|second|third|(?:four|fif|six|seven|eigh|nin|ten|eleven|twelf|[a-z]+teen|[a-z]+ie|hundred)th)\\b(?=\\s+(?:annual|international|national|european|asian|joint|acm|ieee|conference|workshop|symposium|meeting|congress|colloquium|edition|forum|summit)\\b)",
        "replacement": " ",
        "ignore_case": true,
        "enabled": true,
        "sources": [],
        "language": "en",
        "example": "Fourteenth ACM Conference on Recommender Systems"
    },
    {
        "id": "ordinalWordsFr",
        "name": "Spelled ordinals",
        "description": "Remove ordinals in words (…ième, premier, première).",
        "pattern": "(?<![a-zà-ÿ])(?:[a-z-]+i[eè]me|premi(?:er|[eè]re))s?(?![a-zà-ÿ])",
        "replacement": " ",
        "ignore_case": true,
        "enabled": true,
        "sources": [],
        "language": "fr",
        "example": "Quatorzième conférence en recherche d'information"
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
    _update(lambda rules: [r for r in RULES if r["id"] not in {x["id"] for x in rules}] + rules)


def downgrade() -> None:
    _update(lambda rules: [r for r in rules if r.get("id") not in IDS])
