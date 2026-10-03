"""cleaning rules: ordinals, by language (in the rules saved in the settings)

The English and French rules (digits and words: 35th, Fourteenth, 17e, quatorzième)
replace the general "Ordinals" rule (dropped if it was not edited).

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
        "id": "ordinalsEn",
        "name": "Ordinals",
        "description": "Remove ordinals: 1st, 35th…, and in words, first to thousandth (twenty-first, one hundred and first…).",
        "pattern": "\\b(?:\\d+(?:st|nd|rd|th)|{ordinals:en})\\b",
        "replacement": " ",
        "ignore_case": true,
        "enabled": true,
        "sources": [],
        "language": "en",
        "example": "Fourteenth ACM Conference on Recommender Systems"
    },
    {
        "id": "ordinalsFr",
        "name": "Ordinals",
        "description": "Remove ordinals: 1er, 17e, 22èmes…, and in words, premier to millième (second, vingt et unième…).",
        "pattern": "(?<![\\wÀ-ÿ])(?:\\d+(?:e|er|re|[eèé]re|i?[eè]me)s?|{ordinals:fr})(?![\\wÀ-ÿ])",
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
# The general rule they replace, as it was by default.
OLD = {
    "id": "ordinals",
    "name": "Ordinals",
    "description": "Remove ordinal numbers such as 45th, 1st, 17e, 22èmes.",
    "pattern": r"\b\d+(?:st|nd|rd|th|e|er|eme|ème)s?\b",
    "replacement": " ",
    "ignore_case": True,
    "enabled": True,
    "sources": [],
    "example": "45th Annual Meeting",
}


def _unchanged(rule: dict) -> bool:
    return rule.get("id") == "ordinals" and all(rule.get(k, v) == v for k, v in OLD.items())


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
        return [r for r in RULES if r["id"] not in ids] + [r for r in rules if not _unchanged(r)]

    _update(change)


def downgrade() -> None:
    def change(rules: list[dict]) -> list[dict]:
        kept = [r for r in rules if r.get("id") not in IDS]
        if any(r.get("id") == "ordinals" for r in kept):
            return kept
        at = next((i for i, r in enumerate(kept) if r.get("id") == "years"), 0)
        return [*kept[:at], OLD, *kept[at:]]

    _update(change)
