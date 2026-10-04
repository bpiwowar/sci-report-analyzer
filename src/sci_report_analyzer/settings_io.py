"""Export / import of matching settings (shareable JSON).

A settings file holds the matching settings (sources, thresholds, normalization rules,
venue-kind settings, detection rules, tracks), the venues with manual decisions (kind,
level, ranking record, search text, variants, venue rules, identifiers) and optionally
imported JCR rows. People, publications and their annotations are never part of an import.

The format (fields, versioning, import modes): ``docs/json-formats.md``. Bump ``VERSION`` and
update it when a field changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import object_session, selectinload

from . import annotations, venue_match
from .db.models import JcrRecord, Venue, VenueKey
from .db.session import session_scope
from .i18n import _
from .ranking import tracks
from .ranking.service import (
    MatchSettings,
    VenuePattern,
    load_settings,
    save_settings,
    service,
)

FORMAT = "sci-report-analyzer-settings"
VERSION = 6
MIN_VERSION = 6  # (older files had flags, tracks without name rules)


class VariantIO(BaseModel):
    raw: str  # a raw venue text (its key is computed with the local rules)
    source: str | None = None
    manual: bool = True
    track: str | None = None

    @property
    def key(self) -> str:
        return service.key(self.raw, self.source)


class HostIO(BaseModel):
    """A workshop's main conference (by venue name), with the years it applies to."""

    venue: str
    start: int | None = None
    end: int | None = None


class JointIO(BaseModel):
    """A joint venue's parts set by hand and / or the part whose level it takes (by name)."""

    parts: list[str] | None = None  # None: found automatically
    use: str | None = None


class VenueIO(BaseModel):
    name: str
    variants: list[VariantIO] = Field(default_factory=list)
    short_name: str | None = None  # set by hand only ("": the venue has no acronym)
    url: str | None = None
    kind: str | None = None  # manual kind only
    level_type: str | None = None
    level_rank: str | None = None
    record_key: str | None = None
    match_text: str | None = None
    patterns: list[VenuePattern] | None = None
    identifiers: dict[str, Any] | None = None
    hosts: list[HostIO] | None = None
    joint: JointIO | None = None

    @property
    def keys(self) -> list[str]:
        return [k for k in (v.key for v in self.variants) if k] or [service.key(self.name)]


class SettingsFile(BaseModel):
    format: Literal["sci-report-analyzer-settings"] = FORMAT
    version: int = VERSION
    exported_at: str | None = None
    matching: MatchSettings = Field(default_factory=MatchSettings)
    venues: list[VenueIO] = Field(default_factory=list)
    jcr: list[dict[str, Any]] | None = None


def export_settings(*, include_jcr: bool = False) -> SettingsFile:
    with session_scope() as s:
        venues = []
        for v in s.scalars(select(Venue).options(selectinload(Venue.keys))):
            if not v.has_manual and not any(k.manual for k in v.keys):
                continue
            venues.append(
                VenueIO(
                    name=v.name,
                    variants=[
                        VariantIO(
                            raw=k.example or k.key, source=k.source, manual=k.manual, track=k.track
                        )
                        for k in v.keys
                    ],
                    short_name=_short(v),
                    url=v.url,
                    kind=v.kind if v.kind_manual else None,
                    level_type=v.level_type,
                    level_rank=v.level_rank,
                    record_key=v.record_key,
                    match_text=v.match_text,
                    patterns=[VenuePattern.model_validate(p) for p in v.patterns]
                    if v.patterns
                    else None,
                    identifiers=v.identifiers,
                    hosts=_host_ios(s, v),
                    joint=_joint_io(s, v),
                )
            )
        return SettingsFile(
            exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
            matching=load_settings(),
            venues=venues,
            jcr=[r.data for r in s.scalars(select(JcrRecord))] if include_jcr else None,
        )


def parse_file(text: str) -> SettingsFile:
    data = SettingsFile.model_validate_json(text)
    if data.version < MIN_VERSION:
        raise ValueError(
            _(
                "it was exported by an older version of the app (format {version}): only "
                "files of format {min} or later can be imported"
            ).format(version=data.version, min=MIN_VERSION)
        )
    return data


@dataclass
class Conflict:
    kind: str  # matching | venue | variant
    key: str
    local: Any
    imported: Any

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"


# ---- matching settings ---------------------------------------------------------------------


def _rule_text(r) -> str:
    flags = "" if r.enabled else " " + _("(disabled)")
    scope = " " + _("[only {sources}]").format(sources=", ".join(r.sources)) if r.sources else ""
    case = " " + _("[ignore case]") if r.ignore_case else ""
    return f"{r.pattern} → “{r.replacement}”{case}{scope}{flags}"


