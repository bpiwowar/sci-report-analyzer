"""detection rules: no shared task rules, by language (in the rules saved in the settings)

The shared task rules (their venues, the campaigns' names, their papers' titles) are gone:
too specific to some communities (a shared task is now set by hand). The rules mixing
English and French words are split by language ("workshop" / "atelier"): saved with their
former default, they are dropped (they take their new default); edited, they are kept.

Revision ID: d7f3a9c2e186
Revises: b3e9d7a2c5f4
Create Date: 2026-10-04 18:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3a9c2e186"
down_revision: str | None = "b3e9d7a2c5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GONE = {"shared_task_venue", "campaigns", "shared_task_title"}
# The former defaults of the rules split by language (a copy).
OLD = json.loads(
    r"""
[
    {
        "id": "workshop",
        "pattern": "\\bworkshops?\\b|\\bateliers?\\b|(?-i:(?<=\\w)\\s*@\\s*(?=[A-Z]))|\\bco-located\\b|\\bin conjunction with\\b",
        "ignore_case": true
    },
    {
        "id": "workshop_host",
        "pattern": "(?-i:(?<=\\w)\\s*@\\s*)(?P<a>[^,:;()]+)|(?:co-located|in conjunction) with (?:the )?(?P<b>[^,:;()]+)",
        "ignore_case": true
    },
    {
        "id": "conference",
        "pattern": "\\b(conf(erence)?|symposium|workshops?|proceedings|proc\\.|meeting|congress|colloquium|conférence|colloque|journées|atelier|rencontres|forum|summit)\\b",
        "ignore_case": true
    },
    {
        "id": "journal",
        "pattern": "\\b(journal|transactions|trans\\.|revue|letters|review|magazine|annals|bulletin|quarterly|j\\.)\\b",
        "ignore_case": true
    }
]
"""
)
OLD_BY_ID = {r["id"]: r for r in OLD}


def _stale(rule: dict) -> bool:
    old = OLD_BY_ID.get(rule.get("id"))
    if old is not None:
        return (rule.get("pattern"), rule.get("ignore_case", True)) == (
            old["pattern"],
            old["ignore_case"],
        )
    return rule.get("id") in GONE


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM app_setting WHERE key = 'matching'")).first()
    if row is None:
        return  # the default rules: they have them
    value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if "detection_rules" not in value:
        return
    value["detection_rules"] = [r for r in value["detection_rules"] if not _stale(r)]
    bind.execute(
        sa.text("UPDATE app_setting SET value = :v WHERE key = 'matching'"),
        {"v": json.dumps(value)},
    )


def downgrade() -> None:
    pass  # the rules missing take their (former) default; the new ones are dropped
