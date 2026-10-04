"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSON, list[Any]: JSON}


# ---- People & sources -----------------------------------------------------------------------


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    affiliation: Mapped[str | None]
    orcid: Mapped[str | None] = mapped_column(String(19))  # 0000-0002-1825-0097
    notes: Mapped[str | None]
    # Other spellings of the person's name ("B. Piwowarski", maiden name...).
    aliases: Mapped[list[Any]] = mapped_column(default=list)
    # PhD student name (as on theses.fr) -> other spellings used in author lists.
    student_aliases: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # PhD student name (as on theses.fr) -> what became of them after the PhD (a note).
    student_outcomes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Name spellings rejected as matches: {"owner": [...], "<student name>": [...]}.
    name_rejects: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Author category id (as str) -> co-author names in it (e.g. "Intl. collaborators").
    author_categories: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_merged_at: Mapped[datetime | None]

    links: Mapped[list[SourceLink]] = relationship(
        back_populates="person", cascade="all, delete-orphan", order_by="SourceLink.id"
    )
    publications: Mapped[list[Publication]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    periods: Mapped[list[Period]] = relationship(
        back_populates="person", cascade="all, delete-orphan", order_by="Period.id"
    )


class SourceLink(Base):
    """A (candidate or validated) profile of a person on one source."""

    __tablename__ = "source_link"
    __table_args__ = (UniqueConstraint("person_id", "source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str]
    url: Mapped[str | None]
    display_name: Mapped[str | None]
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    score: Mapped[float] = mapped_column(default=0.0)
    # candidate | validated | rejected
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    validated_at: Mapped[datetime | None]
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    # idle | running | ok | error
    sync_state: Mapped[str] = mapped_column(String(16), default="idle")
    sync_started_at: Mapped[datetime | None]
    record_count: Mapped[int | None]

    person: Mapped[Person] = relationship(back_populates="links")
    source_pubs: Mapped[list[SourcePub]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )
    theses: Mapped[list[Thesis]] = relationship(back_populates="link", cascade="all, delete-orphan")

    @property
    def active(self) -> bool:
        """Validated, and its source is used (Settings → Sources)."""
        from ..source_settings import enabled

        return self.status == "validated" and enabled(self.source)

    @property
    def status_label(self) -> str:
        """Human-readable update status for this source."""
        if self.status != "validated":
            return self.status
        if not self.active:
            return "not used"  # its source is disabled in the settings
        if self.sync_state == "running":
            return "updating…"
        if self.sync_state == "error":
            return "error"
        if self.last_synced_at is None:
            return "never synced"
        return "out of date" if self.is_stale else "up to date"

    @property
    def is_stale(self) -> bool:
        if not self.active:
            return False
        if self.last_synced_at is None:
            return True
        return self.validated_at is not None and self.validated_at > self.last_synced_at


# ---- Publications ---------------------------------------------------------------------------


class SourcePub(Base):
    """One publication record as returned by a source."""

    __tablename__ = "source_pub"
    __table_args__ = (UniqueConstraint("link_id", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("source_link.id", ondelete="CASCADE"))
    external_key: Mapped[str]
    title: Mapped[str | None]
    authors: Mapped[list[Any]] = mapped_column(default=list)
    author_pos: Mapped[int | None]
    num_authors: Mapped[int | None]
    year: Mapped[int | None]
    venue: Mapped[str | None]
    issn: Mapped[str | None]
    venue_type: Mapped[str | None]
    doi: Mapped[str | None] = mapped_column(index=True)
    url: Mapped[str | None]
    doc_type: Mapped[str | None]
    pdf_url: Mapped[str | None]
    archival: Mapped[bool] = mapped_column(default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("publication.id", ondelete="SET NULL"), index=True
    )

    link: Mapped[SourceLink] = relationship(back_populates="source_pubs")
    publication: Mapped[Publication | None] = relationship(back_populates="members")


class Publication(Base):
    """A merged publication of a person (a cluster of SourcePub)."""

    __tablename__ = "publication"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    title: Mapped[str | None]
    year: Mapped[int | None]
    doi: Mapped[str | None]
    # The venue of the publication: set automatically, unless venue_manual.
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venue.id", ondelete="SET NULL"), index=True
    )
    venue_manual: Mapped[bool] = mapped_column(default=False)
    # Source whose venue is used when the sources give different venues (validated by hand).
    venue_source: Mapped[str | None] = mapped_column(String(32))
    # Rank set by hand for this publication only: {"type", "rank", "name"} (a level) or
    # {"record_key"} (a ranking record); rank_note says why.
    rank_override: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    rank_note: Mapped[str | None]
    # Venue kind set by hand for this publication (intl_conference, natl_journal, preprint...).
    kind_override: Mapped[str | None] = mapped_column(String(32))
    # Track set by hand for this publication: a track's id (ranking.tracks), or "main" (the
    # main track: no satellite track, whatever the sources say); None: automatic (from the
    # variants, the venue rules and the venue texts). It settles the sources' disagreements.
    track_override: Mapped[str | None] = mapped_column(String(32))
    # DOI given by hand (its record then belongs to this publication).
    doi_manual: Mapped[str | None]
    year_override: Mapped[int | None]
    author_pos_override: Mapped[int | None]
    note: Mapped[str | None]  # Markdown
    # When set, the merge never reassigns members (manual merge / split).
    merge_locked: Mapped[bool] = mapped_column(default=False)
    missing: Mapped[bool] = mapped_column(default=False)
    # Hidden by the user (e.g. misattributed by a source): excluded from lists and stats.
    hidden: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    person: Mapped[Person] = relationship(back_populates="publications")
    # passive_deletes: the DB's ON DELETE SET NULL only touches rows still pointing here, so
    # members re-assigned during a merge are never nulled by the ORM.
    members: Mapped[list[SourcePub]] = relationship(
        back_populates="publication", passive_deletes=True
    )
    venue: Mapped[Venue | None] = relationship()
    # Global tags (per-period ones: PeriodTag).
    tags: Mapped[list[Tag]] = relationship(secondary="publication_tag", lazy="selectin")

    @property
    def has_overrides(self) -> bool:
        """Whether something was set by hand (kept when the paper leaves the sources)."""
        return bool(
            self.venue_manual
            or self.venue_source
            or self.rank_override
            or self.kind_override
            or self.track_override
            or self.year_override
            or self.author_pos_override is not None
            or self.note
            or self.doi_manual
        )


class Period(Base):
    """A named period of interest for a person; with a folder, the person's period in it."""

    __tablename__ = "period"
    __table_args__ = (UniqueConstraint("person_id", "folder_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folder.id", ondelete="CASCADE"))
    name: Mapped[str]
    start_year: Mapped[int | None]
    end_year: Mapped[int | None]
    # Tags of the person within the folder (e.g. "shortlisted"); only used by folder periods.
    tags: Mapped[list[Any]] = mapped_column(default=list)
    # The person's notes within the folder (Markdown), for all their documents and papers.
    notes: Mapped[str | None]

    person: Mapped[Person] = relationship(back_populates="periods")
    folder: Mapped[Folder | None] = relationship(back_populates="periods")
    paper_tags: Mapped[list[PeriodTag]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )
    paper_notes: Mapped[list[PeriodNote]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[list[PeriodDocument]] = relationship(
        back_populates="period",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PeriodDocument.id",
    )

    @property
    def stars(self) -> list[PeriodTag]:
        """The papers starred in the period (tagged with the built-in "starred" tag)."""
        return [t for t in self.paper_tags if t.tag.key == STARRED]


class AuthorCategory(Base):
    """A user-defined group of co-authors (e.g. "Intl. collaborators"), highlighted and
    counted in the panel; its members are set per person."""

    __tablename__ = "author_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    colour: Mapped[str] = mapped_column(String(16), default="#0969da")


class Folder(Base):
    """A named, dated group of people (e.g. a hiring committee), within another one or not.

    A person is in a folder when they have a period in it (a Period with this folder): the
    period of interest of that person for that folder. Its settings (FolderSettings) are
    those it uses (FolderSettingsUse), else its parent's (see folders.settings_id).
    """

    __tablename__ = "folder"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    # The folder it is in (none: at the top level).
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folder.id", ondelete="SET NULL"), index=True
    )
    date: Mapped[date | None]
    hidden: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None]
    # The source a paper must be in to count ("none": no such source; None: the default
    # one, see source_settings).
    primary_source: Mapped[str | None] = mapped_column(String(32))

    periods: Mapped[list[Period]] = relationship(
        back_populates="folder", cascade="all, delete-orphan", passive_deletes=True
    )


class FolderSettings(Base):
    """Settings of folders: its categories (Category) and how the notes cite papers. Used
    by some folders (FolderSettingsUse), and the folders within them using their parent's."""

    __tablename__ = "folder_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # How the notes cite papers: {"tag_id": the tag whose papers are numbered, "format": the
    # number's, "templates": [{"name", "attrs"}] (over the general ones), "skeleton": the
    # starting notes}; see reports.py.
    citations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class FolderSettingsUse(Base):
    """The settings a folder uses (its own); a folder without any uses its parent's."""

    __tablename__ = "folder_settings_use"

    folder_id: Mapped[int] = mapped_column(
        ForeignKey("folder.id", ondelete="CASCADE"), primary_key=True
    )
    settings_id: Mapped[int] = mapped_column(
        ForeignKey("folder_settings.id", ondelete="CASCADE"), index=True
    )


class Category(Base):
    """A category of the folders' grid (e.g. "Research", and under it "Projects"), in order:
    one of their settings; excerpts of their people's documents are filed in it. Its years
    (optional) are those it is about; one can gather the excerpts showing influence
    (``influence``). Its colour is that of its excerpts (their tint on the PDFs)."""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    settings_id: Mapped[int] = mapped_column(
        ForeignKey("folder_settings.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)  # among its siblings
    start_year: Mapped[int | None]
    end_year: Mapped[int | None]
    # The settings' "rayonnement" (at most one): the excerpts flagged "influence" in the
    # other categories are listed in it too.
    influence: Mapped[bool] = mapped_column(default=False, server_default="0")
    colour: Mapped[str | None] = mapped_column(String(16))  # (none: the default tint)


class Excerpt(Base):
    """A passage of a PDF (a document's, or a paper's) of a person in a folder, filed in one
    of the categories of the folder's settings; the years it is about (optional), and whether
    it shows the person's influence ("rayonnement": invited talks, prizes, committees…)."""

    __tablename__ = "excerpt"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), index=True
    )
    period_id: Mapped[int] = mapped_column(ForeignKey("period.id", ondelete="CASCADE"), index=True)
    # Where it is from: a document, or a paper's stored PDF.
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("period_document.id", ondelete="CASCADE")
    )
    publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("publication.id", ondelete="CASCADE")
    )
    page: Mapped[int | None]
    # Its place: one rectangle per line, PDF units ([x0, y0, x1, y1], on its page; or with a
    # fifth number, the page: over pages, or merged excerpts).
    rects: Mapped[list[Any]] = mapped_column(default=list)
    text: Mapped[str]
    start_year: Mapped[int | None]
    end_year: Mapped[int | None]
    influence: Mapped[bool] = mapped_column(default=False, server_default="0")
    position: Mapped[int] = mapped_column(default=0, server_default="0")  # (in its category)
    # Merged with others: the excerpt leading the group (its category, years… are the
    # group's), else none.
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("excerpt.id", ondelete="SET NULL"), index=True
    )
    # Merged as a reference only: its place is cited, not its text.
    ref_only: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Leading a group: the group's text, as edited (none: the quotes of its excerpts).
    group_text: Mapped[str | None] = mapped_column(Text)
    original: Mapped[str | None] = mapped_column(Text)  # (its text as selected, if edited since)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