def _detection_text(r) -> str:
    return r.pattern + (" " + _("[ignore case]") if r.ignore_case else "")


def _track_text(t: tracks.Track) -> str:
    """A track's names, colour, rules and name rules."""
    names = " / ".join(n for n in t.names.values() if n)
    rules = " | ".join(_detection_text(r) for r in t.rules if r.pattern)
    name_rules = " | ".join(
        f"{r.pattern} → “{r.replacement}”" + (" " + _("[ignore case]") if r.ignore_case else "")
        for r in t.name_rules
        if r.pattern
    )
    return f"{names} {t.colour}: {rules}" + (f" · {name_rules}" if name_rules else "")


_SCALARS = (
    "min_score",
    "national_keywords",
    "international_keywords",
    "unknown_scope",
)


def _matching_fields(m: MatchSettings) -> dict[str, Any]:
    d = m.model_dump()
    out: dict[str, Any] = {k: sorted(d[k]) if isinstance(d[k], list) else d[k] for k in _SCALARS}
    out.update({f"sources.{k}": v for k, v in m.sources.items()})
    out.update({f"kind_levels.{k}": v for k, v in m.kind_levels.items()})
    # Normalization rules are compared one by one, keyed by their id.
    out.update({f"rules.{r.id}": _rule_text(r) for r in m.norm_rules})
    out.update({f"detection.{r.id}": _detection_text(r) for r in m.detection_rules})
    out.update({f"tracks.{t.id}": _track_text(t) for t in m.tracks})
    return out


def _apply_matching(local: MatchSettings, data: MatchSettings, take: set[str]) -> MatchSettings:
    d = local.model_dump()
    imp = data.model_dump()
    for k in _SCALARS:
        if k in take:
            d[k] = imp[k]
    for group in ("sources", "kind_levels"):
        for k, v in imp[group].items():
            if k not in d[group] or f"{group}.{k}" in take:
                d[group][k] = v
    rules = {r["id"]: i for i, r in enumerate(d["norm_rules"])}
    for r in imp["norm_rules"]:
        if r["id"] not in rules:
            d["norm_rules"].append(r)
        elif f"rules.{r['id']}" in take:
            d["norm_rules"][rules[r["id"]]] = r
    detection = {r["id"]: i for i, r in enumerate(d["detection_rules"])}
    for r in imp["detection_rules"]:
        if r["id"] in detection and f"detection.{r['id']}" in take:
            d["detection_rules"][detection[r["id"]]] = r
    known = {t["id"]: i for i, t in enumerate(d["tracks"])}
    for t in imp["tracks"]:
        if t["id"] not in known:
            d["tracks"].append(t)
        elif f"tracks.{t['id']}" in take:
            d["tracks"][known[t["id"]]] = t
    return MatchSettings.model_validate(d)


def _replacing(local: MatchSettings, data: MatchSettings) -> MatchSettings:
    """The imported settings, with the local tracks added by hand that the file lacks
    (papers, variants or venue rules may be of them)."""
    ids = {t.id for t in data.tracks}
    kept = [t for t in local.tracks if t.id not in ids and t.id not in tracks.DEFAULTS]
    return data.model_copy(update={"tracks": [*data.tracks, *kept]})


@dataclass
class TrackMapping:
    """How the file's tracks map to the local ones (by their ids)."""

    added: list[tracks.Track]  # in the file only: added
    both: list[tuple[tracks.Track, tracks.Track]]  # (local, imported): the same id
    local: list[tracks.Track]  # added by hand here, not in the file: kept, or removed

    def differ(self, local: tracks.Track, imported: tracks.Track) -> bool:
        return _track_text(local) != _track_text(imported)


def track_mapping(data: SettingsFile) -> TrackMapping:
    """The file's tracks against the local ones (shown before an import)."""
    local = {t.id: t for t in load_settings().tracks}
    ids = {t.id for t in data.matching.tracks}
    return TrackMapping(
        added=[t for t in data.matching.tracks if t.id not in local],
        both=[(local[t.id], t) for t in data.matching.tracks if t.id in local],
        local=[t for t in local.values() if t.id not in ids and t.id not in tracks.DEFAULTS],
    )


# ---- venues --------------------------------------------------------------------------------

# A venue's manual decisions as exported (``Venue.MANUAL_FIELDS``, the level as one field).
_VENUE_FIELDS = (
    "kind",
    "level",
    "record_key",
    "match_text",
    "short_name",
    "url",
    "patterns",
    "identifiers",
    "hosts",
    "joint",
)


