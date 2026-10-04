"""Settings: matching, tracks, tags, data, API keys, import/export."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from nicegui import ui
from sqlalchemy import delete, func, select

from .. import (
    annotations,
    config,
    contribution,
    datadir,
    i18n,
    keys,
    pdfs,
    settings_io,
    source_settings,
    venue_match,
)
from ..db.models import JcrRecord
from ..db.session import session_scope
from ..i18n import N_, _, ngettext
from ..ranking import datasets, detection, tracks
from ..ranking.badge import SOURCE_LABELS, TOGGLABLE_SOURCES
from ..ranking.kinds import KINDS, WORKSHOP_RE, KindEvidence, detect_kind, host_text
from ..ranking.normalize import (
    DEFAULT_NORM_RULES,
    NormRule,
    apply_rules,
    default_rules,
    in_order,
    normalize,
    rule_origin,
)
from ..ranking.service import load_settings, save_settings, service
from ..sources import ADAPTERS
from . import scimago_years, unsaved
from .colours import ColourInput
from .dialogs import actions, confirm, transient_dialog
from .theme import fmt_dt, frame, level_hint, level_options, track_chip

# The settings, in groups: (group, [(tab, label)]).
NAV = (
    (
        N_("Sources"),
        (("sources", N_("Publication sources")), ("keys", N_("API keys"))),
    ),
    (
        N_("Venues"),
        (
            ("matching", N_("Ranking sources")),
            ("rules", N_("Cleaning rules")),
            ("kinds", N_("Venue kinds")),
            ("detection", N_("Detection rules")),
            ("tracks", N_("Tracks")),
        ),
    ),
    (N_("Annotations"), (("tags", N_("Tags & categories")),)),
    (
        N_("Reports"),
        (
            ("contribution", N_("Contribution roles")),
            ("reports", N_("Citation templates")),
        ),
    ),
    (N_("Data"), (("data", N_("Data & cache")), ("io", N_("Import / export")))),
)
PANELS = {
    "sources": lambda: _publication_sources(),
    "keys": lambda: keys_tab(),
    "matching": lambda: matching_tab(),
    "rules": lambda: rules_tab(),
    "kinds": lambda: kinds_tab(),
    "detection": lambda: detection_tab(),
    "tracks": lambda: tracks_tab(),
    "tags": lambda: tags_tab(),
    "contribution": lambda: contribution_tab(),
    "reports": lambda: report_templates_tab(),
    "data": lambda: data_tab(),
    "io": lambda: io_tab(),
}
ui.add_css(
    """