STARRED = "starred"  # Tag.key of the built-in period tag behind the ★ button


class Tag(Base):
    """A tag of papers, created on the fly: global (on the paper) or set per period (on the
    paper within a period, e.g. "starred" — the ★ button)."""

    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    colour: Mapped[str] = mapped_column(String(16), default="#0969da")
    per_period: Mapped[bool] = mapped_column(default=False)
    # A built-in tag (STARRED): it can be renamed and recoloured, not deleted.
    key: Mapped[str | None] = mapped_column(String(16), unique=True)


class PublicationTag(Base):
    """A global tag on a paper."""

    __tablename__ = "publication_tag"

    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)
    # The paper's number in the list it was tagged from (e.g. "3" in a report's list).
    number: Mapped[int | None]


class PeriodTag(Base):
    """A per-period tag on a paper, within one period."""

    __tablename__ = "period_tag"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("period.id", ondelete="CASCADE"), primary_key=True
    )
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)
    # The paper's number in the list it was tagged from (e.g. "3" in a report's list).
    number: Mapped[int | None]

    tag: Mapped[Tag] = relationship(lazy="selectin")


class PeriodNote(Base):
    """A note (Markdown) on a paper within a period (the paper's own note is
    Publication.note)."""

    __tablename__ = "period_note"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("period.id", ondelete="CASCADE"), primary_key=True
    )
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str]


