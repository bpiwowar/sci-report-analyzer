"""Shared settings: a group's matching settings and venue decisions as a folder of JSON
files, made to be kept in a git repository (``shared_git``) and reviewed as pull requests.

The folder (format: ``docs/json-formats.md``)::

    settings.json          the format, and the matching settings that differ from the defaults
    norm_rules.json        cleaning rules added, changed or removed (and their order)
    detection_rules.json   detection rules changed
    tracks.json            tracks added or changed (and their order)
    venues/<id>.json       a venue's manual decisions, one file per venue

Only manual decisions are shared: the automatic ones are found again from each member's
datasets and papers. Each file is written canonically (sorted keys, 2-space indent): the
same settings always give the same bytes, and a diff shows only what changed.

Settings are compared as *items*, a flat ``{key: value}`` view of the folder (a setting, a
rule, a venue's field, a variant): a pull merges the group's items with the local ones
against those last taken from the group (``merge3``), a push writes the local ones chosen.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import annotations, authorship, venue_match
from .db.models import Venue, VenueKey
from .db.session import session_scope
from .i18n import N_, _
from .ranking import tracks as tracks_mod
from .ranking.detection import DetectionRule, default_detection_rules
from .ranking.levels import clean, clean_name, normalized
from .ranking.normalize import NormRule, default_rules
from .ranking.service import MatchSettings, VenuePattern, load_settings, save_settings, service
from .text import ascii_fold

FORMAT = "sci-report-analyzer-shared"
VERSION = 1

SETTINGS_FILE = "settings.json"
NORM_FILE = "norm_rules.json"
DETECTION_FILE = "detection_rules.json"
TRACKS_FILE = "tracks.json"
VENUES_DIR = "venues"
MANAGED = (SETTINGS_FILE, NORM_FILE, DETECTION_FILE, TRACKS_FILE, VENUES_DIR)

Files = dict[str, str]  # path in the folder -> text
Items = dict[str, Any]  # item key -> value

# The matching settings kept as their own items (the others: ``settings/<field>``).
_STRUCTURED = ("sources", "kind_levels", "norm_rules", "detection_rules", "tracks")
# A venue's decisions (``Venue.DECIDED``), as fields of its file.
VENUE_FIELDS = Venue.DECIDED
# A venue's decisions by period, each entry with its author: levels, former names.
LISTS = ("levels", "names")
SHARE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def dumps(data: Any) -> str:
    """The canonical text of a file: sorted keys, 2-space indent, a final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def comparable(value: Any) -> Any:
    """An item's value without who set it nor when (two members setting the same value
    agree)."""
    return authorship.without_meta(value)


# ---- matching settings <-> items ------------------------------------------------------------


def _dump(rule: Any) -> dict[str, Any]:
    return rule.model_dump(mode="json", exclude_none=True)


def _rule_items(
    items: Items, prefix: str, rules: list[Any], defaults: list[Any], *, ordered: bool = True
) -> None:
    """The rules that differ from their default (a removed default: ``{"removed": true}``),
    and their order when it is not the natural one."""
    by_id = {d.id: _dump(d) for d in defaults}
    for r in rules:
        value = _dump(r)
        if r.id not in by_id or comparable(value) != comparable(by_id[r.id]):
            items[f"{prefix}/{r.id}"] = value
    ids = {r.id for r in rules}
    for rid in by_id:
        if rid not in ids:
            items[f"{prefix}/{rid}"] = {"id": rid, "removed": True}
    if ordered:
        natural = [r["id"] for r in _rules_from_items(items, prefix, defaults, order=False)]
        if [r.id for r in rules] != natural:
            items[f"{prefix}_order"] = [r.id for r in rules]


def _rules_from_items(
    items: Items, prefix: str, defaults: list[Any], *, order: bool = True
) -> list[dict[str, Any]]:
    """The rules of the items: the defaults in their order (changed or removed by the
    items), then those added (by id); in the items' order if any."""
    out: dict[str, dict[str, Any]] = {d.id: _dump(d) for d in defaults}
    added = {}
    for key, value in items.items():
        if key.startswith(prefix + "/"):
            rid = key[len(prefix) + 1 :]
            if value.get("removed"):
                out.pop(rid, None)
            elif rid in out:
                out[rid] = value
            else:
                added[rid] = value
    rules = [*out.values(), *(added[k] for k in sorted(added))]
    if order and (wanted := items.get(f"{prefix}_order")):
        rank = {rid: i for i, rid in enumerate(wanted)}
        rules.sort(key=lambda r: rank.get(r["id"], len(rank)))
    return rules