.vr-rule-default { background:#eaf7ec; }
.vr-rule-edited { background:#fff8c5; }
.vr-rule-added { background:#ffe7d1; }
.body--dark .vr-rule-default { background:rgba(46,160,67,.16); }
.body--dark .vr-rule-edited { background:rgba(210,153,34,.18); }
.body--dark .vr-rule-added { background:rgba(219,109,40,.22); }
.vr-settings-nav .q-tab { justify-content:flex-start; min-height:32px; text-transform:none; }
.vr-settings-nav .q-tab__content { align-items:flex-start; }
""",
    shared=True,
)


def register() -> None:
    @ui.page("/settings")
    def settings_page(tab: str = "sources") -> None:
        if tab not in PANELS:
            tab = "sources"
        with frame(_("Settings")):
            ui.label(_("Settings")).classes("text-2xl")
            edits = unsaved.Edits()
            labels = {name: label for _g, entries in NAV for name, label in entries}
            with ui.row().classes("w-full no-wrap items-start gap-4"):
                with ui.column().classes("shrink-0 w-56 gap-0"):
                    with (
                        ui.tabs(value=tab)
                        .props("vertical dense")
                        .classes("vr-settings-nav w-full") as tabs
                    ):
                        for group, entries in NAV:
                            ui.label(_(group)).classes(
                                "text-xs text-grey-7 uppercase font-bold mt-3 mb-1 px-2"
                            )
                            for name, label in entries:
                                edits.tabs[name] = (
                                    ui.tab(name, _(label)).classes("pl-2").mark(f"settings-{name}")
                                )
                    # The screens with unsaved changes, and their Save / Cancel.
                    edits_panel = ui.element("div").classes("w-full")
                with ui.tab_panels(tabs, value=tab).props("vertical").classes("grow min-w-0"):
                    for name, panel in PANELS.items():
                        with ui.tab_panel(name):
                            edits.screen(name, labels[name], panel)
                with edits_panel:
                    edits.panel()


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
                ColourInput(
                    value=r.colour,
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

    def save() -> bool:
        try:
            contribution.save_config(cfg)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return False
        ui.notify(_("Saved (reload a person's page to see it)"), type="positive")
        return True

    save = unsaved.track(lambda: (cfg.roles, cfg.rules, cfg.fallback), save)
    with ui.row().classes("mt-2"):
        ui.button(_("Add a rule"), icon="add", on_click=add_rule).props("flat").mark(
            "contribution-add-rule"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save).mark("contribution-save")


# ---- citation templates --------------------------------------------------------------------


def report_templates_tab() -> None:
    """How a paper is cited in the notes (a folder's own templates are over these)."""
    from .citations import templates_section

    templates_section()


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

    save = unsaved.track(lambda: {n: b.value for n, b in boxes.items()}, save)
    with ui.row().classes("gap-2"):
        unsaved.cancel_button()
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
    ui.label(_("Ranking sources")).classes("text-lg")
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

    save = unsaved.track(lambda: ({k: b.value for k, b in boxes.items()}, slider.value), save)
    with ui.row().classes("gap-2 mt-2"):
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save)


# Where a rule comes from (its card's background, a marker): label, tooltip.
ORIGINS = {
    "default": (N_("default"), N_("A built-in rule, as by default")),
    "edited": (N_("edited"), N_("A built-in rule, changed from its default")),
    "added": (N_("added"), N_("A rule added by hand")),
}
# (A track's card.)
TRACK_ORIGINS = {
    "default": (N_("default"), N_("A built-in track, as by default")),
    "edited": (N_("edited"), N_("A built-in track, changed from its default")),
    "added": (N_("added"), N_("A track added by hand")),
}
_NORM_DEFAULTS = {r.id: r for r in DEFAULT_NORM_RULES}


def _show_origin(card: ui.element, origin: str, origins: dict = ORIGINS) -> None:
    """A rule's card and marker as a default rule's, a changed one's or an added one's."""
    card.classes(remove=" ".join(f"vr-rule-{o}" for o in ORIGINS), add=f"vr-rule-{origin}")
    label, tip = origins[origin]
    ui.badge(_(label), color="grey-8").props("outline").tooltip(_(tip)).mark(f"origin-{origin}")


# The rules' languages, as grouped in Settings: any language (none), then each language.
LANGUAGE_GROUPS: tuple[str | None, ...] = (None, *i18n.LANGUAGES)


def _language_name(lang: str | None) -> str:
    return i18n.LANGUAGES[lang] if lang else _("Any language")


def rules_tab() -> None:
    ui.label(_("Cleaning rules")).classes("text-lg")
    ui.label(
        _(
            "Python regular expressions applied, in order, to the venue texts of the sources "
            "(\\1, \\2… or \\g<name> in the replacement; \\b, \\d, \\w are ASCII). "
            "The cleaned text, lowercased and without accents or punctuation, is the key "
            "matching the venues' variants; it is also what the rankings are searched with. "
            "Which venue a text belongs to is then set on the venues (variants and venue "
            "rules, on the Venues page or from a paper's details). The rules removing words "
            "of a language (e.g. spelled ordinals) are applied first, to every venue text."
        )
    ).classes("text-grey text-sm")
    rules = [r.model_copy() for r in load_settings().norm_rules]
    sources = {k: a.label for k, a in ADAPTERS.items()}
    languages = {"": _("Any language"), **i18n.LANGUAGES}

    def at(r: NormRule) -> int:
        return next(i for i, x in enumerate(rules) if x is r)

    def reset_one(r: NormRule) -> None:
        rules[at(r)] = _NORM_DEFAULTS[r.id].model_copy(deep=True)
        listing.refresh()
        preview()

    def set_language(r: NormRule, lang: str | None) -> None:
        if (lang or None) != r.language:
            r.language = lang or None
            listing.refresh()
            preview()

    def rule_card(r: NormRule, m: str, i: int) -> None:
        card = ui.column().classes("w-full gap-1 border rounded p-2").mark(f"{m}-rule-{i}")

        # Its marker (and background), and its reset when changed from its default.
        @ui.refreshable
        def origin() -> None:
            o = rule_origin(r)
            _show_origin(card, o)
            if o == "edited":
                ui.button(icon="restart_alt", on_click=lambda: reset_one(r)).props(
                    "flat round dense size=sm"
                ).tooltip(_("Reset to the default")).mark(f"{m}-reset-{i}")

        def changed(e=None) -> None:
            origin.refresh()
            preview(e)

        with card:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.checkbox(value=r.enabled, on_change=changed).bind_value(r, "enabled").tooltip(
                    _("enabled")
                ).mark(f"{m}-enabled-{i}")
                ui.input(_("name"), value=r.name, on_change=changed).bind_value(r, "name").props(
                    "dense outlined"
                ).classes("w-56")
                with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                    origin()
                ui.input(_("pattern"), value=r.pattern, on_change=changed).bind_value(
                    r, "pattern"
                ).props("dense outlined").classes("grow font-mono").mark(f"{m}-pattern-{i}")
                ui.icon("arrow_forward")
                ui.input(_("replacement"), value=r.replacement, on_change=changed).bind_value(
                    r, "replacement"
                ).props("dense outlined").classes("w-32 font-mono")
                with ui.column().classes("gap-0"):
                    ui.button(icon="arrow_upward", on_click=lambda: move(r, -1)).props(
                        "flat round dense size=xs"
                    )
                    ui.button(icon="arrow_downward", on_click=lambda: move(r, 1)).props(
                        "flat round dense size=xs"
                    )
                ui.button(
                    icon="delete", on_click=lambda: (rules.pop(at(r)), listing.refresh())
                ).props("flat round dense color=negative")
            with ui.row().classes("items-center gap-2 w-full no-wrap pl-10"):
                ui.select(
                    languages,
                    label=_("language"),
                    value=r.language or "",
                    on_change=lambda e: set_language(r, e.value),
                ).props("dense outlined").classes("w-36").tooltip(
                    _("The language whose words the rule removes (any: a general rule)")
                ).mark(f"{m}-language-{i}")
                ui.checkbox(_("ignore case"), value=r.ignore_case, on_change=changed).bind_value(
                    r, "ignore_case"
                )
                ui.select(
                    sources,
                    multiple=True,
                    label=_("only for"),
                    value=list(r.sources),
                    on_change=changed,
                ).bind_value(r, "sources").props("dense outlined use-chips").classes(
                    "min-w-32"
                ).tooltip(_("Sources whose venue texts the rule applies to (empty: all)"))
                ui.input(_("description"), value=r.description, on_change=changed).bind_value(
                    r, "description"
                ).props("dense outlined").classes("grow")
                if r.example:
                    ex = ui.label().classes("text-xs text-grey font-mono")
                    ex.text = f"“{r.example}” → “{r.apply(r.example).strip()}”"
                if r.compiled() is None:
                    ui.label(_("invalid regex")).classes("text-negative text-xs")

    # By language (the markers: "norm-…" for any language, "norm-fr-…" for French).
    @ui.refreshable
    def listing() -> None:
        for lang in LANGUAGE_GROUPS:
            m = f"norm-{lang}" if lang else "norm"
            with ui.row().classes("items-center gap-2 mt-3"):
                ui.label(_language_name(lang)).classes("text-md font-bold")
                ui.button(
                    _("Add a rule (first)"), icon="add", on_click=lambda lang=lang: add(lang)
                ).props("flat dense").mark(f"{m}-add")
            for i, r in enumerate(x for x in rules if x.language == lang):
                rule_card(r, m, i)

    def move(r: NormRule, d: int) -> None:
        """Before (after) the previous (next) rule of its language."""
        same = [i for i, x in enumerate(rules) if x.language == r.language]
        k = same.index(at(r)) + d
        if 0 <= k < len(same):
            i, j = at(r), same[k]
            rules[i], rules[j] = rules[j], rules[i]
            listing.refresh()
            preview()

    def add(lang: str | None) -> None:
        ids = {r.id for r in rules}
        n = 1 + sum(r.id.startswith("custom") and r.language == lang for r in rules)
        while f"custom{n}{lang or ''}" in ids:
            n += 1
        first = next((i for i, r in enumerate(rules) if r.language == lang), len(rules))
        rules.insert(
            first,
            NormRule(
                id=f"custom{n}{lang or ''}",
                name=_("Custom rule {n}").format(n=n),
                pattern="",
                language=lang,
            ),
        )
        listing.refresh()

    def reset() -> None:
        rules[:] = default_rules()
        listing.refresh()
        preview()

    listing()
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

    def edited() -> list[NormRule]:
        return in_order([r for r in rules if r.pattern], i18n.LANGUAGES)

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
                every = edited()
                for r in every:
                    if r.applies_to(src) and (after := r.apply(v)) != v:
                        ui.label(f"{r.name}: “{after.strip()}”").classes("font-mono text-xs")
                        v = after
                cleaned = apply_rules(text, every, src)
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
        diff = venue_match.key_changes(edited())
        with changes:
            ui.label(
                ngettext(
                    "{n} venue text gets another key", "{n} venue texts get another key", len(diff)
                ).format(n=len(diff))
            ).classes("text-sm")
            for m, new in diff[:30]:
                ui.label(f"[{m.source}] {m.raw}: “{m.key}” → “{new}”").classes("text-xs font-mono")

    test.on_value_change(preview)
    test_source.on_value_change(preview)

    def save() -> bool:
        if not valid():
            return False
        new = load_settings()
        new.norm_rules = edited()
        save_settings(new)
        n = venue_match.refresh()
        ui.notify(
            ngettext(
                "Cleaning rules saved — {n} venue text re-matched",
                "Cleaning rules saved — {n} venue texts re-matched",
                n,
            ).format(n=n),
            type="positive",
        )
        return True

    # (The rules without a pattern are not saved: no change.)
    save = unsaved.track(edited, save)
    with ui.row().classes("gap-2"):
        ui.button(_("Check the effect on the venue texts"), icon="rule", on_click=impact).props(
            "flat"
        ).mark("norm-impact")
        unsaved.cancel_button()
        ui.button(_("Save the rules"), icon="save", on_click=save).mark("norm-save")


# ---- tags ----------------------------------------------------------------------------------


def tags_tab() -> None:
    ui.label(_("Tags")).classes("text-lg")
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
                colour = ColourInput(_("Colour"), value=c.colour).props("dense").classes("w-36")
                ui.button(
                    icon="save",
                    on_click=lambda cid=c.id, n=name, col=colour: (
                        annotations.save_author_category(n.value, col.value, cid),
                        ui.notify(_("Saved")),
                    ),
                ).props("flat round dense")
                ui.button(icon="delete", on_click=lambda c=c: delete(c)).props(
                    "flat round dense color=negative"
                ).mark(f"author-category-delete-{c.id}")

    def delete(c) -> None:
        confirm(
            _("Delete the co-author category “{name}”?").format(name=c.name),
            _("Delete"),
            lambda: (annotations.delete_author_category(c.id), listing.refresh()),
            mark="author-category-delete-ok",
        )

    listing()
    with ui.row().classes("items-center gap-2 mt-2"):
        name = ui.input(_("New category")).props("dense")
        colour = ColourInput(_("Colour"), value="#0969da").props("dense").classes("w-36")
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
            "sources, ranks, tracks, corrections, tags and notes). Then everything is matched "
            "again."
        )
    ).classes("text-sm text-grey")

    def confirm_reset() -> None:
        with transient_dialog() as (dlg, _card):
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

            actions(dlg, _("Clear out"), ok, danger=True, mark="reset-automatic-confirm")

    ui.button(
        _("Clear out automatic settings for all papers"),
        icon="delete_sweep",
        on_click=confirm_reset,
    ).props("color=negative outline").mark("reset-automatic")

    ui.label(_("Manual decisions")).classes("text-lg mt-4")
    ui.label(
        _(
            "Erase what was set by hand, on the venues and / or on the papers; everything is then "
            "matched automatically again. Tags, notes, hidden papers and manual merges are "
            "kept. "
            "Export the settings first to keep a copy of the venue decisions."
        )
    ).classes("text-sm text-grey")

    def confirm_manual() -> None:
        n = venue_match.manual_counts()
        with transient_dialog() as (dlg, _card):
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

            def ok() -> bool | None:
                if not (on_venues.value or on_papers.value):
                    return False
                r = venue_match.clear_manual(venues=on_venues.value, papers=on_papers.value)
                ui.notify(
                    _(
                        "Manual decisions cleared ({venues} on venues, {papers} on papers); "
                        "everything matched again"
                    ).format(venues=r["venues"], papers=r["papers"]),
                    type="positive",
                )

            actions(dlg, _("Clear"), ok, danger=True, mark="clear-manual-confirm")

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

        def ok() -> bool | None:
            try:
                datadir.use_data_dir(dest, copy=copy.value)
            except (OSError, sqlite3.Error) as e:
                ui.notify(
                    _("Could not use {path}: {error}").format(path=dest, error=e),
                    type="negative",
                )
                return False
            ui.notify(_("Restart the app to use {path}").format(path=dest), type="positive")
            refresh()

        confirm(
            what,
            _("Use it"),
            ok,
            danger=False,
            title=_("Use {path}?").format(path=dest),
            mark="data-dir-confirm",
        )

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

    def save() -> None:
        keys.save_keys({k: i.value for k, i in inputs.items()})
        ui.notify(_("Saved"), type="positive")

    save = unsaved.track(lambda: {k: i.value for k, i in inputs.items()}, save)
    with ui.row().classes("gap-2"):
        unsaved.cancel_button()
        ui.button(_("Save"), on_click=save)


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

    save = unsaved.track(
        lambda: (
            edition.value,
            {k: v.value for k, v in selects.items()},
            natl.value,
            intl.value,
            scope.value,
        ),
        save,
    )
    with ui.row().classes("gap-2 mt-2"):
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save)