def _host_ios(s, v: Venue) -> list[HostIO] | None:
    out = []
    for h in v.hosts or []:
        host = s.get(Venue, h.get("venue_id"))
        if host is not None:
            out.append(HostIO(venue=host.name, start=h.get("from"), end=h.get("to")))
    return out or None


def _joint_io(s, v: Venue) -> JointIO | None:
    j = v.joint or {}
    if not (j.get("manual") or j.get("use")):
        return None

    def name(vid: int | None) -> str | None:
        return (venue := s.get(Venue, vid)) and venue.name if vid else None

    parts = [n for n in map(name, j.get("parts") or []) if n] if j.get("manual") else None
    return JointIO(parts=parts, use=name(j.get("use")))


def _joint_value(v: Venue | VenueIO) -> dict | None:
    joint = v.joint if isinstance(v, VenueIO) else _joint_io(object_session(v), v)
    return joint.model_dump() if joint else None


def _hosts_value(v: Venue | VenueIO) -> list[dict] | None:
    hosts = v.hosts if isinstance(v, VenueIO) else _host_ios(object_session(v), v)
    return [h.model_dump() for h in hosts] if hosts else None


def _find_venue(s, name: str) -> Venue | None:
    v = s.scalar(select(Venue).where(Venue.name == name).limit(1))
    if v is None and (key := service.key(name)) and (vk := s.get(VenueKey, key)):
        v = vk.venue
    return v


def _short(v: Venue) -> str | None:
    """A venue's acronym set by hand ("": none), not an inferred one."""
    return (v.short_name or "") if v.short_manual else None


def _venue_values(v: Venue | VenueIO) -> dict[str, Any]:
    kind = v.kind if isinstance(v, VenueIO) or v.kind_manual else None
    level = f"{v.level_type} {v.level_rank}" if v.level_rank else None
    return {
        "kind": kind,
        "level": level,
        "record_key": v.record_key,
        "match_text": v.match_text,
        "short_name": v.short_name if isinstance(v, VenueIO) else _short(v),
        "url": v.url,
        "patterns": _patterns(v.patterns),
        "identifiers": v.identifiers or None,
        "hosts": _hosts_value(v),
        "joint": _joint_value(v),
    }


def _patterns(patterns: list | None) -> list[dict] | None:
    out = [
        (p if isinstance(p, VenuePattern) else VenuePattern.model_validate(p)).model_dump(
            exclude_defaults=True
        )
        for p in patterns or []
    ]
    return out or None


def _local_venue(s, vio: VenueIO) -> Venue | None:
    for key in vio.keys:
        if vk := s.get(VenueKey, key):
            return vk.venue
    return None


def _venue_conflicts(s, vio: VenueIO) -> list[Conflict]:
    local = _local_venue(s, vio)
    if local is None:
        return []
    out = []
    lv, iv = _venue_values(local), _venue_values(vio)
    for f in _VENUE_FIELDS:
        if lv[f] is not None and iv[f] is not None and lv[f] != iv[f]:
            out.append(Conflict("venue", f"{vio.keys[0]}.{f}", lv[f], iv[f]))
    for key in vio.keys:
        vk = s.get(VenueKey, key)
        if vk is not None and vk.venue_id != local.id:
            out.append(
                Conflict(
                    "variant",
                    key,
                    _("in “{venue}”").format(venue=vk.venue.name),
                    _("in “{venue}”").format(venue=vio.name),
                )
            )
    return out


def find_conflicts(data: SettingsFile) -> list[Conflict]:
    """Entries present on both sides with different values (for a merge import)."""
    out: list[Conflict] = []
    local_m, imp_m = _matching_fields(load_settings()), _matching_fields(data.matching)
    for k, v in imp_m.items():
        if k in local_m and local_m[k] != v:
            out.append(Conflict("matching", k, local_m[k], v))
    with session_scope() as s:
        for vio in data.venues:
            out.extend(_venue_conflicts(s, vio))
    return out


