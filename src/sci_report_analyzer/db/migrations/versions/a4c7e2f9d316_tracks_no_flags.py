"""tracks as settings, a paper's track set by hand, no flags

Flags are gone. A paper's track is set by hand (``publication.track_override``: a track's
id, "main" for the main track, NULL: automatic): a paper with a flag having a track gets
that track. A flag's colour with a track becomes the track's (the tracks, with their names,
colours and rules, are in the matching settings); the track rules saved among the detection
rules move into their tracks. A flag without a track becomes a tag of the same name and
colour, on the same papers. Then the flag tables are dropped.

Revision ID: a4c7e2f9d316
Revises: d7f3a9c2e186
Create Date: 2026-10-04 20:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c7e2f9d316"
down_revision: str | None = "d7f3a9c2e186"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The built-in tracks (a copy: ranking.tracks.DEFAULT_TRACKS), in their order.
TRACKS = json.loads(
    r"""
[
    {
        "id": "findings",
        "names": {"en": "Findings", "fr": "Findings"},
        "colour": "#2f6fb0",
        "rules": [
            {"id": "track_findings", "pattern": "\\bfindings\\b", "ignore_case": true,
             "language": "en",
             "examples": ["Findings of the Association for Computational Linguistics: ACL 2023"]}
        ]
    },
    {
        "id": "tutorial",
        "names": {"en": "Tutorial", "fr": "Tutoriel"},
        "colour": "#1a7f37",
        "rules": [
            {"id": "track_tutorial", "pattern": "\\btutorials?\\b", "ignore_case": true,
             "language": "en", "examples": ["ECIR 2024 Tutorials"]},
            {"id": "track_tutorial_fr", "pattern": "\\btutoriels?\\b", "ignore_case": true,
             "language": "fr", "examples": ["Foo 2024, tutoriels"]}
        ]
    },
    {
        "id": "demo",
        "names": {"en": "Demo", "fr": "Démo"},
        "colour": "#8a6fd0",
        "rules": [
            {"id": "track_demo", "pattern": "\\b(?:demos?|demonstrations?)\\b",
             "ignore_case": true, "language": "en",
             "examples": ["ACL 2023 (System Demonstrations)"]},
            {"id": "track_demo_fr", "pattern": "\\b(?:démos?|démonstrations?)\\b",
             "ignore_case": true, "language": "fr", "examples": ["Foo 2024 (Démonstrations)"]}
        ]
    },
    {
        "id": "short",
        "names": {"en": "Short", "fr": "Court"},
        "colour": "#d4a72c",
        "rules": [
            {"id": "track_short", "pattern": "\\bshort papers?\\b", "ignore_case": true,
             "language": "en", "examples": ["ACL 2022 (Volume 2: Short Papers)"]},
            {"id": "track_short_fr", "pattern": "\\barticles? courts?\\b", "ignore_case": true,
             "language": "fr", "examples": ["Foo 2024 (Articles courts)"]}
        ]
    }
]
"""
)
TRACK_IDS = {t["id"] for t in TRACKS}


def _settings(bind) -> dict | None:
    row = bind.execute(sa.text("SELECT value FROM app_setting WHERE key = 'matching'")).first()
    if row is None:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def _tracks(value: dict | None, colours: dict[str, str]) -> tuple[dict, bool]:
    """The settings with their tracks (the flags' colours, the track rules saved among the
    detection rules), and whether they changed from the defaults."""
    value = dict(value or {})
    defs = json.loads(json.dumps(TRACKS))
    changed = False
    saved = {r.get("id"): r for r in value.get("detection_rules") or []}
    for t in defs:
        if colours.get(t["id"], t["colour"]) != t["colour"]:
            t["colour"], changed = colours[t["id"]], True
        for r in t["rules"]:
            if (s := saved.get(r["id"])) is not None:
                edited = (s.get("pattern"), s.get("ignore_case", True))
                if edited != (r["pattern"], r["ignore_case"]):
                    r["pattern"], r["ignore_case"], changed = edited[0], edited[1], True
    if "detection_rules" in value:
        value["detection_rules"] = [
            r for r in value["detection_rules"] if not str(r.get("id", "")).startswith("track_")
        ]
    value["tracks"] = defs
    return value, changed


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "track_override" not in {c["name"] for c in inspector.get_columns("publication")}:
        # (A plain ADD COLUMN: a batch copy of the table could cascade-delete its rows'
        # links.)
        op.add_column("publication", sa.Column("track_override", sa.String(32), nullable=True))
    if not inspector.has_table("flag"):
        return  # (already done)
    flags = bind.execute(sa.text("SELECT id, name, colour, track FROM flag ORDER BY id")).all()
    on = bind.execute(
        sa.text("SELECT publication_id, flag_id FROM publication_flag ORDER BY flag_id")
    ).all()
    by_id = {f.id: f for f in flags}

    # A flag with a (known) track: the paper's track; the first one when several.
    for pub_id, flag_id in on:
        f = by_id.get(flag_id)
        if f is not None and f.track in TRACK_IDS:
            bind.execute(
                sa.text(
                    "UPDATE publication SET track_override = :t "
                    "WHERE id = :p AND track_override IS NULL"
                ),
                {"t": f.track, "p": pub_id},
            )

    # The others: tags (an existing global tag of that name is reused).
    for f in flags:
        if f.track in TRACK_IDS:
            continue
        tag = bind.execute(
            sa.text("SELECT id, per_period FROM tag WHERE name = :n"), {"n": f.name}
        ).first()
        name = f.name
        if tag is not None and tag.per_period:
            name, tag = f"{f.name} (flag)", None  # (a per-period tag cannot be global)
        if tag is None:
            bind.execute(
                sa.text(
                    "INSERT INTO tag (name, colour, per_period) VALUES (:n, :c, 0) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"n": name, "c": f.colour or "#57606a"},
            )
            tag = bind.execute(sa.text("SELECT id FROM tag WHERE name = :n"), {"n": name}).first()
        for pub_id, flag_id in on:
            if flag_id == f.id:
                bind.execute(
                    sa.text(
                        "INSERT INTO publication_tag (publication_id, tag_id) VALUES (:p, :t) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"p": pub_id, "t": tag.id},
                )

    # The tracks in the settings, with the flags' colours.
    colours = {f.track: f.colour for f in flags if f.track in TRACK_IDS and f.colour}
    current = _settings(bind)
    value, changed = _tracks(current, colours)
    if current is not None:
        bind.execute(
            sa.text("UPDATE app_setting SET value = :v WHERE key = 'matching'"),
            {"v": json.dumps(value)},
        )
    elif changed:
        bind.execute(
            sa.text("INSERT INTO app_setting (key, value) VALUES ('matching', :v)"),
            {"v": json.dumps(value)},
        )

    op.drop_table("publication_flag")
    op.drop_table("flag")


def downgrade() -> None:
    op.create_table(
        "flag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("colour", sa.String(), nullable=False),
        sa.Column("track", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "publication_flag",
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("flag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["flag_id"], ["flag.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("publication_id", "flag_id"),
    )
    # A flag per track (in its colour), on the papers of that track set by hand.
    bind = op.get_bind()
    value = _settings(bind) or {}
    for t in value.get("tracks") or TRACKS:
        if t.get("id") not in TRACK_IDS:
            continue
        bind.execute(
            sa.text("INSERT INTO flag (name, colour, track) VALUES (:n, :c, :t)"),
            {"n": t["id"], "c": t.get("colour") or "#57606a", "t": t["id"]},
        )
    bind.execute(
        sa.text(
            "INSERT INTO publication_flag (publication_id, flag_id) "
            "SELECT p.id, f.id FROM publication p JOIN flag f ON f.track = p.track_override"
        )
    )
    op.drop_column("publication", "track_override")