# ---- detection rules -----------------------------------------------------------------------


def _try_detection(st, venue: str) -> str:
    """What the rules in force detect in a venue text."""
    ev = KindEvidence(venue or None)
    kind = detect_kind(
        None,
        ev,
        national_keywords=st.national_keywords,
        international_keywords=st.international_keywords,
        unknown_scope=st.unknown_scope,
    )
    out = [_("kind: {kind}").format(kind=KINDS.get(kind, kind))]
    if track := tracks.detect(venue):
        out.append(_("track: {track}").format(track=tracks.name(track)))
    if WORKSHOP_RE.search(venue) and (host := host_text(venue)):
        out.append(_("main conference: “{host}”").format(host=host))
    if detection.Rule("joint").search(venue):
        out.append(_("joint conference"))
    return " · ".join(out)


def detection_tab() -> None:
    st = load_settings()
    ui.label(_("Detection rules")).classes("text-lg")
    ui.label(
        _(
            "Python regular expressions classifying the venues when no decision was made by "
            "hand: workshops and their main conference, conference or journal, joint "
            "conferences (those of the tracks: Settings → Tracks). A rule matching words of a "
            "language is part of another (“atelier”, "
            "of the workshop rule): a venue text matching it matches the latter. {rule:<id>} "
            "in a pattern stands for the pattern of the rule with that id; (?-i:…) makes a "
            "part case-sensitive. Each rule shows whether it is a default one, and its default "
            "when changed."
        )
    ).classes("text-grey text-sm")
    rules = {r.id: r.model_copy() for r in st.detection_rules}

    def compiled() -> dict:
        return detection.compile_rules(rules.values())

    # Live preview with the rules as edited (saved or not).
    venue = (
        ui.input(_("Try a venue text"), placeholder="Trustworthy AI @ ACM Multimedia 2024")
        .props("dense outlined clearable debounce=300")
        .classes("w-full")
        .mark("detect-try-venue")
    )
    result = ui.label().classes("font-mono text-sm").mark("detect-result")

    def preview(_e=None) -> None:
        if not venue.value:
            result.text = ""
            return
        with detection.using(rules.values()):
            result.text = _try_detection(st, venue.value or "")

    venue.on_value_change(preview)

    def examples(d: detection.DetectionDefault) -> None:
        rx = compiled()[d.id]
        if rx is None:
            ui.label(_("invalid regex")).classes("text-negative text-xs")
            return
        for ex in d.examples:
            found = rx.search(ex)
            text = f"{'✓' if found else '✗'} “{ex}”"
            if found and found.lastindex:
                text += " → “{}”".format(next((g for g in found.groups() if g), "").strip())
            ui.label(text).classes(
                "text-xs font-mono " + ("text-grey" if found else "text-negative")
            )

    def card(d: detection.DetectionDefault) -> None:
        r = rules[d.id]
        box = ui.column().classes("w-full gap-1 border rounded p-2").mark(f"detect-rule-{d.id}")

        def edited() -> bool:
            return (r.pattern, r.ignore_case) != (d.pattern, d.ignore_case)

        def changed(_e=None) -> None:
            status.refresh()
            shown.refresh()
            preview()

        @ui.refreshable
        def body() -> None:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                with ui.column().classes("gap-1 w-48 shrink-0"):
                    ui.label(_(d.name)).classes("font-bold")
                    with ui.row().classes("items-center gap-1"):
                        ui.badge(_language_name(d.language), color="grey-8").props(
                            "outline"
                        ).tooltip(_("The language of the rule's words"))
                        status()
                ui.input(_("pattern"), value=r.pattern, on_change=changed).bind_value(
                    r, "pattern"
                ).props("dense outlined debounce=300").classes("grow font-mono").mark(
                    f"detect-pattern-{d.id}"
                )
                ui.checkbox(_("ignore case"), value=r.ignore_case, on_change=changed).bind_value(
                    r, "ignore_case"
                )
                ui.button(icon="restart_alt", on_click=reset).props("flat round dense").tooltip(
                    _("Reset to the default")
                ).mark(f"detect-reset-{d.id}")
            shown()

        @ui.refreshable
        def status() -> None:
            _show_origin(box, "edited" if edited() else "default")

        @ui.refreshable
        def shown() -> None:
            with ui.column().classes("gap-0 pl-52 w-full"):
                ui.label(_(d.description)).classes("text-xs text-grey")
                if d.part_of:
                    of = detection.DEFAULTS[d.part_of]
                    name = f"{_(of.name)} · {_language_name(of.language)}"
                    ui.label(_("part of the rule “{name}”").format(name=name)).classes(
                        "text-xs text-grey"
                    )
                if edited():
                    case = " " + _("[ignore case]") if d.ignore_case else ""
                    ui.label(_("default: {pattern}").format(pattern=d.pattern + case)).classes(
                        "text-xs text-grey font-mono break-all"
                    )
                examples(d)

        def reset() -> None:
            r.pattern, r.ignore_case = d.pattern, d.ignore_case
            body.refresh()
            preview()

        with box:
            body()

    # By language, then by what they decide.
    defaults = detection.DEFAULT_DETECTION_RULES
    for lang in LANGUAGE_GROUPS:
        ui.label(_language_name(lang)).classes("text-lg font-bold mt-4")
        for group, label in detection.DETECTION_GROUPS.items():
            mine = [d for d in defaults if (d.language, d.group) == (lang, group)]
            if mine:
                ui.label(label).classes("text-md font-bold mt-2")
            for d in mine:
                card(d)

    def save() -> bool:
        bad = [_(detection.DEFAULTS[k].name) for k, rx in compiled().items() if rx is None]
        if bad:
            ui.notify(_("Invalid rule: {names}").format(names=", ".join(bad)), type="negative")
            return False
        new = load_settings()
        new.detection_rules = list(rules.values())
        save_settings(new)
        ui.notify(_("Detection rules saved"), type="positive")
        return True

    save = unsaved.track(lambda: list(rules.values()), save)
    with ui.row().classes("gap-2 mt-2"):
        unsaved.cancel_button()
        ui.button(_("Save the rules"), icon="save", on_click=save).mark("detect-save")


