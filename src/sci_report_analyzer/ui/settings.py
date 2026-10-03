"""Settings: matching, flags, tags, data, API keys, import/export."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from nicegui import ui
from sqlalchemy import delete, func, select

from .. import (
    annotations,
    config,
    contribution,
    datadir,
    keys,
    pdfs,
    reports,
    settings_io,
    source_settings,
    venue_match,
)
from ..db.models import JcrRecord
from ..db.session import session_scope
from ..i18n import _, ngettext
from ..ranking import datasets
from ..ranking.badge import SOURCE_LABELS, TOGGLABLE_SOURCES, TRACK_LABEL
from ..ranking.kinds import KINDS
from ..ranking.normalize import NormRule, apply_rules, default_rules, normalize
from ..ranking.service import load_settings, save_settings, service
from ..sources import ADAPTERS
from . import scimago_years
from .theme import badge_details, fmt_dt, frame, level_hint, level_options, rank_chip


def register() -> None:
    @ui.page("/settings")
    def settings_page(tab: str = "matching") -> None:
        with frame(_("Settings")):
            ui.label(_("Settings")).classes("text-2xl")
            with ui.tabs(value=tab).classes("w-full") as tabs:
                for name, label in (
                    ("matching", _("Matching")),
                    ("rules", _("Normalization rules")),
                    ("kinds", _("Venue kinds")),
                    ("flags", _("Flags, tags & categories")),
                    ("contribution", _("Contribution roles")),
                    ("reports", _("Report templates")),
                    ("data", _("Data & cache")),
                    ("keys", _("API keys")),
                    ("io", _("Import / export")),
                ):
                    ui.tab(name, label)
            with ui.tab_panels(tabs, value=tab).classes("w-full"):
                with ui.tab_panel("matching"):
                    matching_tab()
                with ui.tab_panel("rules"):
                    rules_tab()
                with ui.tab_panel("kinds"):
                    kinds_tab()
                with ui.tab_panel("flags"):
                    flags_tab()
                with ui.tab_panel("contribution"):
                    contribution_tab()
                with ui.tab_panel("reports"):
                    report_templates_tab()
                with ui.tab_panel("data"):
                    data_tab()
                with ui.tab_panel("keys"):
                    keys_tab()
                with ui.tab_panel("io"):
                    io_tab()


# ---- contribution roles --------------------------------------------------------------------


def contribution_tab() -> None:
    """The roles of a person in a paper (the publications panel's "Contribution role"
    chart), and the rules giving them from the person's position: the first that matches
    wins, else the catch-all role."""
    cfg = contribution.load_config()

    def small_button(icon: str, on_click, enabled: bool = True, negative: bool = False) -> None:
        b = ui.button(icon=icon, on_click=on_click).props("flat round dense")
        if negative:
            b.props("color=negative")
        b.set_enabled(enabled)

    def move(items: list, i: int, d: int, refresh) -> None:
        items[i], items[i + d] = items[i + d], items[i]
        refresh()

    # -- roles
    ui.label(_("Roles")).classes("text-lg")
    ui.label(
        _("In their order on the chart (e.g. from doing the work to supervising it).")
    ).classes("text-sm text-grey")

    @ui.refreshable
    def roles_list() -> None:
        for i, r in enumerate(cfg.roles):
            used = r.key == cfg.fallback or any(x.role == r.key for x in cfg.rules)
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"contribution-role-{i}"):
                ui.color_input(
                    value=r.colour,
                    preview=True,
                    on_change=lambda e, r=r: setattr(r, "colour", e.value),
                ).props("dense outlined").classes("w-32")
                ui.input(
                    value=r.label, on_change=lambda e, r=r: setattr(r, "label", e.value or "")
                ).props("dense outlined").classes("w-48").on("blur", lambda: rules_list.refresh())
                small_button(
                    "arrow_upward", lambda i=i: move(cfg.roles, i, -1, roles_list.refresh), i > 0
                )
                small_button(
                    "arrow_downward",
                    lambda i=i: move(cfg.roles, i, 1, roles_list.refresh),
                    i < len(cfg.roles) - 1,
                )
                with ui.element():
                    small_button("delete", lambda i=i: remove_role(i), not used, negative=True)
                    if used:
                        ui.tooltip(_("Used by a rule (or the catch-all)"))

    def remove_role(i: int) -> None:
        del cfg.roles[i]
        refresh_all()

    def add_role() -> None:
        cfg.roles.append(
            contribution.Role(contribution.new_role_key(cfg), _("New role"), "#9e9e9e")
        )
        refresh_all()

    with ui.column().classes("gap-1"):
        roles_list()
    ui.button(_("Add a role"), icon="add", on_click=add_role).props("flat dense").mark(
        "contribution-add-role"
    )

    # -- rules
    ui.label(_("Rules")).classes("text-lg mt-4")
    ui.markdown(
        _(
            "The first rule that matches gives the role, else the catch-all. A condition uses "
            "`n` (the number of authors), `p` (the person's position, from 1) and `phd` (one of "
            "their PhD students is an author). Compared with `p`, a negative number counts from "
            "the end (`p=-1`: the last author, `p>=-3`: one of the last three) and a percentage "
            "is the place in the list (0%: the first author, 100%: the last). Operators: "
            "`= != < <= > >=`, `and`, `or`, `not`, parentheses. E.g. "
            "`n>=12 and p>=25% and p<=75%`."
        )
    ).classes("text-sm text-grey")

    def role_options() -> dict[str, str]:
        return {r.key: r.label or _("(no name)") for r in cfg.roles}

    @ui.refreshable
    def rules_list() -> None:
        options = role_options()
        for i, r in enumerate(cfg.rules):
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"contribution-rule-{i}"):
                ui.label(f"{i + 1}.").classes("w-6 text-grey")
                ui.select(
                    options, value=r.role, on_change=lambda e, r=r: set_role(r, e.value)
                ).props("dense outlined").classes("w-40")
                ui.label(_("if")).classes("text-grey")
                ui.input(
                    value=r.condition,
                    on_change=lambda e, r=r: setattr(r, "condition", e.value or ""),
                    validation=contribution.check_condition,
                ).props("dense outlined").classes("w-96").mark(f"contribution-condition-{i}")
                small_button(
                    "arrow_upward", lambda i=i: move(cfg.rules, i, -1, rules_list.refresh), i > 0
                )
                small_button(
                    "arrow_downward",
                    lambda i=i: move(cfg.rules, i, 1, rules_list.refresh),
                    i < len(cfg.rules) - 1,
                )
                small_button("delete", lambda i=i: remove_rule(i), negative=True)
        # The catch-all: always last, without a condition.
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.label("").classes("w-6")
            ui.select(
                options,
                value=cfg.fallback,
                on_change=lambda e: set_fallback(e.value),
            ).props("dense outlined").classes("w-40").mark("contribution-fallback")
            ui.label(_("otherwise (catch-all)")).classes("text-grey")

    def set_role(rule: contribution.Rule, key: str) -> None:
        rule.role = key
        roles_list.refresh()

    def set_fallback(key: str) -> None:
        cfg.fallback = key
        roles_list.refresh()

    def remove_rule(i: int) -> None:
        del cfg.rules[i]
        refresh_all()

    def add_rule() -> None:
        cfg.rules.append(contribution.Rule(cfg.fallback, ""))
        refresh_all()

    def refresh_all() -> None:
        roles_list.refresh()
        rules_list.refresh()

    with ui.column().classes("gap-1"):
        rules_list()

    def reset() -> None:
        d = contribution.default_config()
        cfg.roles, cfg.rules, cfg.fallback = d.roles, d.rules, d.fallback
        refresh_all()

    def save() -> None:
        try:
            contribution.save_config(cfg)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return
        ui.notify(_("Saved (reload a person's page to see it)"), type="positive")

    with ui.row().classes("mt-2"):
        ui.button(_("Add a rule"), icon="add", on_click=add_rule).props("flat").mark(
            "contribution-add-rule"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        ui.button(_("Save"), icon="save", on_click=save).mark("contribution-save")


# ---- report templates ----------------------------------------------------------------------


def report_templates_tab() -> None:
    """How a paper is cited when inserted in a report (the report editor's "Cite as")."""
    cfg = reports.load_templates()
    ui.markdown(
        _(
            "How a paper is cited when inserted in a report: `[@key]` followed by the template. "
            "`{.notes}`, `{.tags}`, `{.full}` (title, venue…) as in the report's help; otherwise "
            "the fields `.number` (the number, formatted), `.index` (the bare number), `.title`, "
            "`.venue`, `.short-venue` (its acronym), `.year`, `.tags`, `.notes` are replaced and "
            "the rest kept, a parenthesis without a value dropped: `{**#.index** (.short-venue "
            ".year)}` gives **#2** (EMNLP 2026), `{**#.index** (.short-venue .year): .notes}` "
            "follows it with the notes. Empty: the number."
        )
    ).classes("text-sm text-grey")

    @ui.refreshable
    def rows() -> None:
        for i, t in enumerate(cfg.items):
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"report-template-{i}"):
                default = cfg.default == t.attrs
                ui.button(
                    icon="star" if default else "star_border", on_click=lambda t=t: set_default(t)
                ).props("flat round dense color=amber-8").tooltip(
                    _("The default") if default else _("Make it the default")
                ).mark(f"report-template-default-{i}")
                ui.input(
                    _("Label"), value=t.label, on_change=lambda e, t=t: setattr(t, "label", e.value)
                ).props("dense outlined").classes("w-56")
                ui.input(
                    _("Template"),
                    value=t.attrs,
                    on_change=lambda e, t=t: set_attrs(t, e.value or ""),
                    validation=reports.check_template,
                ).props("dense outlined").classes("w-96").mark(f"report-template-attrs-{i}")
                b = ui.button(icon="delete", on_click=lambda i=i: remove(i))
                b.props("flat round dense color=negative")

    def set_default(t: reports.Template) -> None:
        cfg.default = t.attrs
        rows.refresh()

    def set_attrs(t: reports.Template, attrs: str) -> None:
        if cfg.default == t.attrs:
            cfg.default = attrs
        t.attrs = attrs

    def remove(i: int) -> None:
        del cfg.items[i]
        rows.refresh()

    def add() -> None:
        cfg.items.append(reports.Template(_("New template"), "{.short-venue .year}"))
        rows.refresh()

    def reset() -> None:
        d = reports.default_templates()
        cfg.items, cfg.default = d.items, d.default
        rows.refresh()

    def save() -> None:
        try:
            reports.save_templates(cfg)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return
        rows.refresh()
        ui.notify(_("Saved (reload a report to see it)"), type="positive")

    with ui.column().classes("gap-1"):
        rows()
    with ui.row().classes("mt-2"):
        ui.button(_("Add a template"), icon="add", on_click=add).props("flat").mark(
            "report-template-add"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        ui.button(_("Save"), icon="save", on_click=save).mark("report-templates-save")


# ---- matching ------------------------------------------------------------------------------


def _publication_sources() -> None:
    ui.label(_("Publication sources")).classes("text-lg")
    ui.label(
        _(
            "Sources the app uses: a disabled one is neither searched nor synced, and its records "
            "are left out of the publications and venues (they are kept: enabling it again brings "
            "them back). DOI: the records registered with the papers' DOIs (Crossref, DataCite), "
            "the main source of the papers that have one."
        )
    ).classes("text-sm text-grey")
    off = source_settings.disabled()
    boxes = {}
    with ui.row().classes("gap-4"):
        for name, a in ADAPTERS.items():
            boxes[name] = ui.checkbox(a.label, value=name not in off).mark(f"use-source-{name}")

    def save() -> None:
        source_settings.set_disabled({n for n, b in boxes.items() if not b.value})
        ui.notify(_("Saved: re-sync to fetch from newly enabled sources"), type="positive")

    ui.button(_("Save"), icon="save", on_click=save).props("dense").mark("use-sources-save")
    ui.label(_("Primary source")).classes("text-lg mt-4")
    ui.label(
        _(
            "A paper the primary source doesn't list for a person is not counted (stats, "
            "reports, summary): e.g. HAL, where CNRS researchers must deposit their papers. "
            "The other sources still help find its venue. Applies to people with a validated "
            "profile on that source; a folder can use another one."
        )
    ).classes("text-sm text-grey")
    choices = {n: a.label for n, a in ADAPTERS.items() if a.linkable and a.provides_publications}

    def set_primary(e) -> None:
        source_settings.set_default_primary(e.value)
        ui.notify(_("Saved"), type="positive")

    ui.select(
        {None: _("None"), **choices},
        value=source_settings.default_primary(),
        on_change=set_primary,
    ).classes("w-48").mark("primary-source")


def matching_tab() -> None:
    st = load_settings()
    _publication_sources()
    ui.label(_("Ranking sources")).classes("text-lg mt-6")
    boxes = {}
    for src in TOGGLABLE_SOURCES:
        boxes[src] = ui.checkbox(SOURCE_LABELS[src], value=st.source_on(src))
    ui.label(_("Minimum match confidence")).classes("text-lg mt-4")
    with ui.row().classes("items-center w-96"):
        slider = ui.slider(min=0.4, max=1.0, step=0.05, value=st.min_score).classes("grow")
        ui.label().bind_text_from(slider, "value", lambda v: f"{v:.2f}")
    ui.label(
        _(
            "Fuzzy matches below this score fall back to OpenAlex (if enabled) or are "
            "reported as not ranked."
        )
    ).classes("text-sm text-grey")

    def save() -> None:
        new = load_settings()
        new.sources = {k: b.value for k, b in boxes.items()}
        new.min_score = float(slider.value)
        save_settings(new)
        ui.notify(_("Saved — cached matches cleared"), type="positive")

    ui.button(_("Save"), icon="save", on_click=save).classes("mt-2")


def rules_tab() -> None:
    ui.label(_("Normalization rules")).classes("text-lg")
    ui.label(
        _(
            "Python regular expressions applied, in order, to the venue texts of the sources "
            "(\\1, \\2… or \\g<name> in the replacement; \\b, \\d, \\w are ASCII). The "
            "cleaned text, lowercased and without accents or punctuation, is the key matching the "
            "venues' variants; it is also what the rankings are searched with. Which venue a text "
            "belongs to is then set on the venues (variants and venue rules, on the Venues page "
            "or from a paper's details)."
        )
    ).classes("text-grey text-sm")
    rules = [r.model_copy() for r in load_settings().norm_rules]
    sources = {k: a.label for k, a in ADAPTERS.items()}

    @ui.refreshable
    def listing() -> None:
        for i, r in enumerate(rules):
            with ui.column().classes("w-full gap-1 border rounded p-2").mark(f"norm-rule-{i}"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.checkbox(value=r.enabled).bind_value(r, "enabled").tooltip(
                        _("enabled")
                    ).mark(f"norm-enabled-{i}")
                    ui.input(_("name"), value=r.name).bind_value(r, "name").props(
                        "dense outlined"
                    ).classes("w-56")
                    ui.input(
                        _("pattern"), value=r.pattern, on_change=lambda e: preview(e)
                    ).bind_value(r, "pattern").props("dense outlined").classes(
                        "grow font-mono"
                    ).mark(f"norm-pattern-{i}")
                    ui.icon("arrow_forward")
                    ui.input(
                        _("replacement"), value=r.replacement, on_change=lambda e: preview(e)
                    ).bind_value(r, "replacement").props("dense outlined").classes("w-32 font-mono")
                    with ui.column().classes("gap-0"):
                        ui.button(icon="arrow_upward", on_click=lambda i=i: move(i, -1)).props(
                            "flat round dense size=xs"
                        )
                        ui.button(icon="arrow_downward", on_click=lambda i=i: move(i, 1)).props(
                            "flat round dense size=xs"
                        )
                    ui.button(
                        icon="delete", on_click=lambda i=i: (rules.pop(i), listing.refresh())
                    ).props("flat round dense color=negative")
                with ui.row().classes("items-center gap-2 w-full no-wrap pl-10"):
                    ui.checkbox(_("ignore case"), value=r.ignore_case).bind_value(r, "ignore_case")
                    ui.select(
                        sources, multiple=True, label=_("only for"), value=list(r.sources)
                    ).bind_value(r, "sources").props("dense outlined use-chips").classes(
                        "min-w-32"
                    ).tooltip(_("Sources whose venue texts the rule applies to (empty: all)"))
                    ui.input(_("description"), value=r.description).bind_value(
                        r, "description"
                    ).props("dense outlined").classes("grow")
                    if r.example:
                        ex = ui.label().classes("text-xs text-grey font-mono")
                        ex.text = f"“{r.example}” → “{r.apply(r.example).strip()}”"
                    if r.compiled() is None:
                        ui.label(_("invalid regex")).classes("text-negative text-xs")

    def move(i: int, d: int) -> None:
        j = i + d
        if 0 <= j < len(rules):
            rules[i], rules[j] = rules[j], rules[i]
            listing.refresh()
            preview()

    listing()

    def add() -> None:
        n = 1 + sum(r.id.startswith("custom") for r in rules)
        rules.insert(
            0, NormRule(id=f"custom{n}", name=_("Custom rule {n}").format(n=n), pattern="")
        )
        listing.refresh()

    def reset() -> None:
        rules[:] = default_rules()
        listing.refresh()
        preview()

    with ui.row().classes("items-center gap-2"):
        ui.button(_("Add a rule (first)"), icon="add", on_click=add).props("flat").mark("norm-add")
        ui.button(_("Reset to the defaults"), icon="restart_alt", on_click=reset).props("flat")

    # Live preview with the rules as edited (saved or not).
    with ui.row().classes("items-center gap-2 w-full"):
        test = (
            ui.input(_("Try a venue text"), placeholder="NeurIPS 2024 Workshop on Foo")
            .props("dense outlined clearable debounce=300")
            .classes("grow")
        )
        test_source = (
            ui.select({"": _("any source"), **sources}, value="", label=_("source"))
            .props("dense outlined")
            .classes("w-40")
        )
    out = ui.column().classes("gap-0")
    changes = ui.column().classes("gap-0 w-full").mark("norm-changes")

    def valid() -> bool:
        bad = [r.name for r in rules if r.pattern and r.compiled() is None]
        if bad:
            ui.notify(_("Invalid rule: {names}").format(names=", ".join(bad)), type="negative")
        return not bad

    def preview(_e=None) -> None:
        out.clear()
        text = test.value or ""
        src = test_source.value or None
        if text:
            with out:
                v = text
                for r in rules:
                    if r.pattern and r.applies_to(src) and (after := r.apply(v)) != v:
                        ui.label(f"{r.name}: “{after.strip()}”").classes("font-mono text-xs")
                        v = after
                cleaned = apply_rules(text, [r for r in rules if r.pattern], src)
                ui.label(
                    _("cleaned: “{cleaned}” · key: “{key}”").format(
                        cleaned=cleaned, key=normalize(cleaned)
                    )
                ).classes("font-mono text-sm")

    def impact() -> None:
        """Venue texts whose key changes with the edited rules."""
        changes.clear()
        if not valid():
            return
        edited = [r for r in rules if r.pattern]
        diff = []
        for m in venue_match.matches().values():
            new = normalize(apply_rules(m.raw, edited, m.source))
            if new != m.key:
                diff.append((m, new))
        with changes:
            ui.label(_("{n} venue text(s) get another key").format(n=len(diff))).classes("text-sm")
            for m, new in diff[:30]:
                ui.label(f"[{m.source}] {m.raw}: “{m.key}” → “{new}”").classes("text-xs font-mono")

    test.on_value_change(preview)
    test_source.on_value_change(preview)

    def save() -> None:
        if not valid():
            return
        new = load_settings()
        new.norm_rules = [r for r in rules if r.pattern]
        save_settings(new)
        n = venue_match.refresh()
        ui.notify(
            _("Normalization rules saved — {n} venue text(s) re-matched").format(n=n),
            type="positive",
        )

    with ui.row().classes("gap-2"):
        ui.button(_("Check the effect on the venue texts"), icon="rule", on_click=impact).props(
            "flat"
        ).mark("norm-impact")
        ui.button(_("Save the rules"), icon="save", on_click=save).mark("norm-save")


# ---- corrections & levels ------------------------------------------------------------------


def lookup_tab() -> None:
    out = ui.column().classes("w-full")

    async def run(e) -> None:
        out.clear()
        if not e.value:
            return
        badge = await service.resolve(e.value)
        with out:
            ui.label(_("Cleaned as: “{text}”").format(text=service.clean(e.value))).classes(
                "text-sm text-grey"
            )
            with ui.row().classes("items-center gap-2"):
                rank_chip(badge)
                ui.label(badge_details(badge).replace("\n", " · ") if badge else _("not ranked"))
            ui.label(_("Candidates")).classes("font-medium mt-2")
            for b in service.candidates(e.value):
                with ui.row().classes("items-center gap-2"):
                    rank_chip(b)
                    ui.label(f"{b.name} · {b.source} · {round(b.score * 100)}%")

    ui.input(_("Venue to look up"), on_change=run).props("debounce=500 clearable").classes("w-full")


# ---- flags ---------------------------------------------------------------------------------


def flags_tab() -> None:
    tracks = {"": _("— (no track)"), **TRACK_LABEL}

    @ui.refreshable
    def listing() -> None:
        for f in annotations.all_flags():
            with ui.row().classes("items-center gap-2"):
                name = ui.input(_("Name"), value=f.name).props("dense")
                colour = (
                    ui.color_input(_("Colour"), value=f.colour, preview=True)
                    .props("dense")
                    .classes("w-36")
                )
                track = (
                    ui.select(tracks, value=f.track or "", label=_("Track"))
                    .props("dense")
                    .classes("w-40")
                )
                ui.button(
                    icon="save",
                    on_click=lambda fid=f.id, n=name, c=colour, t=track: (
                        annotations.save_flag(n.value, c.value, t.value, fid),
                        ui.notify(_("Saved")),
                    ),
                ).props("flat round dense")
                ui.button(
                    icon="delete",
                    on_click=lambda fid=f.id: (annotations.delete_flag(fid), listing.refresh()),
                ).props("flat round dense color=negative")

    ui.label(
        _(
            "Flags stick to publications. A flag with a track makes the paper a satellite "
            "category (e.g. “Short CORE A*”) in the distribution."
        )
    ).classes("text-grey")
    listing()
    with ui.row().classes("items-center gap-2 mt-2"):
        name = ui.input(_("New flag")).props("dense")
        colour = (
            ui.color_input(_("Colour"), value="#57606a", preview=True)
            .props("dense")
            .classes("w-36")
        )
        track = ui.select(tracks, value="", label=_("Track")).props("dense").classes("w-40")
        ui.button(
            _("Add"),
            on_click=lambda: (
                (annotations.save_flag(name.value, colour.value, track.value), listing.refresh())
                if name.value
                else None
            ),
        )

    ui.label(_("Tags")).classes("text-lg mt-6")
    from .tags import tags_section

    tags_section()
    author_categories_section()


def author_categories_section() -> None:
    ui.label(_("Co-author categories")).classes("text-lg mt-6")
    ui.label(
        _(
            "Groups of co-authors (e.g. “Intl. collaborators”), highlighted in author lists and "
            "counted in the panel. Put a co-author in a category by clicking their name in a "
            "publication's details; membership is per person."
        )
    ).classes("text-grey")

    @ui.refreshable
    def listing() -> None:
        for c in annotations.author_categories():
            with ui.row().classes("items-center gap-2"):
                name = ui.input(_("Name"), value=c.name).props("dense")
                colour = (
                    ui.color_input(_("Colour"), value=c.colour, preview=True)
                    .props("dense")
                    .classes("w-36")
                )
                ui.button(
                    icon="save",
                    on_click=lambda cid=c.id, n=name, col=colour: (
                        annotations.save_author_category(n.value, col.value, cid),
                        ui.notify(_("Saved")),
                    ),
                ).props("flat round dense")
                ui.button(
                    icon="delete",
                    on_click=lambda cid=c.id: (
                        annotations.delete_author_category(cid),
                        listing.refresh(),
                    ),
                ).props("flat round dense color=negative")

    listing()
    with ui.row().classes("items-center gap-2 mt-2"):
        name = ui.input(_("New category")).props("dense")
        colour = (
            ui.color_input(_("Colour"), value="#0969da", preview=True)
            .props("dense")
            .classes("w-36")
        )
        ui.button(
            _("Add"),
            on_click=lambda: (
                (annotations.save_author_category(name.value, colour.value), listing.refresh())
                if name.value
                else None
            ),
        )


# ---- data ----------------------------------------------------------------------------------


def _reset_automatic_section() -> None:
    ui.label(_("Automatic matching")).classes("text-lg mt-4")
    ui.label(
        _(
            "Clear out everything computed automatically, for all papers: automatic venues and "
            "their variants, which venue each source text belongs to, the papers' automatic "
            "venues and kinds, and the cached ranking matches. Everything set by hand is kept "
            "(venue levels, records, kinds, names, variants and rules; papers' venues, validated "
            "sources, ranks, corrections, flags, tags and notes). Then everything is matched "
            "again."
        )
    ).classes("text-sm text-grey")

    def confirm() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label(_("Clear out the automatic settings of all papers?")).classes("font-medium")
            ui.label(
                _(
                    "Automatic venues are rebuilt from the sources' texts; manual decisions "
                    "are kept. Venues created automatically may get new ids (links to them "
                    "break)."
                )
            ).classes("text-sm")

            def ok() -> None:
                r = venue_match.reset_automatic()
                ui.notify(
                    _(
                        "Automatic settings cleared: {venues} venue(s) dropped, "
                        "{texts} venue text(s) matched again"
                    ).format(venues=r["venues"], texts=r["texts"]),
                    type="positive",
                )
                dlg.close()

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                ui.button(_("Clear out"), on_click=ok).props("color=negative").mark(
                    "reset-automatic-confirm"
                )
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    ui.button(
        _("Clear out automatic settings for all papers"), icon="delete_sweep", on_click=confirm
    ).props("color=negative outline").mark("reset-automatic")

    ui.label(_("Manual decisions")).classes("text-lg mt-4")
    ui.label(
        _(
            "Erase what was set by hand, on the venues and / or on the papers; everything is then "
            "matched automatically again. Flags, tags, notes, hidden papers and manual merges are "
            "kept. "
            "Export the settings first to keep a copy of the venue decisions."
        )
    ).classes("text-sm text-grey")

    def confirm_manual() -> None:
        n = venue_match.manual_counts()
        with ui.dialog() as dlg, ui.card():
            ui.label(_("Clear manual decisions?")).classes("font-medium")
            on_venues = ui.checkbox(
                _(
                    "Venues: {venues} venue(s) with a kind, level, record, search text, "
                    "short name, rules or ISSNs; {variants} variant(s) set by hand"
                ).format(venues=n["venues"], variants=n["variants"]),
                value=True,
            ).mark("clear-manual-venues")
            on_papers = ui.checkbox(
                _(
                    "Papers: {papers} paper(s) with a venue, validated source, rank, kind, "
                    "year, author position or note set by hand"
                ).format(papers=n["papers"]),
                value=True,
            ).mark("clear-manual-papers")
            ui.label(_("This cannot be undone.")).classes("text-sm text-negative")

            def ok() -> None:
                if not (on_venues.value or on_papers.value):
                    return
                r = venue_match.clear_manual(venues=on_venues.value, papers=on_papers.value)
                ui.notify(
                    _(
                        "Manual decisions cleared ({venues} on venues, {papers} on papers); "
                        "everything matched again"
                    ).format(venues=r["venues"], papers=r["papers"]),
                    type="positive",
                )
                dlg.close()

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                ui.button(_("Clear"), on_click=ok).props("color=negative").mark(
                    "clear-manual-confirm"
                )
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    ui.button(_("Clear manual decisions…"), icon="restart_alt", on_click=confirm_manual).props(
        "color=negative outline"
    ).mark("clear-manual")


def _data_dir_section(refresh) -> None:
    ui.label(_("Data directory")).classes("text-lg")
    ui.label(
        _("Holds the database (people, publications, venues, settings) and the stored PDFs.")
    ).classes("text-sm text-gray-600")
    with ui.column().classes("gap-0 text-sm"):
        ui.label(
            _("{path}  (from {origin})").format(path=config.DATA_DIR, origin=config.DATA_DIR_ORIGIN)
        ).classes("font-mono").mark("data-dir")
        ui.label(_("Database: {path}").format(path=config.DB_PATH)).classes(
            "font-mono text-gray-600"
        )
    if config.DATA_DIR_ORIGIN != "the settings" and config.DATA_DIR_ORIGIN != "the default":
        ui.label(
            _("Set by {origin}: a location chosen here only applies when it is not given.").format(
                origin=config.DATA_DIR_ORIGIN
            )
        ).classes("text-sm text-orange-800")
    pending = config.saved_data_dir()
    if pending and pending.resolve() != config.DATA_DIR:
        ui.label(_("From the next start: {path}").format(path=pending)).classes(
            "text-sm text-orange-800"
        )

    with ui.row().classes("items-center gap-2 w-full"):
        path = (
            ui.input(_("New location"), value=str(config.DATA_DIR))
            .classes("grow font-mono")
            .mark("data-dir-input")
        )
        copy = ui.checkbox(_("Copy the current data there"), value=True).mark("data-dir-copy")

    def change() -> None:
        dest = Path(path.value.strip()).expanduser()
        if not path.value.strip() or not dest.is_absolute():
            ui.notify(_("Give an absolute path"), type="warning")
            return
        if not copy.value and not datadir.has_data(dest):
            what = _(
                "From the next start, the app will use an empty database. Restart the app to "
                "switch; the current directory is left as it is."
            )
        elif not copy.value:
            what = _(
                "From the next start, the app will use the database already there. Restart the "
                "app to switch; the current directory is left as it is."
            )
        else:
            what = _(
                "From the next start, the app will use a copy of the current data. Restart the "
                "app to switch; the current directory is left as it is."
            )
        with ui.dialog() as dlg, ui.card():
            ui.label(_("Use {path}?").format(path=dest)).classes("text-lg")
            ui.label(what)
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")

                def ok() -> None:
                    try:
                        datadir.use_data_dir(dest, copy=copy.value)
                    except (OSError, sqlite3.Error) as e:
                        ui.notify(
                            _("Could not use {path}: {error}").format(path=dest, error=e),
                            type="negative",
                        )
                        return
                    ui.notify(_("Restart the app to use {path}").format(path=dest), type="positive")
                    refresh()
                    dlg.close()

                ui.button(_("Use it"), on_click=ok).props("color=primary").mark("data-dir-confirm")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    with ui.row().classes("gap-2"):
        ui.button(_("Change location…"), icon="folder_open", on_click=change).mark(
            "data-dir-change"
        )
        if config.saved_data_dir():

            def reset() -> None:
                config.save_data_dir(None)
                ui.notify(_("From the next start: {path}").format(path=config.DEFAULT_DATA_DIR))
                refresh()

            ui.button(_("Back to the default"), on_click=reset).props("flat")


def _pdfs_section() -> None:
    from . import pdf_viewer

    ui.label(_("Stored PDFs")).classes("text-lg mt-4")

    @ui.refreshable
    def status() -> None:
        u = pdfs.usage()
        text = _("{n} PDFs ({size:.1f} MB) in {path}").format(
            n=u.files, size=u.size / 1e6, path=pdfs.pdf_dir()
        )
        if u.orphans or u.lost:
            text += " · " + _(
                "{orphans} files of papers no longer in the database, "
                "{lost} PDFs whose file was deleted"
            ).format(orphans=len(u.orphans), lost=u.lost)
        ui.label(text).classes("text-sm").mark("pdfs-usage")

    status()

    def clean() -> None:
        files, forgotten = pdfs.cleanup()
        ui.notify(
            _("{files} files deleted, {forgotten} PDFs forgotten").format(
                files=files, forgotten=forgotten
            )
        )
        status.refresh()

    def set_confirm(e) -> None:
        annotations.save_ui_state(pdf_viewer.NO_CONFIRM, not e.value)
        pdf_viewer._no_confirm = not e.value

    with ui.row().classes("items-center gap-3"):
        ui.button(_("Clean up"), icon="cleaning_services", on_click=clean).props("flat").tooltip(
            _(
                "Delete the files of papers no longer in the database, and forget the PDFs "
                "whose file was deleted by hand"
            )
        ).mark("pdfs-cleanup")
        ui.checkbox(
            _("Ask before downloading a PDF"),
            value=not annotations.ui_state(pdf_viewer.NO_CONFIRM, False),
            on_change=set_confirm,
        ).mark("pdfs-confirm")
    ui.label(
        _(
            "Downloaded from the papers' open-access links (or uploaded), to read and annotate "
            "them in the browser. A paper that leaves the sources takes its downloaded PDF with "
            "it; one uploaded or annotated keeps the paper (as missing from the sources)."
        )
    ).classes("text-xs text-grey -mt-2")


def data_tab() -> None:
    @ui.refreshable
    def data_dir_box() -> None:
        _data_dir_section(data_dir_box.refresh)

    data_dir_box()
    _pdfs_section()
    ui.label(_("Ranking datasets")).classes("text-lg mt-4")
    ui.markdown(
        _(
            "{n} ranking records:\n\n"
            "- [SCImago Journal & Country Rank](https://www.scimagojr.com) (SJR, quartiles), "
            "used as SCImago asks, with this credit: downloaded from the project's repository "
            "and checked every week (each update merged into the copy here);\n"
            "- [ICORE conference rankings](https://portal.core.edu.au/conf-ranks/), every edition "
            "since 2008;\n"
            "- JCR impact factors are Clarivate's and never shipped: import your own export below."
        ).format(n=len(service.matcher))
    ).classes("text-sm")
    journals = ui.label().classes("text-sm")
    predatory = ui.label().classes("text-sm")

    def refresh_status() -> None:
        journals.text = (
            _("Scimago journals: read from the source tree ({path}).").format(
                path=datasets.journals_path()
            )
            if datasets.from_source_tree()
            else _("Scimago journals: downloaded {when}.").format(
                when=fmt_dt(datasets.journals_updated_at())
            )
        )
        predatory.text = _(
            "Predatory list (Beall's list, from stop-predatory-journals, MIT licence): "
            "{n} entries, downloaded {when}, refreshed every week."
        ).format(n=len(service.predatory), when=fmt_dt(datasets.predatory_updated_at()))

    refresh_status()

    async def update() -> None:
        ui.notify(_("Downloading the Scimago journals and the predatory list…"))
        try:
            await datasets.refresh_journals(force=True)
            await datasets.refresh_predatory(force=True)
        except Exception as e:
            ui.notify(_("Update failed: {error}").format(error=e), type="negative")
            return
        service.invalidate(data=True, clear_cache=True)
        refresh_status()
        ui.notify(_("Ranking datasets updated"), type="positive")

    ui.button(_("Update now"), icon="cloud_download", on_click=update).mark("predatory-update")

    ui.label(_("Match cache")).classes("text-lg mt-4")

    @ui.refreshable
    def cache() -> None:
        stats = service.cache_stats()
        cache_labels = {"manual": _("manual"), "unranked": _("unranked")}
        for src in (*TOGGLABLE_SOURCES, "manual", "unranked"):
            with ui.row().classes("items-center gap-2"):
                ui.label(cache_labels.get(src, src)).classes("w-32")
                ui.label(str(stats.get(src, 0))).classes("w-16")
                ui.button(
                    _("Clear"), on_click=lambda s=src: (service.clear_cache(s), cache.refresh())
                ).props("flat dense")
        ui.button(_("Clear all"), on_click=lambda: (service.clear_cache(), cache.refresh())).props(
            "flat dense color=negative"
        )

    cache()
    _reset_automatic_section()

    ui.label(_("JCR (Journal Citation Reports) import")).classes("text-lg mt-4")
    ui.label(
        _(
            "JCR data is proprietary: export a CSV from Clarivate and import it here "
            "(journal name, ISSN, impact factor, quartile columns are detected)."
        )
    ).classes("text-sm text-grey")
    jcr_count = ui.label()

    def count() -> None:
        with session_scope() as s:
            n = s.scalar(select(func.count()).select_from(JcrRecord))
            jcr_count.text = ngettext("{n} JCR row", "{n} JCR rows", n).format(n=n)

    count()

    async def upload(e) -> None:
        rows = datasets.jcr_rows_from_csv(await e.file.text())
        if not rows:
            ui.notify(_("No usable rows found (need a journal-name column)"), type="warning")
            return
        with session_scope() as s:
            s.execute(delete(JcrRecord))
            s.add_all(JcrRecord(data=r) for r in rows)
        service.invalidate(data=True, clear_cache=True)
        count()
        ui.notify(
            ngettext("Imported {n} JCR row", "Imported {n} JCR rows", len(rows)).format(
                n=len(rows)
            ),
            type="positive",
        )

    ui.upload(on_upload=upload, auto_upload=True, label=_("JCR CSV")).props('accept=".csv,.txt"')

    ui.label(_("Scimago, past years")).classes("text-lg mt-4")
    ui.label(
        _(
            "A journal gets its quartile of a paper's year: download the years missing from "
            "scimagojr.com (it blocks the app) and drop their CSVs here, the year read from "
            "the file name (“scimagojr 2021.csv”). Also brings back the journals no longer "
            "listed."
        )
    ).classes("text-sm text-grey")

    @ui.refreshable
    def sjr_years() -> None:
        loaded = service.scimago_years
        imported = set(datasets.imported_scimago_years())
        newest = max(loaded, default=datasets.SCIMAGO_FIRST_YEAR)
        with ui.row().classes("items-center gap-1").mark("scimago-years"):
            for y in range(datasets.SCIMAGO_FIRST_YEAR, newest + 1):
                if y in loaded:
                    ui.badge(str(y), color="blue-grey-6" if y in imported else "primary")
                else:
                    ui.link(str(y), datasets.scimago_download_url(y), new_tab=True).classes(
                        "text-xs text-grey"
                    ).tooltip(_("Not loaded: download it"))

    sjr_years()
    scimago_years.uploader(on_done=lambda _y: sjr_years.refresh())


def keys_tab() -> None:
    stored = keys.stored_keys()
    inputs = {}
    for name, (env, label) in keys.KEYS.items():
        inputs[name] = ui.input(
            label,
            value=stored.get(name, ""),
            password=name != "email",
            password_toggle_button=name != "email",
        ).classes("w-[32rem]")
        ui.label(_("(or set the {env} environment variable)").format(env=env)).classes(
            "text-xs text-grey"
        )
    ui.button(
        _("Save"),
        on_click=lambda: (
            keys.save_keys({k: i.value for k, i in inputs.items()}),
            ui.notify(_("Saved"), type="positive"),
        ),
    )


# ---- venue kinds ---------------------------------------------------------------------------


def kinds_tab() -> None:
    st = load_settings()
    ui.label(
        _(
            "Venues are classified in two levels: first their kind (preprint, national or "
            "international conference or journal), then their rank. Ranked venues (CORE, "
            "Scimago, JCR) are international; other venues are classified with the keywords "
            "below. Kinds can be set by hand on a venue or a paper."
        )
    ).classes("text-grey text-sm")

    ui.label(_("Default level per kind")).classes("text-lg mt-2")
    ui.label(
        _(
            "Used for papers of that kind that have no rank (and no manual decision), e.g. "
            "every national conference counts as a given level."
        )
    ).classes("text-sm text-grey")
    levels = ["A*", "A", "B", "C", "Q1", "Q2", "Q3", "Q4"]
    selects = {}
    with ui.grid(columns="auto auto 1fr").classes("items-center gap-x-3"):
        for kind, label in KINDS.items():
            ui.label(label)
            selects[kind] = (
                ui.select(
                    {"": "—", **level_options(levels)},
                    value=st.kind_levels.get(kind, ""),
                    new_value_mode="add-unique",
                )
                .props("dense outlined")
                .classes("w-72")
            )
            level_hint(selects[kind])

    ui.label(_("National / international")).classes("text-lg mt-4")
    with ui.row().classes("w-full gap-4 no-wrap"):
        natl = (
            ui.textarea(
                _("National keywords (one per line)"), value="\n".join(st.national_keywords)
            )
            .classes("w-1/2")
            .props("rows=8")
        )
        intl = (
            ui.textarea(
                _("International keywords (one per line)"),
                value="\n".join(st.international_keywords),
            )
            .classes("w-1/2")
            .props("rows=8")
        )
    ui.label(
        _(
            "Words are matched case-insensitively, acronyms (e.g. TALN) case-sensitively. "
            "National keywords win over international ones."
        )
    ).classes("text-xs text-grey")
    scope = ui.radio(
        {"international": _("international"), "national": _("national")}, value=st.unknown_scope
    ).props("inline")
    ui.label(_("Scope of unranked venues matching no keyword")).classes("text-xs text-grey -mt-2")

    ui.label(_("Ranks over time")).classes("text-lg mt-4")
    edition = (
        ui.radio(
            {
                "publication": _("rank at the paper's publication time"),
                "latest": _("rank in the latest edition / year"),
            },
            value=st.core_edition,
        )
        .props("inline")
        .mark("core-edition")
    )
    ui.label(
        _(
            "At publication time, a paper gets the rank of the CORE edition in force that year "
            "(2008, 2010, 2013, 2014, 2017, 2018, 2020, 2021, 2023, 2026; papers older than the "
            "first edition get its rank), and its journal's Scimago quartile that year (else "
            "the closest year before, else the first one: import the years missing in Data & "
            "cache)."
        )
    ).classes("text-xs text-grey -mt-2")

    def save() -> None:
        new = load_settings()
        new.core_edition = edition.value
        new.kind_levels = {k: v.value for k, v in selects.items() if v.value}
        new.national_keywords = [w.strip() for w in natl.value.splitlines() if w.strip()]
        new.international_keywords = [w.strip() for w in intl.value.splitlines() if w.strip()]
        new.unknown_scope = scope.value
        save_settings(new)
        ui.notify(_("Saved"), type="positive")

    ui.button(_("Save"), icon="save", on_click=save).classes("mt-2")


# ---- import / export -----------------------------------------------------------------------


def io_tab() -> None:
    ui.label(_("Export")).classes("text-lg")
    ui.label(
        _(
            "Matching settings, cleaning rules, corrections, manual levels and flag "
            "definitions. People and publications are never exported."
        )
    ).classes("text-sm text-grey")
    include_jcr = ui.checkbox(_("Include imported JCR rows (check you may share them)"))

    def export() -> None:
        data = settings_io.export_settings(include_jcr=include_jcr.value)
        ui.download.content(
            data.model_dump_json(indent=2).encode(),
            "sci-report-analyzer-settings.json",
            "application/json",
        )

    ui.button(_("Export settings"), icon="download", on_click=export)

    ui.label(_("Import")).classes("text-lg mt-6")
    ui.label(
        _(
            "Replace erases every current matching setting; merge keeps them and lets you "
            "resolve each conflict."
        )
    ).classes("text-sm text-grey")

    async def upload(e) -> None:
        try:
            data = settings_io.parse_file(await e.file.text())
        except (ValueError, json.JSONDecodeError) as err:
            ui.notify(
                _("Not a SciReport Analyzer settings file: {error}").format(error=err),
                type="negative",
                multi_line=True,
            )
            return
        import_dialog(data)

    ui.upload(on_upload=upload, auto_upload=True, label=_("Settings file (.json)")).props(
        'accept=".json"'
    )


def import_dialog(data: settings_io.SettingsFile) -> None:
    conflicts = settings_io.find_conflicts(data)
    choice: dict[str, bool] = {c.id: True for c in conflicts}  # True = take imported
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Import settings")).classes("text-lg")
        ui.label(
            _("{corrections} corrections · {levels} manual levels · {flags} flags").format(
                corrections=len(data.corrections), levels=len(data.levels), flags=len(data.flags)
            )
            + (" · " + _("{n} JCR rows").format(n=len(data.jcr)) if data.jcr else "")
            + (
                " · " + _("exported {when}").format(when=data.exported_at)
                if data.exported_at
                else ""
            )
        )
        mode = ui.radio(
            {
                "merge": _("Merge with current settings"),
                "replace": _("Replace all current matching settings"),
            },
            value="merge",
        ).props("inline")

        conflict_box = ui.column().classes("w-full")

        @ui.refreshable
        def conflict_list() -> None:
            if not conflicts:
                ui.label(_("No conflict: imported entries will simply be added.")).classes(
                    "text-positive"
                )
                return
            ui.label(
                _("{n} conflict(s): choose which value to keep").format(n=len(conflicts))
            ).classes("font-medium")
            with ui.row():
                ui.button(_("Take all imported"), on_click=lambda: _all(True)).props("dense flat")
                ui.button(_("Keep all local"), on_click=lambda: _all(False)).props("dense flat")
            with ui.scroll_area().classes("w-full h-72"):
                for c in conflicts:
                    with ui.row().classes("items-center no-wrap w-full gap-2 border-b py-1"):
                        ui.label(f"{c.kind}: {c.key}").classes("w-1/3 text-sm")
                        ui.toggle(
                            {
                                False: _("local: {value}").format(value=c.local),
                                True: _("imported: {value}").format(value=c.imported),
                            },
                            value=choice[c.id],
                            on_change=lambda e, cid=c.id: choice.__setitem__(cid, e.value),
                        ).props("dense no-caps size=sm")

        def _all(v: bool) -> None:
            for k in choice:
                choice[k] = v
            conflict_list.refresh()

        with conflict_box:
            conflict_list()
        conflict_box.bind_visibility_from(mode, "value", lambda v: v == "merge")

        def apply() -> None:
            take = {k for k, v in choice.items() if v}
            counts = settings_io.import_settings(data, mode.value, take)
            ui.notify(
                _("Imported: {counts}").format(
                    counts=", ".join(f"{v} {k}" for k, v in counts.items())
                ),
                type="positive",
            )
            dlg.close()

        with ui.row().classes("justify-end w-full"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Import"), on_click=apply)
    dlg.open()
