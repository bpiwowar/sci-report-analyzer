"""cleaning rules: ordinals replaced by "Zth" / "Zème" (in the rules saved in the settings)

Easier to read than their removal; left out of the keys, so the matching is the same.
Only the rules whose replacement was not edited change.

Revision ID: d2b7e4c91a56
Revises: c4f8a2d6e913
Create Date: 2026-10-04 11:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2b7e4c91a56"
down_revision: str | None = "c4f8a2d6e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (A copy: later changes of the defaults don't change this migration.)
MARKS = {"ordinalsEn": "Zth", "ordinalsFr": "Zème"}
OLD_DESCRIPTIONS = {
    "ordinalsEn": "Remove ordinals: 1st, 35th…, and in words, first to thousandth "
    "(twenty-first, one hundred and first…).",
    "ordinalsFr": "Remove ordinals: 1er, 17e, 22èmes…, and in words, premier to millième "
    "(second, vingt et unième…).",
}
DESCRIPTIONS = {
    "ordinalsEn": "Replace ordinals by “Zth” (left out of the keys): 1st, 35th…, and in "
    "words, first to thousandth (twenty-first, one hundred and first…).",
    "ordinalsFr": "Replace ordinals by “Zème” (left out of the keys): 1er, 17e, 22èmes…, "
    "and in words, premier to millième (second, vingt et unième…).",
}


def _update(old: dict, new: dict, old_text: dict, new_text: dict) -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM app_setting WHERE key = 'matching'")).first()
    if row is None:
        return  # the default rules: they have them
    value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    for r in value.get("norm_rules") or []:
        rid = r.get("id")
        if rid in old and r.get("replacement", " ") == old[rid]:
            r["replacement"] = new[rid]
            if r.get("description") == old_text[rid]:
                r["description"] = new_text[rid]
    bind.execute(
        sa.text("UPDATE app_setting SET value = :v WHERE key = 'matching'"),
        {"v": json.dumps(value)},
    )


def upgrade() -> None:
    _update(dict.fromkeys(MARKS, " "), MARKS, OLD_DESCRIPTIONS, DESCRIPTIONS)


def downgrade() -> None:
    _update(MARKS, dict.fromkeys(MARKS, " "), DESCRIPTIONS, OLD_DESCRIPTIONS)