# ---- tracks --------------------------------------------------------------------------------

# Colours offered to a new track (in turn).
NEW_TRACK_COLOURS = ("#bf3989", "#1b7c83", "#bc4c00", "#4d2d8f", "#57606a")


def tracks_tab() -> None:
    ui.label(_("Tracks")).classes("text-lg")
    ui.label(
        _(
            "The satellite tracks of the conferences (Findings, tutorials, demos, short "
            "papers…): a paper of a track is counted apart (e.g. “Short CORE A*”, striped in "
            "the track's colour). A venue text is of a track when one of its rules (Python "
            "regular expressions, each general or of a language) matches it; the tracks are "
            "tried in their order, Findings last. A variant, a venue rule or a paper (in its "
            "details) can also be set to a track by hand. Each track and rule shows whether "
            "it is a default one, changed from its default or added."
        )
    ).classes("text-grey text-sm")
    defs = [t.model_copy(deep=True) for t in load_settings().tracks]
    saved_ids = {t.id for t in defs}
    languages = {"": _("Any language"), **i18n.LANGUAGES}

    def at(t: tracks.Track) -> int:
        return next(i for i, x in enumerate(defs) if x is t)

    # Live preview with the tracks as edited (saved or not).
    venue = (
        ui.input(_("Try a venue text"), placeholder="ACL 2023 (System Demonstrations)")
        .props("dense outlined clearable debounce=300")
        .classes("w-full")
        .mark("track-try")
    )
    result = ui.row().classes("items-center gap-2").mark("track-result")

    def preview(_e=None) -> None:
        result.clear()
        if not venue.value:
            return
        with tracks.using(defs), result:
            if found := tracks.detect(venue.value):
                track_chip(found)
            else:
                ui.label(_("no track: the main conference")).classes("text-sm text-grey")

    venue.on_value_change(preview)

    def reset_rule(r: tracks.TrackRule) -> None:
        d = tracks.DEFAULT_RULES[r.id]
        r.pattern, r.ignore_case, r.language = d.pattern, d.ignore_case, d.language
        listing.refresh()
        preview()

    def rule_row(t: tracks.Track, r: tracks.TrackRule, track_changed: Callable[[], None]) -> None:
        """A rule of ``t``; ``track_changed`` refreshes the track's marker."""
        row = ui.column().classes("w-full gap-0 border rounded p-1").mark(f"track-rule-{r.id}")

        @ui.refreshable
        def status() -> None:
            o = tracks.rule_origin(r)
            _show_origin(row, o)
            if o == "edited":
                ui.button(icon="restart_alt", on_click=lambda: reset_rule(r)).props(
                    "flat round dense size=sm"
                ).tooltip(_("Reset to the default")).mark(f"track-rule-reset-{r.id}")

        @ui.refreshable
        def shown() -> None:
            rx = r.compiled()
            if r.pattern and rx is None:
                ui.label(_("invalid regex")).classes("text-negative text-xs")
                return
            for ex in r.examples:
                found = rx.search(ex) if rx else None
                ui.label(f"{'✓' if found else '✗'} “{ex}”").classes(
                    "text-xs font-mono " + ("text-grey" if found else "text-negative")
                )
            if (d := tracks.DEFAULT_RULES.get(r.id)) and tracks.rule_origin(r) == "edited":
                case = " " + _("[ignore case]") if d.ignore_case else ""
                ui.label(_("default: {pattern}").format(pattern=d.pattern + case)).classes(
                    "text-xs text-grey font-mono break-all"
                )

        def changed(_e=None) -> None:
            status.refresh()
            shown.refresh()
            track_changed()
            preview()

        def set_language(e) -> None:
            r.language = e.value or None
            changed()

        with row:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                if r.id in tracks.DEFAULT_RULES:
                    ui.badge(_language_name(r.language), color="grey-8").props("outline").classes(
                        "shrink-0"
                    ).tooltip(_("The language of the rule's words"))
                else:
                    ui.select(
                        languages,
                        label=_("language"),
                        value=r.language or "",
                        on_change=set_language,
                    ).props("dense outlined").classes("w-36").tooltip(
                        _("The language of the rule's words (any: a general rule)")
                    ).mark(f"track-rule-language-{r.id}")
                with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                    status()
                ui.input(_("pattern"), value=r.pattern, on_change=changed).bind_value(
                    r, "pattern"
                ).props("dense outlined debounce=300").classes("grow font-mono").mark(
                    f"track-pattern-{r.id}"
                )
                ui.checkbox(_("ignore case"), value=r.ignore_case, on_change=changed).bind_value(
                    r, "ignore_case"
                )
                if r.id not in tracks.DEFAULT_RULES:
                    ui.button(
                        icon="delete", on_click=lambda: (t.rules.remove(r), listing.refresh())
                    ).props("flat round dense color=negative").tooltip(_("Delete the rule"))
            with ui.column().classes("gap-0 pl-2"):
                shown()

    def add_rule(t: tracks.Track) -> None:
        ids = {r.id for x in defs for r in x.rules}
        n = 1
        while f"{t.id}_{n}" in ids:
            n += 1
        t.rules.append(tracks.TrackRule(id=f"{t.id}_{n}", pattern=""))
        listing.refresh()

    def reset_name_rule(r: tracks.NameRule) -> None:
        d = tracks.DEFAULT_NAME_RULES[r.id]
        r.pattern, r.replacement, r.ignore_case = d.pattern, d.replacement, d.ignore_case
        listing.refresh()

    def name_rule_row(
        t: tracks.Track, r: tracks.NameRule, track_changed: Callable[[], None]
    ) -> None:
        """A name rule of ``t`` (its examples shown with the name the track's rules give)."""
        row = ui.column().classes("w-full gap-0 border rounded p-1").mark(f"track-name-rule-{r.id}")

        @ui.refreshable
        def status() -> None:
            o = tracks.name_rule_origin(r)
            _show_origin(row, o)
            if o == "edited":
                ui.button(icon="restart_alt", on_click=lambda: reset_name_rule(r)).props(
                    "flat round dense size=sm"
                ).tooltip(_("Reset to the default")).mark(f"track-name-rule-reset-{r.id}")

        @ui.refreshable
        def shown() -> None:
            if r.pattern and r.compiled() is None:
                ui.label(_("invalid regex")).classes("text-negative text-xs")
                return
            for ex in r.examples:
                found = tracks.conference_name(t.id, ex, t.name_rules)
                ui.label(f"“{ex}” → “{found or ex}”").classes(
                    "text-xs font-mono " + ("text-grey" if found else "text-negative")
                )
            if (d := tracks.DEFAULT_NAME_RULES.get(r.id)) and tracks.name_rule_origin(
                r
            ) == "edited":
                case = " " + _("[ignore case]") if d.ignore_case else ""
                ui.label(
                    _("default: {pattern}").format(pattern=f"{d.pattern} → “{d.replacement}”{case}")
                ).classes("text-xs text-grey font-mono break-all")

        def changed(_e=None) -> None:
            status.refresh()
            shown.refresh()
            track_changed()

        with row:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                    status()
                ui.input(_("pattern"), value=r.pattern, on_change=changed).bind_value(
                    r, "pattern"
                ).props("dense outlined debounce=300").classes("grow font-mono").mark(
                    f"track-name-pattern-{r.id}"
                )
                ui.icon("arrow_forward")
                ui.input(_("replacement"), value=r.replacement, on_change=changed).bind_value(
                    r, "replacement"
                ).props("dense outlined debounce=300").classes("w-32 font-mono").mark(
                    f"track-name-replacement-{r.id}"
                )
                ui.checkbox(_("ignore case"), value=r.ignore_case, on_change=changed).bind_value(
                    r, "ignore_case"
                )
                if r.id not in tracks.DEFAULT_NAME_RULES:
                    ui.button(
                        icon="delete",
                        on_click=lambda: (t.name_rules.remove(r), listing.refresh()),
                    ).props("flat round dense color=negative").tooltip(_("Delete the rule"))
            with ui.column().classes("gap-0 pl-2"):
                shown()

    def add_name_rule(t: tracks.Track) -> None:
        ids = {r.id for x in defs for r in x.name_rules}
        n = 1
        while f"{t.id}_name_{n}" in ids:
            n += 1
        t.name_rules.append(tracks.NameRule(id=f"{t.id}_name_{n}", pattern=""))
        listing.refresh()

    def reset_track(t: tracks.Track) -> None:
        defs[at(t)] = tracks.DEFAULTS[t.id].model_copy(deep=True)
        listing.refresh()
        preview()

    def move(t: tracks.Track, d: int) -> None:
        i, j = at(t), at(t) + d
        if 0 <= j < len(defs):
            defs[i], defs[j] = defs[j], defs[i]
            listing.refresh()

    def remove(t: tracks.Track) -> None:
        defs.pop(at(t))
        listing.refresh()
        preview()

    def card(t: tracks.Track) -> None:
        box = ui.column().classes("w-full gap-1 border rounded p-2").mark(f"track-{t.id}")

        @ui.refreshable
        def status() -> None:
            o = tracks.origin(t)
            _show_origin(box, o, TRACK_ORIGINS)
            if o == "edited":
                ui.button(icon="restart_alt", on_click=lambda: reset_track(t)).props(
                    "flat round dense size=sm"
                ).tooltip(_("Reset the track to its default")).mark(f"track-reset-{t.id}")

        def changed(_e=None) -> None:
            status.refresh()
            chip.refresh()
            preview()

        @ui.refreshable
        def chip() -> None:
            with tracks.using(defs):
                track_chip(t.id)

        with box:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                with ui.row().classes("items-center gap-1 w-40 shrink-0"):
                    chip()
                    ui.label(t.id).classes("text-xs text-grey font-mono").tooltip(
                        _("The track's identifier (in the exported settings)")
                    )
                for lang, lang_name in i18n.LANGUAGES.items():
                    t.names.setdefault(lang, "")
                    ui.input(
                        _("name ({language})").format(language=lang_name),
                        value=t.names[lang],
                        on_change=changed,
                    ).bind_value(t.names, lang).props("dense outlined").classes("w-40").mark(
                        f"track-name-{t.id}-{lang}"
                    )
                ColourInput(_("Colour"), value=t.colour, on_change=changed).bind_value(
                    t, "colour"
                ).props("dense").classes("w-36").mark(f"track-colour-{t.id}")
                with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                    status()
                ui.space()
                with ui.column().classes("gap-0"):
                    ui.button(icon="arrow_upward", on_click=lambda: move(t, -1)).props(
                        "flat round dense size=xs"
                    ).mark(f"track-up-{t.id}")
                    ui.button(icon="arrow_downward", on_click=lambda: move(t, 1)).props(
                        "flat round dense size=xs"
                    ).mark(f"track-down-{t.id}")
                if t.id not in tracks.DEFAULTS:
                    ui.button(icon="delete", on_click=lambda: remove(t)).props(
                        "flat round dense color=negative"
                    ).tooltip(_("Delete the track")).mark(f"track-delete-{t.id}")
            if t.id == tracks.FINDINGS_ID:
                ui.label(
                    _(
                        "A Findings volume is ranked as its main conference; it is detected "
                        "after the other tracks."
                    )
                ).classes("text-xs text-grey")
            # Its rules, by language (any language first).
            order = list(LANGUAGE_GROUPS)
            for r in sorted(t.rules, key=lambda r: order.index(r.language)):
                rule_row(t, r, status.refresh)
            ui.button(_("Add a rule"), icon="add", on_click=lambda: add_rule(t)).props(
                "flat dense"
            ).mark(f"track-add-rule-{t.id}")
            # Its name rules (in their order).
            ui.label(_("Name of the conference")).classes("text-sm font-bold mt-1")
            ui.label(
                _(
                    "A venue marked as this track (Venues → “Mark as a track”) is renamed to "
                    "its conference's name: the one these regexes give from its name, applied "
                    "in turn (\\1, \\2… or \\g<name> in the replacement)."
                )
            ).classes("text-xs text-grey")
            for r in t.name_rules:
                name_rule_row(t, r, status.refresh)
            ui.button(_("Add a name rule"), icon="add", on_click=lambda: add_name_rule(t)).props(
                "flat dense"
            ).mark(f"track-add-name-rule-{t.id}")

    @ui.refreshable
    def listing() -> None:
        for t in defs:
            card(t)

    listing()

    def new_id(label: str) -> str:
        """A new track's id: unique, also among those deleted but not saved yet (their
        papers still have them)."""
        return tracks.new_id(label, {*saved_ids, *(t.id for t in defs)})

    def add_track() -> None:
        if not (label := (new_name.value or "").strip()):
            return
        tid = new_id(label)
        n = sum(t.id not in tracks.DEFAULTS for t in defs)
        names = {lang: "" for lang in i18n.LANGUAGES} | {"en": label}
        colour = NEW_TRACK_COLOURS[n % len(NEW_TRACK_COLOURS)]
        defs.append(tracks.Track(id=tid, names=names, colour=colour))
        new_name.value = ""
        listing.refresh()

    def show_id(e) -> None:
        label = (e.value or "").strip()
        id_hint.text = (
            _("Its identifier: {id} (it cannot be changed later)").format(id=new_id(label))
            if label
            else ""
        )

    with ui.row().classes("items-center gap-2 mt-2"):
        new_name = (
            ui.input(_("New track (its English name)"), on_change=show_id)
            .props("dense outlined")
            .mark("track-new-name")
        )
        ui.button(_("Add a track"), icon="add", on_click=add_track).props("flat").mark("track-add")
        id_hint = ui.label().classes("text-xs text-orange-9").mark("track-new-id")

    def reset() -> None:
        defs[:] = tracks.default_tracks() + [t for t in defs if t.id not in tracks.DEFAULTS]
        listing.refresh()
        preview()

    def save() -> bool:
        bad = [
            t.name()
            for t in defs
            for r in (*t.rules, *t.name_rules)
            if r.pattern and r.compiled() is None
        ]
        if bad:
            ui.notify(_("Invalid rule: {names}").format(names=", ".join(bad)), type="negative")
            return False
        new = load_settings()
        new.tracks = [t.model_copy(deep=True) for t in defs]
        save_settings(new)
        # The tracks deleted: no paper, variant or venue rule is of them any more.
        gone = saved_ids - {t.id for t in defs}
        annotations.forget_tracks(gone)
        saved_ids.clear()
        saved_ids.update(t.id for t in defs)
        ui.notify(_("Tracks saved"), type="positive")
        return True

    save = unsaved.track(lambda: defs, save)
    with ui.row().classes("gap-2 mt-2"):
        ui.button(_("Reset the built-in tracks"), icon="restart_alt", on_click=reset).props(
            "flat"
        ).mark("tracks-reset")
        unsaved.cancel_button()
        ui.button(_("Save the tracks"), icon="save", on_click=save).mark("tracks-save")