class PublicationPdf(Base):
    """The PDF of a paper, stored in the data directory (``pdfs/``) to read and annotate."""

    __tablename__ = "publication_pdf"

    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publication.id", ondelete="CASCADE"), primary_key=True
    )
    # Relative to the PDF directory (a file left behind by a deleted paper: see pdfs.cleanup).
    path: Mapped[str]
    # Where it was downloaded from; None: uploaded by hand.
    origin: Mapped[str | None]
    added_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Last saved from the viewer (with annotations): the file is then the user's work.
    edited_at: Mapped[datetime | None]
    # Places to come back to: [{"name", "p": page, "y": top (PDF units)}].
    bookmarks: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")


class PeriodDocument(Base):
    """A PDF of a person within a period / folder (e.g. their application or CV), stored in
    the data directory (``pdfs/documents/``) to read and annotate. It is not one of their
    papers: the papers it mentions are found in it and linked (see documents.py)."""

    __tablename__ = "period_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("period.id", ondelete="CASCADE"), index=True)
    name: Mapped[str]
    # Relative to the PDF directory.
    path: Mapped[str]
    note: Mapped[str | None]  # Markdown
    # Its lines of text ({"p": page, "t": text, "r": [x0, y0, x1, y1]}), as extracted by
    # the viewer (PDF.js): where its papers are looked for. None: not extracted yet.
    lines: Mapped[list[Any] | None] = mapped_column(JSON(none_as_null=True))
    # Papers linked by hand (from a selection): [{"pub": id, "p": page, "r": [rects],
    # "text": the selected text}].
    links: Mapped[list[Any]] = mapped_column(default=list)
    # Places to come back to: [{"name", "p": page, "y": top (PDF units)}].
    bookmarks: Mapped[list[Any]] = mapped_column(default=list)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Last saved from the viewer (with annotations).
    edited_at: Mapped[datetime | None]

    period: Mapped[Period] = relationship(back_populates="documents")