def matching_items(m: MatchSettings) -> Items:
    """The matching settings' items: those that differ from the defaults."""
    defaults = MatchSettings()
    dump, ddump = m.model_dump(mode="json"), defaults.model_dump(mode="json")
    items: Items = {}
    for f in MatchSettings.model_fields:
        if f not in _STRUCTURED and dump[f] != ddump[f]:
            items[f"settings/{f}"] = dump[f]
    for k, v in m.sources.items():
        if ddump["sources"].get(k) != v:
            items[f"sources/{k}"] = v
    for k, v in m.kind_levels.items():
        items[f"kind_levels/{k}"] = v
    _rule_items(items, "norm_rule", m.norm_rules, default_rules())
    _rule_items(items, "detection", m.detection_rules, default_detection_rules(), ordered=False)
    _rule_items(items, "track", m.tracks, tracks_mod.default_tracks())
    return items


def matching_from_items(items: Items) -> MatchSettings:
    d = MatchSettings().model_dump(mode="json")
    for key, value in items.items():
        group, _sep, name = key.partition("/")
        if group == "settings" and name in d:
            d[name] = value
        elif group in ("sources", "kind_levels"):
            d[group][name] = value
    d["norm_rules"] = _rules_from_items(items, "norm_rule", default_rules())
    d["detection_rules"] = _rules_from_items(
        items, "detection", default_detection_rules(), order=False
    )
    d["tracks"] = _rules_from_items(items, "track", tracks_mod.default_tracks())
    return MatchSettings.model_validate(d)


# ---- venues <-> items -----------------------------------------------------------------------


def new_share_id(name: str, taken: set[str]) -> str:
    """A venue's id in shared settings, from its acronym or name ("SIGIR" → "sigir")."""
    base = re.sub(r"[^a-z0-9]+", "-", ascii_fold(name).lower()).strip("-")[:40] or "venue"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


def _ensure_share_ids(s: Session, venues: list[Venue], taken: set[str]) -> None:
    taken = taken | {sid for sid in s.scalars(select(Venue.share_id)) if sid}
    for v in venues:
        if not v.share_id:
            v.share_id = new_share_id(v.short_name or v.name, taken)
            taken.add(v.share_id)
    s.flush()


def variant_key(source: str | None, raw: str) -> str:
    return f"{source or ''}|{raw}"


def _venue_values(v: Venue, sids: dict[int, str]) -> dict[str, Any]:
    """A venue's decisions (none: automatic), its main conferences and parts by share id."""
    j = v.joint or {}
    joint = None
    if j.get("manual") or j.get("use"):
        joint = {
            "parts": [sids[p] for p in j.get("parts") or [] if p in sids]
            if j.get("manual")
            else None,
            "use": sids.get(j.get("use")) if j.get("use") else None,
        }
        joint = {k: x for k, x in joint.items() if x is not None} or {"parts": []}
    hosts = [
        {
            k: x
            for k, x in (
                ("venue", sids[h["venue_id"]]),
                ("from", h.get("from")),
                ("to", h.get("to")),
            )
            if x is not None
        }
        for h in v.hosts or []
        if h.get("venue_id") in sids
    ]
    patterns = [
        VenuePattern.model_validate(p).model_dump(mode="json", exclude_defaults=True)
        for p in v.patterns or []
    ]
    return {
        "kind": v.kind if v.kind_manual and v.kind else None,
        "short_name": (v.short_name or "") if v.short_manual else None,
        "url": v.url or None,
        "record_key": v.record_key or None,
        "match_text": v.match_text or None,
        "patterns": patterns or None,
        "identifiers": {k: x for k, x in sorted((v.identifiers or {}).items()) if x} or None,
        "hosts": hosts or None,
        "joint": joint,
    }


def _venue_items(v: Venue, sids: dict[int, str]) -> Items:
    p = f"venue/{v.share_id}"
    out: Items = {f"{p}/name": v.name}
    decisions = v.decisions or {}
    for f, value in _venue_values(v, sids).items():
        if value is not None:
            out[f"{p}/{f}"] = {"value": value, **decisions.get(f, {})}
    for f in LISTS:
        if entries := getattr(v, f):
            out[f"{p}/{f}"] = [dict(x) for x in entries]
    for k in v.keys:
        if k.manual:
            meta = {"track": k.track, "by": k.decided_by, "on": k.decided_on}
            out[f"{p}/variant/{variant_key(k.source, k.example or k.key)}"] = {
                f: x for f, x in meta.items() if x
            }
    return out