def _import_venue(s, vio: VenueIO, *, force: bool, take: set[str]) -> tuple[Venue, set[str]]:
    """Import a venue; returns it with the fields naming other venues (main conferences,
    joint venue) to set once every venue is imported."""
    venue = _local_venue(s, vio)
    if venue is None:
        venue = Venue(name=vio.name)
        s.add(venue)
        s.flush()
    for var in vio.variants:
        if not (key := var.key):
            continue
        vk = s.get(VenueKey, key)
        if vk is None:
            s.add(
                VenueKey(
                    key=key,
                    venue_id=venue.id,
                    manual=var.manual,
                    example=var.raw,
                    source=var.source,
                    track=var.track,
                )
            )
        elif vk.venue_id != venue.id and (force or f"variant:{key}" in take):
            vk.venue_id, vk.manual, vk.track = venue.id, True, var.track
    local, imported = _venue_values(venue), _venue_values(vio)

    def wanted(f: str) -> bool:
        if imported[f] is None:
            return False
        return force or local[f] is None or f"venue:{vio.keys[0]}.{f}" in take

    if wanted("kind"):
        venue.kind, venue.kind_manual = vio.kind, True
    if wanted("level"):
        venue.level_type, venue.level_rank = vio.level_type, vio.level_rank
    if wanted("record_key"):
        venue.record_key = vio.record_key
    if wanted("match_text"):
        venue.match_text = vio.match_text
    if wanted("short_name"):
        venue.short_name, venue.short_manual = vio.short_name or None, True
    if wanted("url"):
        venue.url = vio.url
    if wanted("patterns"):
        venue.patterns = imported["patterns"]
    if wanted("identifiers"):
        venue.identifiers = vio.identifiers
    s.flush()
    return venue, {f for f in ("hosts", "joint") if wanted(f)}


def _import_joint(s, venue: Venue, joint: JointIO) -> None:
    def vid(name: str | None) -> int | None:
        v = _find_venue(s, name) if name else None
        return v.id if v is not None and v.id != venue.id else None

    current = venue.joint or {}
    if joint.parts is not None:
        parts = list(dict.fromkeys(i for i in map(vid, joint.parts) if i))
        current = {"parts": parts, "manual": True}
    venue.joint = {**current, "use": vid(joint.use)}


def import_settings(
    data: SettingsFile,
    mode: Literal["replace", "merge"],
    take_imported: set[str] | None = None,
    remove_tracks: set[str] | None = None,
) -> dict[str, int]:
    """Apply an import.

    ``replace`` first erases every matching setting and venue decision (kind, level,
    record, search text, venue rules, identifiers; variants and paper links are kept).
    ``merge`` keeps local values and adds the imported ones; for conflicting entries, those whose
    ``Conflict.id`` is in ``take_imported`` are overwritten, the others stay local.
    The local tracks added by hand that the file lacks are kept, but those in
    ``remove_tracks`` (``TrackMapping.local``): no paper, variant or rule is of them then.
    """
    take = take_imported or set()
    counts = {"venues": 0, "jcr": 0}
    replace = mode == "replace"
    with session_scope() as s:
        if replace:
            for v in s.scalars(select(Venue)):
                v.kind_manual = False
                v.level_type = v.level_rank = v.record_key = v.match_text = None
                v.short_name = v.url = v.patterns = v.identifiers = v.hosts = None
                v.short_manual = False
                if v.joint and (v.joint.get("manual") or v.joint.get("use")):
                    v.joint = None
            if data.jcr is not None:
                s.execute(delete(JcrRecord))
        deferred = []
        for vio in data.venues:
            venue, fields = _import_venue(s, vio, force=replace, take=take)
            if fields:
                deferred.append((venue, vio, fields))
            counts["venues"] += 1
        for venue, vio, fields in deferred:
            if "hosts" in fields:
                hosts = []
                for h in vio.hosts or []:
                    if (host := _find_venue(s, h.venue)) is not None and host.id != venue.id:
                        hosts.append({"venue_id": host.id, "from": h.start, "to": h.end})
                venue.hosts = hosts or None
            if "joint" in fields:
                _import_joint(s, venue, vio.joint)
        if data.jcr:
            known = set() if replace else {r.data.get("name") for r in s.scalars(select(JcrRecord))}
            for row in data.jcr:
                if row.get("name") not in known:
                    s.add(JcrRecord(data=row))
                    counts["jcr"] += 1

    if replace:
        new = _replacing(load_settings(), data.matching)
    else:
        new = _apply_matching(
            load_settings(), data.matching, {t.removeprefix("matching:") for t in take}
        )
    gone = {t.id for t in track_mapping(data).local} & (remove_tracks or set())
    new.tracks = [t for t in new.tracks if t.id not in gone]
    save_settings(new)
    annotations.forget_tracks(gone)
    service.invalidate(
        data=bool(counts["jcr"]) or (replace and data.jcr is not None), clear_cache=True
    )
    venue_match.refresh(reassign=True)
    return counts