class Report(Base):
    """A report (Markdown) on a person within a period / folder, citing their papers
    (``[@key]``, see reports.py): those with some tags. Its view is gone, merged into the
    folder's notes (where it was appended, see old_reports.py): kept for its data."""

    __tablename__ = "report"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("period.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(default="")
    tag_ids: Mapped[list[Any]] = mapped_column(default=list)  # the papers to discuss
    # How a paper's number is written ({n}: the number).
    number_format: Mapped[str] = mapped_column(default="**#{n}**")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Thesis(Base):
    __tablename__ = "thesis"
    __table_args__ = (UniqueConstraint("link_id", "thesis_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("source_link.id", ondelete="CASCADE"))
    thesis_id: Mapped[str]
    # director | rapporteur | examiner | president | author | other
    role: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None]
    student: Mapped[str | None]
    supervisors: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str | None]
    defence_date: Mapped[str | None]
    start_date: Mapped[str | None]
    discipline: Mapped[str | None]
    institution: Mapped[str | None]
    url: Mapped[str | None]

    link: Mapped[SourceLink] = relationship(back_populates="theses")


# ---- Matching settings (shareable) ----------------------------------------------------------


class DoiRecord(Base):
    """Metadata of a DOI (Crossref, else doi.org content negotiation), cached forever."""

    __tablename__ = "doi_record"

    doi: Mapped[str] = mapped_column(primary_key=True)  # normalized (lower case)
    status: Mapped[str] = mapped_column(String(16))  # ok | not_found
    origin: Mapped[str | None] = mapped_column(String(16))  # crossref | csl (doi.org)
    # Normalized fields (title, year, venue, venue_type, doc_type, authors, issn, url,
    # archival, event...) and the registry's answer.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class AppSetting(Base):
    """Key/value settings. ``matching`` holds the matching settings (``MatchSettings``)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Venue(Base):
    """A journal or conference. Variants (processed venue texts) map to it via VenueKey.

    Every user decision is flagged manual so that automatic processing never overrides it.
    """

    __tablename__ = "venue"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    # Short name (e.g. "ICLR"): set by hand when short_manual (none: the venue has no
    # acronym), else inferred from the ranking record / texts (and cached here).
    short_name: Mapped[str | None]
    short_manual: Mapped[bool] = mapped_column(default=False)
    # The venue's website (e.g. the conference series' or journal's home page).
    url: Mapped[str | None]
    # Kind (first level): detected automatically unless kind_manual.
    kind: Mapped[str | None] = mapped_column(String(32))
    kind_manual: Mapped[bool] = mapped_column(default=False)
    # Manual level (second level), e.g. conference/A or journal/Q1.
    level_type: Mapped[str | None] = mapped_column(String(16))
    level_rank: Mapped[str | None]
    # Manually picked ranking record (Matcher.record_key).
    record_key: Mapped[str | None]
    # "Search rankings as": the text the automatic ranking lookup searches for.
    match_text: Mapped[str | None]
    # Regex rules: source venue texts matching one belong to this venue (VenuePattern dicts).
    patterns: Mapped[list[Any] | None] = mapped_column(JSON(none_as_null=True))
    # Identifiers of the venue ({"issn": [...]}): records carrying one belong to it.
    identifiers: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    # A workshop's main conferences: [{"venue_id": 12, "from": 2015, "to": 2019}, ...] (years
    # included, either one open). Its papers take the rank of the one of their year.
    hosts: Mapped[list[Any] | None] = mapped_column(JSON(none_as_null=True))
    # A joint venue (e.g. CORIA-TALN): {"parts": [venue ids], "manual": bool, "use": venue id
    # or None}. Parts are found from the acronyms of its texts unless set by hand ("manual",
    # an empty list then says it is not joint); "use": the part whose level it takes.
    joint: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    keys: Mapped[list[VenueKey]] = relationship(
        back_populates="venue", cascade="all, delete-orphan", order_by="VenueKey.key"
    )

    @property
    def level(self) -> dict[str, Any] | None:
        if not self.level_rank:
            return None
        return {"type": self.level_type or "conference", "rank": self.level_rank, "name": self.name}

    def host_at(self, year: int | None) -> int | None:
        """The main conference of a workshop for a paper of ``year``."""
        for h in self.hosts or []:
            lo, hi = h.get("from"), h.get("to")
            if year is None or ((lo is None or lo <= year) and (hi is None or year <= hi)):
                return h.get("venue_id")
        return None

    @property
    def has_manual(self) -> bool:
        return bool(
            self.kind_manual
            or self.level_rank
            or self.record_key
            or self.match_text
            or self.short_manual
            or self.url
            or self.patterns
            or self.identifiers
            or self.hosts
            or bool(self.joint and (self.joint.get("manual") or self.joint.get("use")))
        )

    @property
    def parts(self) -> list[int]:
        """A joint venue's venues (none for another)."""
        return list((self.joint or {}).get("parts") or [])


class VenueKey(Base):
    """A variant of a venue: a raw venue text, matched by its key (cleaned + normalized).

    The key is recomputed from the raw text when the normalization rules change.
    """

    __tablename__ = "venue_key"

    key: Mapped[str] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venue.id", ondelete="CASCADE"), index=True)
    # Assigned to this venue by the user (merge): never re-assigned automatically.
    manual: Mapped[bool] = mapped_column(default=False)
    # The raw venue text of the variant, and its source (the source's rules apply to it).
    example: Mapped[str | None]
    source: Mapped[str | None] = mapped_column(String(32))
    # Track (demo, findings, short...) of the publications with this variant.
    track: Mapped[str | None]

    venue: Mapped[Venue] = relationship(back_populates="keys")


class VenueText(Base):
    """A distinct venue text of a source and the venue it belongs to (computed from the
    normalization rules, variants and venue rules; rebuilt when they change)."""

    __tablename__ = "venue_text"
    __table_args__ = (UniqueConstraint("source", "raw"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    raw: Mapped[str]
    clean: Mapped[str]
    key: Mapped[str] = mapped_column(index=True)
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venue.id", ondelete="SET NULL"), index=True
    )
    # How it matched: variant (a manual variant) | pattern | auto (its key) | archival
    via: Mapped[str] = mapped_column(String(16), default="auto")
    # The venue rule that matched (index in the venue's patterns).
    pattern_index: Mapped[int | None]
    track: Mapped[str | None]
    # Other venues whose rules match the text too.
    conflicts: Mapped[list[Any]] = mapped_column(default=list)

    venue: Mapped[Venue | None] = relationship()


class JcrRecord(Base):
    __tablename__ = "jcr_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict[str, Any]]


class VenueCache(Base):
    __tablename__ = "venue_cache"
    __table_args__ = (Index("ix_venue_cache_source", "source"),)

    key: Mapped[str] = mapped_column(primary_key=True)
    badge: Mapped[dict[str, Any] | None]
    source: Mapped[str | None]
    ts: Mapped[datetime] = mapped_column(default=utcnow)