def _referenced(v: Venue) -> set[int]:
    j = v.joint or {}
    ids = {h.get("venue_id") for h in v.hosts or []}
    if j.get("manual"):
        ids |= set(j.get("parts") or [])
    if j.get("use"):
        ids.add(j["use"])
    return {i for i in ids if i}


def local_items(taken: set[str] = frozenset()) -> Items:
    """The local settings' items: the matching settings, and the venues with a manual
    decision or variant (those they name: by name only), given share ids if they had none
    (``taken``: ids of other venues, e.g. in the group's folder)."""
    items = matching_items(load_settings())
    with session_scope() as s:
        all_venues = list(s.scalars(select(Venue).options(selectinload(Venue.keys))))
        by_id = {v.id: v for v in all_venues}
        shared = [v for v in all_venues if v.has_manual or any(k.manual for k in v.keys)]
        named = {i for v in shared for i in _referenced(v)} - {v.id for v in shared}
        named_venues = [by_id[i] for i in sorted(named) if i in by_id]
        _ensure_share_ids(s, [*shared, *named_venues], set(taken))
        sids = {v.id: v.share_id for v in all_venues if v.share_id}
        for v in shared:
            items.update(_venue_items(v, sids))
        for v in named_venues:
            items[f"venue/{v.share_id}/name"] = v.name
    return items


# ---- items <-> files ------------------------------------------------------------------------


def files_from_items(items: Items) -> Files:
    """The folder of the items, each file canonical."""
    settings: dict[str, Any] = {"format": FORMAT, "version": VERSION}
    rules: dict[str, dict[str, Any]] = defaultdict(dict)
    venues: dict[str, dict[str, Any]] = defaultdict(dict)
    for key, value in items.items():
        group, _sep, rest = key.partition("/")
        if group == "settings":
            settings.setdefault("settings", {})[rest] = value
        elif group in ("sources", "kind_levels"):
            settings.setdefault(group, {})[rest] = value
        elif group in ("norm_rule", "detection", "track"):
            rules[group][rest] = value
        elif group in ("norm_rule_order", "track_order"):
            rules[group]["order"] = value
        elif group == "venue":
            sid, _sep, field = rest.partition("/")
            venues[sid][field] = value
    files = {SETTINGS_FILE: dumps(settings)}
    for name, group, entries in (
        (NORM_FILE, "norm_rule", "rules"),
        (DETECTION_FILE, "detection", "rules"),
        (TRACKS_FILE, "track", "tracks"),
    ):
        data: dict[str, Any] = {}
        if rules[group]:
            data[entries] = [rules[group][k] for k in sorted(rules[group])]
        if order := rules[f"{group}_order"].get("order"):
            data["order"] = order
        if data:
            files[name] = dumps(data)
    for sid, fields in venues.items():
        files[f"{VENUES_DIR}/{sid}.json"] = dumps(_venue_file(sid, fields))
    return files


