"""Initial schema (the baseline: the earlier revisions are squashed into it)

The schema of the former chain of revisions as of its last one (f6b3d8a2c5e9, kept as the
id: a database already at it needs nothing), and the built-in tag for starred papers.

Revision ID: f6b3d8a2c5e9
Revises:
Create Date: 2026-10-04 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b3d8a2c5e9"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(*["key"]),
    )
    op.create_table(
        "author_category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("colour", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["name"]),
    )
    op.create_table(
        "doi_record",
        sa.Column("doi", sa.String(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint(*["doi"]),
    )
    op.create_table(
        "folder",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("primary_source", sa.String(32), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["folder.id"], ondelete="SET NULL", name="fk_folder_parent_id"
        ),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "folder_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "jcr_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("affiliation", sa.String(), nullable=True),
        sa.Column("orcid", sa.String(19), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("student_aliases", sa.JSON(), nullable=False),
        sa.Column("student_outcomes", sa.JSON(), nullable=False),
        sa.Column("name_rejects", sa.JSON(), nullable=False),
        sa.Column("author_categories", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_merged_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "tag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("colour", sa.String(16), nullable=False),
        sa.Column("per_period", sa.Boolean(), nullable=False),
        sa.Column("key", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["key"]),
        sa.UniqueConstraint(*["name"]),
    )
    op.create_table(
        "venue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("kind_manual", sa.Boolean(), nullable=False),
        sa.Column("level_type", sa.String(16), nullable=True),
        sa.Column("level_rank", sa.String(), nullable=True),
        sa.Column("record_key", sa.String(), nullable=True),
        sa.Column("match_text", sa.String(), nullable=True),
        sa.Column("patterns", sa.JSON(), nullable=True),
        sa.Column("identifiers", sa.JSON(), nullable=True),
        sa.Column("hosts", sa.JSON(), nullable=True),
        sa.Column("joint", sa.JSON(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("short_manual", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "venue_cache",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("badge", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint(*["key"]),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("influence", sa.Boolean(), server_default=sa.text("'0'"), nullable=False),
        sa.Column("colour", sa.String(16), nullable=True),
        sa.Column("settings_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["settings_id"],
            ["folder_settings.id"],
            ondelete="CASCADE",
            name="fk_category_settings_id",
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["category.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "folder_settings_use",
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("settings_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["settings_id"], ["folder_settings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["folder_id"]),
    )
    op.create_table(
        "period",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["person_id", "folder_id"]),
    )
    op.create_table(
        "publication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.Column("venue_manual", sa.Boolean(), nullable=False),
        sa.Column("venue_source", sa.String(32), nullable=True),
        sa.Column("rank_override", sa.JSON(), nullable=True),
        sa.Column("rank_note", sa.String(), nullable=True),
        sa.Column("kind_override", sa.String(32), nullable=True),
        sa.Column("doi_manual", sa.String(), nullable=True),
        sa.Column("year_override", sa.Integer(), nullable=True),
        sa.Column("author_pos_override", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("merge_locked", sa.Boolean(), nullable=False),
        sa.Column("missing", sa.Boolean(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("track_override", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["venue_id"], ["venue.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "source_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("score", sa.Double(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("sync_state", sa.String(16), nullable=False),
        sa.Column("sync_started_at", sa.DateTime(), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["person_id", "source", "external_id"]),
    )
    op.create_table(
        "venue_key",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("example", sa.String(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("track", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["venue_id"], ["venue.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["key"]),
    )
    op.create_table(
        "venue_text",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("raw", sa.String(), nullable=False),
        sa.Column("clean", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.Column("via", sa.String(16), nullable=False),
        sa.Column("pattern_index", sa.Integer(), nullable=True),
        sa.Column("track", sa.String(), nullable=True),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["venue_id"], ["venue.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["source", "raw"]),
    )
    op.create_table(
        "period_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("bookmarks", sa.JSON(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_table(
        "period_note",
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["period_id", "publication_id"]),
    )
    op.create_table(
        "period_tag",
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["period_id", "publication_id", "tag_id"]),
    )
    op.create_table(
        "publication_pdf",
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("origin", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("bookmarks", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["publication_id"]),
    )
    op.create_table(
        "publication_tag",
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["publication_id", "tag_id"]),
    )
    op.create_table(
        "report",
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("tag_ids", sa.JSON(), nullable=False),
        sa.Column("number_format", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["period_id"]),
    )
    op.create_table(
        "source_pub",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("external_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("author_pos", sa.Integer(), nullable=True),
        sa.Column("num_authors", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(), nullable=True),
        sa.Column("issn", sa.String(), nullable=True),
        sa.Column("venue_type", sa.String(), nullable=True),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("doc_type", sa.String(), nullable=True),
        sa.Column("pdf_url", sa.String(), nullable=True),
        sa.Column("archival", sa.Boolean(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["link_id"], ["source_link.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["link_id", "external_key"]),
    )
    op.create_table(
        "thesis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("thesis_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("student", sa.String(), nullable=True),
        sa.Column("supervisors", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("defence_date", sa.String(), nullable=True),
        sa.Column("start_date", sa.String(), nullable=True),
        sa.Column("discipline", sa.String(), nullable=True),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["link_id"], ["source_link.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
        sa.UniqueConstraint(*["link_id", "thesis_id", "role"]),
    )
    op.create_table(
        "excerpt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("publication_id", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("rects", sa.JSON(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("influence", sa.Boolean(), server_default=sa.text("'0'"), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), server_default=sa.text("'0'"), nullable=False),
        sa.Column("ref_only", sa.Boolean(), server_default=sa.text("'0'"), nullable=False),
        sa.Column("group_text", sa.Text(), nullable=True),
        sa.Column("original", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"], ["excerpt.id"], ondelete="SET NULL", name="fk_excerpt_group_id"
        ),
        sa.ForeignKeyConstraint(["category_id"], ["category.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["period_id"], ["period.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["period_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publication.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(*["id"]),
    )
    op.create_index("ix_folder_parent_id", "folder", ["parent_id"], unique=False)
    op.create_index("ix_venue_cache_source", "venue_cache", ["source"], unique=False)
    op.create_index("ix_category_parent_id", "category", ["parent_id"], unique=False)
    op.create_index("ix_category_settings_id", "category", ["settings_id"], unique=False)
    op.create_index(
        "ix_folder_settings_use_settings_id", "folder_settings_use", ["settings_id"], unique=False
    )
    op.create_index("ix_publication_person_id", "publication", ["person_id"], unique=False)
    op.create_index("ix_publication_venue_id", "publication", ["venue_id"], unique=False)
    op.create_index("ix_venue_key_venue_id", "venue_key", ["venue_id"], unique=False)
    op.create_index("ix_venue_text_key", "venue_text", ["key"], unique=False)
    op.create_index("ix_venue_text_venue_id", "venue_text", ["venue_id"], unique=False)
    op.create_index("ix_period_document_period_id", "period_document", ["period_id"], unique=False)
    op.create_index("ix_source_pub_doi", "source_pub", ["doi"], unique=False)
    op.create_index("ix_source_pub_publication_id", "source_pub", ["publication_id"], unique=False)
    op.create_index("ix_excerpt_category_id", "excerpt", ["category_id"], unique=False)
    op.create_index("ix_excerpt_group_id", "excerpt", ["group_id"], unique=False)
    op.create_index("ix_excerpt_period_id", "excerpt", ["period_id"], unique=False)
    # The built-in tag for starred papers
    op.execute(
        "INSERT INTO tag (name, colour, per_period, key) VALUES ('starred', '#ff8f00', 1, 'starred')"
    )


def downgrade() -> None:
    op.drop_table("excerpt")
    op.drop_table("thesis")
    op.drop_table("source_pub")
    op.drop_table("report")
    op.drop_table("publication_tag")
    op.drop_table("publication_pdf")
    op.drop_table("period_tag")
    op.drop_table("period_note")
    op.drop_table("period_document")
    op.drop_table("venue_text")
    op.drop_table("venue_key")
    op.drop_table("source_link")
    op.drop_table("publication")
    op.drop_table("period")
    op.drop_table("folder_settings_use")
    op.drop_table("category")
    op.drop_table("venue_cache")
    op.drop_table("venue")
    op.drop_table("tag")
    op.drop_table("person")
    op.drop_table("jcr_record")
    op.drop_table("folder_settings")
    op.drop_table("folder")
    op.drop_table("doi_record")
    op.drop_table("author_category")
    op.drop_table("app_setting")