# ---- import / export -----------------------------------------------------------------------


def io_tab() -> None:
    ui.label(_("Export")).classes("text-lg")
    ui.label(
        _(
            "Matching settings (cleaning and detection rules, tracks…) and the venues' manual "
            "decisions. People and publications are never exported."
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


def _import_tracks(
    mapping: settings_io.TrackMapping, mode: str, choice: dict[str, bool], remove: set[str]
) -> None:
    """How the file's tracks map to the local ones (by id); the local ones it lacks are
    kept or removed (``remove``)."""
    ui.label(_("Tracks")).classes("font-medium")

    def chip(t: tracks.Track) -> None:
        with tracks.using([t]):  # (as defined in the file, or here)
            track_chip(t.id)

    def line(t: tracks.Track, what: str) -> None:
        with ui.row().classes("items-center gap-2 no-wrap").mark(f"import-track-{t.id}"):
            chip(t)
            ui.label(what).classes("text-sm text-grey")

    for t in mapping.added:
        line(t, _("added (from the file)"))
    for local, imported in mapping.both:
        if not mapping.differ(local, imported):
            line(local, _("in both, the same"))
        elif mode == "replace" or choice.get(f"matching:tracks.{local.id}"):
            line(imported, _("in both: the file's"))
        else:
            line(local, _("in both: the local one"))
    for t in mapping.local:
        with ui.row().classes("items-center gap-2 no-wrap").mark(f"import-track-{t.id}"):
            chip(t)
            ui.label(_("not in the file (added here)")).classes("text-sm text-grey")
            ui.toggle(
                {False: _("keep it"), True: _("remove it")},
                value=t.id in remove,
                on_change=lambda e, tid=t.id: remove.add(tid) if e.value else remove.discard(tid),
            ).props("dense no-caps size=sm").mark(f"import-track-remove-{t.id}")
    if mapping.local:
        ui.label(
            _("A track removed is no paper's, variant's or venue rule's track any more.")
        ).classes("text-xs text-grey")


def import_dialog(data: settings_io.SettingsFile) -> None:
    conflicts = settings_io.find_conflicts(data)
    choice: dict[str, bool] = {c.id: True for c in conflicts}  # True = take imported
    mapping = settings_io.track_mapping(data)
    remove: set[str] = set()  # the local tracks the file lacks, removed
    with transient_dialog(_("Import settings"), width="w-full max-w-3xl") as (dlg, _card):
        ui.label(
            _("{venues} venues · {tracks} tracks").format(
                venues=len(data.venues), tracks=len(data.matching.tracks)
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

        @ui.refreshable
        def tracks_view() -> None:
            _import_tracks(mapping, mode.value, choice, remove)

        with ui.column().classes("w-full gap-1").mark("import-tracks"):
            tracks_view()
        mode.on_value_change(tracks_view.refresh)
        conflict_box = ui.column().classes("w-full")

        @ui.refreshable
        def conflict_list() -> None:
            if not conflicts:
                ui.label(_("No conflict: imported entries will simply be added.")).classes(
                    "text-positive"
                )
                return
            ui.label(
                ngettext(
                    "{n} conflict: choose which value to keep",
                    "{n} conflicts: choose which value to keep",
                    len(conflicts),
                ).format(n=len(conflicts))
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
                            on_change=lambda e, cid=c.id: choose(cid, e.value),
                        ).props("dense no-caps size=sm")

        def choose(cid: str, v: bool) -> None:
            choice[cid] = v
            tracks_view.refresh()

        def _all(v: bool) -> None:
            for k in choice:
                choice[k] = v
            conflict_list.refresh()
            tracks_view.refresh()

        with conflict_box:
            conflict_list()
        conflict_box.bind_visibility_from(mode, "value", lambda v: v == "merge")

        def apply() -> None:
            take = {k for k, v in choice.items() if v}
            counts = settings_io.import_settings(data, mode.value, take, remove)
            ui.notify(
                _("Imported: {counts}").format(
                    counts=", ".join(f"{v} {k}" for k, v in counts.items())
                ),
                type="positive",
            )

        actions(dlg, _("Import"), apply, mark="import-apply")