def _venue_file(sid: str, fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": sid, "name": fields.get("name") or sid}
    decisions = {}
    variants = []
    for field, value in fields.items():
        if field in VENUE_FIELDS:
            out[field] = value["value"]
            if meta := {k: x for k, x in value.items() if k != "value" and x}:
                decisions[field] = meta
        elif field in LISTS:
            out[field] = value
        elif field.startswith("variant/"):
            source, _sep, raw = field.removeprefix("variant/").partition("|")
            variants.append({"raw": raw, **({"source": source} if source else {}), **value})
    if decisions:
        out["decisions"] = decisions
    if variants:
        out["variants"] = sorted(variants, key=lambda x: (x.get("source", ""), x["raw"]))
    return out


class FormatError(ValueError):
    """A file of the folder is not valid."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


def _load(path: str, text: str) -> Any:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise FormatError(path, _("not valid JSON ({error})").format(error=err)) from err
    if not isinstance(data, dict):
        raise FormatError(path, _("not a JSON object"))
    return data


def items_from_files(files: Files) -> Items:
    """The items of a folder (``FormatError`` when a file is invalid); other files are
    ignored. An empty folder: no item (the app's defaults)."""
    items: Items = {}
    if text := files.get(SETTINGS_FILE):
        data = _load(SETTINGS_FILE, text)
        if data.get("format") != FORMAT:
            raise FormatError(SETTINGS_FILE, _("not a shared settings folder (format)"))
        if (version := data.get("version") or 0) > VERSION:
            raise FormatError(
                SETTINGS_FILE,
                _("written by a newer version of the app (format {version})").format(
                    version=version
                ),
            )
        for group in ("settings", "sources", "kind_levels"):
            for k, v in (data.get(group) or {}).items():
                items[f"{group}/{k}"] = v
    for name, group, entries, model in (
        (NORM_FILE, "norm_rule", "rules", NormRule),
        (DETECTION_FILE, "detection", "rules", DetectionRule),
        (TRACKS_FILE, "track", "tracks", tracks_mod.Track),
    ):
        if text := files.get(name):
            data = _load(name, text)
            for r in data.get(entries) or []:
                if not isinstance(r, dict) or not r.get("id"):
                    raise FormatError(name, _("an entry without an id"))
                if not r.get("removed"):
                    try:
                        r = _dump(model.model_validate(r))
                    except ValueError as err:
                        raise FormatError(name, f"{r['id']}: {err}") from err
                else:
                    r = {"id": r["id"], "removed": True}
                items[f"{group}/{r['id']}"] = r
            if order := data.get("order"):
                items[f"{group}_order"] = list(order)
    for path, text in files.items():
        if path.startswith(VENUES_DIR + "/") and path.endswith(".json"):
            items.update(_venue_items_of(path, _load(path, text)))
    return items


def _venue_items_of(path: str, data: dict[str, Any]) -> Items:
    sid = path.removeprefix(VENUES_DIR + "/").removesuffix(".json")
    if data.get("id") != sid or not SHARE_ID.match(sid):
        raise FormatError(path, _("its id must be its file's name ({id})").format(id=sid))
    p = f"venue/{sid}"
    items: Items = {f"{p}/name": str(data.get("name") or sid)}
    decisions = data.get("decisions") or {}
    for f in VENUE_FIELDS:
        if (value := data.get(f)) is not None:
            if f == "patterns":
                try:
                    value = [
                        VenuePattern.model_validate(x).model_dump(
                            mode="json", exclude_defaults=True
                        )
                        for x in value
                    ]
                except ValueError as err:
                    raise FormatError(path, _("venue rules: {error}").format(error=err)) from err
            items[f"{p}/{f}"] = {"value": value, **decisions.get(f, {})}
    for f, cleaner in (("levels", clean), ("names", clean_name)):
        if data.get(f):
            try:
                items[f"{p}/{f}"] = normalized(data[f], cleaner)
            except (ValueError, TypeError) as err:
                raise FormatError(path, f"{f}: {err}") from err
    for var in data.get("variants") or []:
        if not isinstance(var, dict) or not var.get("raw"):
            raise FormatError(path, _("a variant without its text (raw)"))
        meta = {k: var[k] for k in ("track", "by", "on") if var.get(k)}
        items[f"{p}/variant/{variant_key(var.get('source'), var['raw'])}"] = meta
    return items


def check(files: Files) -> list[str]:
    """What is wrong in a folder: invalid files, regexes that do not compile, files not
    written canonically, venues named (main conference, joint venue) but missing."""
    try:
        items = items_from_files(files)
    except FormatError as err:
        return [str(err)]
    problems = []
    m = matching_from_items(items)
    for r in m.norm_rules:
        if r.pattern and r.compiled() is None:
            problems.append(f"{NORM_FILE}: {r.id}: " + _("invalid regular expression"))
    for t in m.tracks:
        for r in [*t.rules, *t.name_rules]:
            if r.pattern and r.compiled() is None:
                problems.append(f"{TRACKS_FILE}: {t.id}/{r.id}: " + _("invalid regular expression"))
    sids = {k.split("/")[1] for k in items if k.startswith("venue/")}
    for key, value in items.items():
        if key.endswith("/patterns"):
            for p in value["value"]:
                if VenuePattern.model_validate(p).compiled() is None:
                    problems.append(f"{key}: " + _("invalid regular expression"))
        named = []
        if key.endswith("/hosts"):
            named = [h.get("venue") for h in value["value"]]
        elif key.endswith("/joint"):
            named = [*(value["value"].get("parts") or []), value["value"].get("use")]
        for sid in named:
            if sid and sid not in sids:
                problems.append(f"{key}: " + _("no venue {id}").format(id=sid))
    canonical = files_from_items(items)
    for path, text in files.items():
        if path in canonical and text != canonical[path]:
            problems.append(f"{path}: " + _("not written canonically (run with --fix)"))
    return problems


# ---- three-way merge ------------------------------------------------------------------------


@dataclass
class Conflict:
    """An item changed both here and in the group since the last pull, differently."""

    key: str
    base: Any
    local: Any
    remote: Any


def merge3(base: Items, local: Items, remote: Items) -> tuple[Items, list[Conflict]]:
    """The group's changes since ``base`` applied to the local items, the local ones kept:
    an item changed on one side only takes that side's value (removed included); changed
    on both, differently, it is a conflict (its local value kept in the result). A venue's
    name never conflicts: the group's is taken."""
    merged: Items = {}
    conflicts = []
    for key in sorted(base.keys() | local.keys() | remote.keys()):
        b, lo, r = base.get(key), local.get(key), remote.get(key)
        cb, cl, cr = comparable(b), comparable(lo), comparable(r)
        if cl in (cr, cb):
            value = r
        elif cr == cb:
            value = lo
        elif key.endswith("/name") and key.startswith("venue/") and r is not None:
            value = r
        else:
            conflicts.append(Conflict(key, b, lo, r))
            value = lo
        if value is not None:
            merged[key] = value
    return merged, conflicts


def resolved(merged: Items, conflicts: list[Conflict], take_group: set[str]) -> Items:
    """The merge with the conflicts whose key is in ``take_group`` given the group's value."""
    out = dict(merged)
    for c in conflicts:
        if c.key in take_group:
            if c.remote is None:
                out.pop(c.key, None)
            else:
                out[c.key] = c.remote
    return out


def changes(old: Items, new: Items) -> list[str]:
    """The keys whose value differs (who set it aside)."""
    return sorted(
        k for k in old.keys() | new.keys() if comparable(old.get(k)) != comparable(new.get(k))
    )


def chosen(base: Items, local: Items, keys: set[str]) -> Items:
    """The group's items with the local changes of ``keys``: what a push proposes. A venue
    changed or named gets its name too."""
    out = dict(base)
    for k in keys:
        if k in local:
            out[k] = local[k]
        else:
            out.pop(k, None)
    named = {k.split("/")[1] for k in out if k.startswith("venue/")} | _named_by(out)
    for sid in named:
        name = f"venue/{sid}/name"
        if name not in out and name in local:
            out[name] = local[name]
    # (a venue left with its name only, and named by no other: removed)
    for sid in {k.split("/")[1] for k in out if k.startswith("venue/")}:
        own = [k for k in out if k.startswith(f"venue/{sid}/")]
        if own == [f"venue/{sid}/name"] and sid not in _named_by(out):
            out.pop(own[0])
    return out


def _named_by(items: Items) -> set[str]:
    out: set[str] = set()
    for k, value in items.items():
        if k.endswith("/hosts"):
            out |= {h.get("venue") for h in value["value"]}
        elif k.endswith("/joint"):
            out |= {*(value["value"].get("parts") or []), value["value"].get("use")}
    return out - {None}


# ---- items -> database ----------------------------------------------------------------------


def link_venues(remote: Items) -> int:
    """Give the group's venues not known here by their share id the share id of the local
    venue of the same name or variant (without one yet); returns how many were linked."""
    names = {
        k.split("/")[1]: v
        for k, v in remote.items()
        if k.startswith("venue/") and k.endswith("/name")
    }
    variants = defaultdict(list)
    for k in remote:
        if k.startswith("venue/") and "/variant/" in k:
            sid, _v, var = k.removeprefix("venue/").split("/", 2)
            source, _sep, raw = var.partition("|")
            variants[sid].append((raw, source or None))
    n = 0
    with session_scope() as s, authorship.keeping():
        known = {sid for sid in s.scalars(select(Venue.share_id)) if sid}
        for sid, name in names.items():
            if sid in known:
                continue
            v = _local_venue(s, name, variants[sid])
            if v is not None and not v.share_id:
                v.share_id = sid
                known.add(sid)
                n += 1
    return n


def _local_venue(s: Session, name: str, variants: list[tuple[str, str | None]]) -> Venue | None:
    for raw, source in variants:
        if (key := service.key(raw, source)) and (vk := s.get(VenueKey, key)):
            return vk.venue
    v = s.scalar(select(Venue).where(Venue.name == name).limit(1))
    if v is None and (key := service.key(name)) and (vk := s.get(VenueKey, key)):
        v = vk.venue
    return v


def apply(new: Items, old: Items) -> dict[str, int]:
    """Make the local settings those of ``new`` (``old``: the local items now), the
    decisions keeping their authors. Returns what changed."""
    changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    counts = {"settings": 0, "venues": 0}
    with authorship.keeping():
        if any(not k.startswith("venue/") for k in changed):
            counts["settings"] = _apply_matching(new)
        by_venue: dict[str, set[str]] = defaultdict(set)
        for k in changed:
            if k.startswith("venue/"):
                sid, _sep, field = k.removeprefix("venue/").partition("/")
                by_venue[sid].add(field)
        if by_venue:
            counts["venues"] = _apply_venues(new, by_venue)
    if any(counts.values()):
        service.invalidate(clear_cache=True)
        venue_match.refresh(reassign=True)
    return counts


def _apply_matching(new: Items) -> int:
    before = load_settings()
    settings = matching_from_items(new)
    gone = {t.id for t in before.tracks} - {t.id for t in settings.tracks}
    save_settings(settings)
    if gone:
        annotations.forget_tracks(gone)
    return 1


def _apply_venues(new: Items, by_venue: dict[str, set[str]]) -> int:
    def item(sid: str, field: str) -> Any:
        return new.get(f"venue/{sid}/{field}")

    with session_scope() as s:
        venues: dict[str, Venue] = {
            v.share_id: v for v in s.scalars(select(Venue).where(Venue.share_id.is_not(None)))
        }

        def venue(sid: str) -> Venue | None:
            if sid not in venues and (name := item(sid, "name")):
                v = _local_venue(s, name, [])
                if v is None or v.share_id:
                    v = Venue(name=name)
                    s.add(v)
                v.share_id = sid
                s.flush()
                venues[sid] = v
            return venues.get(sid)

        for sid, fields in by_venue.items():
            v = venue(sid)
            if v is None:
                continue
            if "name" in fields and (name := item(sid, "name")) and name != v.name:
                v.name = name
            decisions = dict(v.decisions or {})
            for f in VENUE_FIELDS:
                if f in fields and f not in ("hosts", "joint"):
                    _set_field(v, f, item(sid, f))
            for f in LISTS:
                if f in fields:
                    setattr(v, f, item(sid, f))
            for f in fields & set(VENUE_FIELDS):
                value = item(sid, f)
                meta = {k: x for k, x in (value or {}).items() if k != "value" and x}
                if meta:
                    decisions[f] = meta
                else:
                    decisions.pop(f, None)
            v.decisions = decisions or None
            for field in fields:
                if field.startswith("variant/"):
                    _set_variant(s, v, field.removeprefix("variant/"), item(sid, field))
        s.flush()
        ids = {sid: v.id for sid, v in venues.items()}
        for sid, fields in by_venue.items():
            v = venues.get(sid)
            if v is None:
                continue
            for f in ("hosts", "joint"):
                if f in fields:
                    value = item(sid, f)
                    if value is not None:
                        for named in _names(f, value["value"]):
                            if venue(named) is not None:
                                ids[named] = venues[named].id
                    _set_link(v, f, value, ids)
    return len(by_venue)


def _names(field: str, value: Any) -> list[str]:
    if field == "hosts":
        return [h["venue"] for h in value if h.get("venue")]
    return [x for x in (*(value.get("parts") or []), value.get("use")) if x]


def _set_field(v: Venue, field: str, value: dict[str, Any] | None) -> None:
    x = value["value"] if value is not None else None
    if field == "kind":
        if x:
            v.kind, v.kind_manual = x, True
        else:
            v.kind_manual = False
    elif field == "short_name":
        v.short_name, v.short_manual = (x or None, True) if x is not None else (None, False)
    elif field == "patterns":
        v.patterns = [VenuePattern.model_validate(p).model_dump() for p in x] if x else None
    else:
        setattr(v, field, x or None)


def _set_link(v: Venue, field: str, value: dict[str, Any] | None, ids: dict[str, int]) -> None:
    x = value["value"] if value is not None else None
    if field == "hosts":
        hosts = [
            {"venue_id": ids[h["venue"]], "from": h.get("from"), "to": h.get("to")}
            for h in x or []
            if h.get("venue") in ids and ids[h["venue"]] != v.id
        ]
        v.hosts = hosts or None
        return
    current = v.joint or {}
    if x is None:
        if current.get("manual") or current.get("use"):
            v.joint = None
        return
    joint = {k: y for k, y in current.items() if k not in ("manual", "use")}
    if x.get("parts") is not None:
        joint.update(parts=[ids[p] for p in x["parts"] if p in ids], manual=True)
    joint["use"] = ids.get(x["use"]) if x.get("use") else None
    v.joint = joint


def _set_variant(s: Session, v: Venue, var: str, value: dict[str, Any] | None) -> None:
    source, _sep, raw = var.partition("|")
    key = service.key(raw, source or None)
    if not key:
        return
    vk = s.get(VenueKey, key)
    if value is None:  # (no longer assigned by hand: matched automatically again)
        if vk is not None and vk.venue_id == v.id:
            vk.manual, vk.decided_by, vk.decided_on = False, None, None
        return
    if vk is None:
        vk = VenueKey(key=key, venue_id=v.id, example=raw, source=source or None)
        s.add(vk)
    vk.venue_id, vk.manual, vk.track = v.id, True, value.get("track")
    vk.decided_by, vk.decided_on = value.get("by"), value.get("on")


# ---- describing items -----------------------------------------------------------------------


def describe(key: str, items: Items) -> str:
    """An item for people: "SIGIR · level", "Cleaning rule custom1", "Setting min_score"."""
    group, _sep, rest = key.partition("/")
    if group == "venue":
        sid, _sep, field = rest.partition("/")
        name = items.get(f"venue/{sid}/name") or sid
        if field.startswith("variant/"):
            source, _sep, raw = field.removeprefix("variant/").partition("|")
            return _("{venue} · variant “{text}”").format(venue=name, text=raw) + (
                f" ({source})" if source else ""
            )
        return f"{name} · {_(_FIELD_LABELS[field]) if field in _FIELD_LABELS else field}"
    labels = {
        "settings": _("Setting {name}"),
        "sources": _("Ranking source {name}"),
        "kind_levels": _("Default level of {name}"),
        "norm_rule": _("Cleaning rule {name}"),
        "detection": _("Detection rule {name}"),
        "track": _("Track {name}"),
    }
    if group in ("norm_rule_order", "track_order"):
        return (
            _("Order of the cleaning rules")
            if group == "norm_rule_order"
            else _("Order of the tracks")
        )
    return labels.get(group, "{name}").format(name=rest)


_FIELD_LABELS = {
    "name": N_("name"),
    "kind": N_("kind"),
    "short_name": N_("short name"),
    "url": N_("website"),
    "record_key": N_("ranking record"),
    "match_text": N_("search text"),
    "patterns": N_("venue rules"),
    "identifiers": N_("identifiers"),
    "hosts": N_("main conferences"),
    "joint": N_("joint venue"),
    "levels": N_("levels"),
    "names": N_("former names"),
}


def show(value: Any) -> str:
    """An item's value in a line (none: "—")."""
    if value is None:
        return "—"
    v = value
    if isinstance(v, dict) and "value" in v:
        v = v["value"]
    if isinstance(v, list) and v and isinstance(v[0], dict) and "rank" in v[0]:
        from .ranking.levels import label

        return label(v)
    if isinstance(v, list) and v and isinstance(v[0], dict) and "name" in v[0]:
        from .ranking.levels import range_label

        return " · ".join(f"{n['name']} ({range_label(n)})" for n in v)
    if isinstance(v, dict | list):
        return json.dumps(comparable(v), ensure_ascii=False, sort_keys=True)
    return str(v)


def author(value: Any) -> str:
    """Who set an item, when ("jdoe, 2026-10-01"), if known."""
    if isinstance(value, list):
        dated = [x for x in value if isinstance(x, dict) and x.get("on")]
        value = max(dated, key=lambda x: x["on"]) if dated else None
    if not isinstance(value, dict):
        return ""
    return ", ".join(x for x in (value.get("by"), value.get("on")) if x)
