"""The settings of folders (named; a folder uses those it chose, else the default ones): their
list in the Parameters (created, copied, renamed, made the default, deleted), their editor
in sections (their categories, report templates and language; the excerpts'
markers and levels; the primary source, the star lists and citations; the models), and the
choice of a folder's."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from nicegui import ui

from .. import folders, i18n, llm, report_templates, reports, source_settings
from ..i18n import N_, _, ngettext
from ..sources import ADAPTERS
from .categories_editor import HELP as CATEGORIES_HELP
from .categories_editor import categories_editor
from .dialogs import actions, confirm, transient_dialog

TAB = "folders"  # (their tab in the Parameters)
URL = f"/settings?tab={TAB}"


INFLUENCE_TIP = N_(
    "In [[excerpts]] (and the copied excerpts), the excerpts flagged “rayonnement”; their "
    "section where [[influence]] is in the report, else at the end of [[excerpts]]"
)
INFLUENCE_MODES = {
    "copy": N_("Also in the Rayonnement section"),
    "move": N_("Only in the Rayonnement section, by category"),
    "mark": N_("Only in their categories, marked"),
}


def markers_section(folder_id: int) -> Callable[[], dict[str, reports.Marker]]:
    """The tags of an excerpt's markers in the reports (blank: the default, in their
    language), and whether the excerpts marked are first in their categories; returns them."""
    from .viewer_side import MARKER_ICONS

    current = reports.markers(folder_id)
    fields = {}
    with ui.row().classes("items-center gap-4"):
        for key, m in current.items():
            label, _tip, icon, colour = MARKER_ICONS[key]
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon(icon, size="xs", color=colour)
                tag = (
                    ui.input(_(label), value=m.tag)
                    .props('dense prefix="#"')
                    .classes("w-32")
                    .tooltip(_("Its tag in the reports (e.g. turned into an icon when copied)"))
                    .mark(f"folder-marker-tag-{key}")
                )
                first = (
                    ui.checkbox(_("First"), value=m.first)
                    .tooltip(
                        _(
                            "An excerpt marked moves first in its category (unmarked: "
                            "after those marked)"
                        )
                    )
                    .mark(f"folder-marker-first-{key}")
                )
                fields[key] = (tag, first)
    return lambda: {
        k: reports.Marker((t.value or "").strip("# "), bool(f.value))
        for k, (t, f) in fields.items()
    }


def models_section(folder_id: int) -> Callable[[], tuple[str, str]]:
    """The models the folder uses instead of the default ones (Settings → Local LLM): the LLM
    and the embedding model (blank: the default; those of the servers proposed); returns
    their values."""
    st = llm.settings()
    model, embed = llm.folder_models(folder_id)
    with ui.row().classes("w-full gap-2 no-wrap"):
        inputs = [
            ui.select(
                [value] if value else [],
                value=value or None,
                label=label,
                with_input=True,
                new_value_mode="add-unique",
                clearable=True,
            )
            .classes("grow")
            .tooltip(tip)
            .mark(mark)
            for value, label, tip, mark in (
                (
                    model,
                    _("LLM (default: {model})").format(model=st.model or _("none")),
                    _("For the features using the local LLM"),
                    "folder-llm-model",
                ),
                (
                    embed,
                    _("Embedding model (default: {model})").format(
                        model=st.embed_model or _("none")
                    ),
                    _("E.g. to suggest an excerpt's category (trained again if changed)"),
                    "folder-embed-model",
                ),
            )
        ]

    async def load() -> None:
        for select, listing in (
            (inputs[0], llm.list_models(st.url)),
            (
                inputs[1],
                llm.list_embed_models(
                    st.embed_api, st.embed_url if st.embed_api == "openai" else st.url
                ),
            ),
        ):
            try:
                found = await listing
            except Exception:
                continue  # (the server is not running: typed)
            value = select.value
            select.set_options(sorted({*found, *([value] if value else [])}), value=value)

    if st.enabled:
        ui.timer(0.1, load, once=True)
    return lambda: (inputs[0].value or "", inputs[1].value or "")


def levels_section(folder_id: int) -> Callable[[], tuple[list[reports.Level], str | None]]:
    """The levels an excerpt can have (e.g. local, national, international), in order: their
    tags in the reports (blank: the default, in their language; a new one: added below,
    removed with its button), and the default level of the excerpts without one; returns
    them."""
    rows = [{"key": lv.key, "tag": lv.tag} for lv in reports.levels(folder_id)]
    state = {"default": reports.default_level(folder_id) or ""}
    fields: dict[str, ui.select] = {}

    def options() -> dict[str, str]:
        return {
            "": _("No level"),
            **{r["key"] or r["tag"]: f"#{r['tag']}" for r in rows if (r["key"] or r["tag"])},
        }

    @ui.refreshable
    def listing() -> None:
        with ui.row().classes("items-center gap-2"):
            ui.label(_("Levels")).classes("text-sm").tooltip(
                _(
                    "An excerpt's level (e.g. national; none: the default one), written as "
                    "a #tag in the reports (e.g. turned into a text when copied)"
                )
            )
            for i, r in enumerate(rows):
                with ui.row().classes("items-center gap-0 no-wrap"):
                    ui.input(value=r["tag"]).props('dense prefix="#"').classes("w-28").on(
                        "blur", renamed
                    ).bind_value(r, "tag").mark(f"folder-level-tag-{i}")
                    ui.button(icon="close", on_click=lambda i=i: drop(i)).props(
                        "flat dense round size=xs color=grey-7"
                    ).tooltip(_("Remove this level")).mark(f"folder-level-remove-{i}")
            ui.button(icon="add", on_click=add).props("flat dense round size=sm").tooltip(
                _("Add a level")
            ).mark("folder-level-add")
            fields["default"] = (
                ui.select(options(), label=_("Default level"))
                .bind_value(state, "default")
                .props("dense")
                .classes("w-40")
                .tooltip(_("That of the excerpts without one"))
                .mark("folder-level-default")
            )

    def renamed() -> None:
        """(the default's options: the tags as typed; a new level's, its tag)"""
        shown, default = options(), fields["default"]
        default.set_options(shown, value=default.value if default.value in shown else "")

    def add() -> None:
        rows.append({"key": "", "tag": ""})
        listing.refresh()

    def drop(i: int) -> None:
        if rows[i]["key"] and state["default"] == rows[i]["key"]:
            state["default"] = ""
        del rows[i]
        listing.refresh()

    listing()

    def values() -> tuple[list[reports.Level], str | None]:
        kept = [r for r in rows if (r["tag"] or "").strip("# ") or r["key"]]
        return (
            [reports.Level(r["key"], (r["tag"] or "").strip("# ")) for r in kept],
            state["default"] or None,
        )

    return values


# The sections of the editor of some settings: (key, icon, title, what they are for).
SECTIONS = (
    ("categories", "category", N_("Categories"), CATEGORIES_HELP),
    (
        "report",
        "article",
        N_("Report"),
        N_(
            "The templates a report can start from (their blocks name the categories), and "
            "the language of what is generated."
        ),
    ),
    (
        "excerpts",
        "format_quote",
        N_("Excerpts"),
        N_(
            "How the excerpts are written in the reports: their markers and levels (as "
            "#tags), and where those flagged “rayonnement” go."
        ),
    ),
    (
        "citations",
        "format_list_numbered",
        N_("Citations & sources"),
        N_(
            "Which papers count (the primary source), the star lists, and how the notes "
            "number and cite the papers."
        ),
    ),
    (
        "models",
        "smart_toy",
        N_("Models (local AI)"),
        N_(
            "The local AI models used for these folders instead of the default ones (Settings "
            "→ Local LLM); blank: the default."
        ),
    ),
)


def subtitle(text: str) -> ui.label:
    """The title of a group of fields within a section."""
    return ui.label(text).classes("text-sm font-medium text-primary mt-2")


def report_section(ref: folders.SettingsRef) -> Callable[[], bool | None]:
    """The report templates; returns their save (False: refused, said)."""
    from .report_templates import HELP, templates_editor

    items = report_templates.load(ref)
    subtitle(_("Report templates"))
    ui.markdown(_(HELP)).classes("text-sm text-grey")
    templates_editor(items)

    def save() -> bool | None:
        try:
            report_templates.save(ref, items)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return False
        return None

    return save


def settings_fields(settings_id: int, section: str = "categories") -> Callable[[], bool | None]:
    """The editors of some settings (changed for every folder using them), in sections (see
    SECTIONS, in tabs atop; ``section``: the one shown first): their categories, report
    templates and language; the excerpts' markers and levels, where those flagged
    "rayonnement" go; the primary source, the star lists and citations; the models. Returns
    their save (False: refused, said)."""
    from .citations import folder_citations_section

    ref = folders.SettingsRef(settings_id)
    users = folders.sharing(ref)
    ui.label(
        _("Used by {folders}: changed for them too").format(folders=", ".join(users))
        if users
        else _("Used by no folder")
    ).classes("text-sm text-primary" if users else "text-sm text-grey").mark("settings-users")
    with (
        ui.tabs(value=section)
        .props("dense no-caps inline-label align=left")
        .classes("w-full") as tabs
    ):
        for key, icon, title, _help in SECTIONS:
            ui.tab(key, _(title), icon=icon).mark(f"settings-section-{key}")
    panels = ui.tab_panels(tabs, value=section).classes("w-full")

    @contextmanager
    def panel(key: str) -> Iterator[None]:
        """A section's panel, under its title and what it is for."""
        icon, title, help_text = next(x[1:] for x in SECTIONS if x[0] == key)
        with panels, ui.tab_panel(key).classes("p-0 gap-2").mark(f"settings-panel-{key}"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon(icon, color="primary")
                ui.label(_(title)).classes("text-base font-medium text-primary")
            ui.label(_(help_text)).classes("text-sm text-grey")
            yield

    with panel("categories"):
        categories_editor(ref)
        ui.label(_("Changed at once (not on Save)")).classes("text-xs text-grey")
    with panel("report"):
        report_save = report_section(ref)
        subtitle(_("Language"))
        lang = (
            ui.select(
                {"": _("That of the interface"), **i18n.LANGUAGES},
                value=reports.language(ref) or "",
                label=_("Language"),
            )
            .props("dense")
            .classes("w-64")
            .tooltip(_("Of what is generated: references, excerpts, summary…"))
            .mark("folder-language")
        )
    with panel("excerpts"):
        subtitle(_("Markers"))
        marker_fields = markers_section(ref)
        subtitle(_("Levels"))
        level_fields = levels_section(ref)
        subtitle(_("Rayonnement"))
        influence = (
            ui.select(
                {k: _(v) for k, v in INFLUENCE_MODES.items()},
                value=reports.influence_mode(ref),
                label=_("Rayonnement excerpts"),
            )
            .props("dense")
            .classes("w-96")
            .mark("folder-influence-mode")
        )
        ui.markdown(_(INFLUENCE_TIP)).classes("text-sm text-grey")
    with panel("citations"):
        subtitle(_("Primary source"))
        default = source_settings.default_primary()
        primary = (
            ui.select(
                {
                    None: _("Default ({source})").format(
                        source=ADAPTERS[default].label if default in ADAPTERS else _("none")
                    ),
                    source_settings.NO_PRIMARY: _("None"),
                    **{
                        n: a.label
                        for n, a in ADAPTERS.items()
                        if a.linkable and a.provides_publications
                    },
                },
                value=folders.primary_source(ref),
                label=_("Primary source"),
            )
            .props("dense")
            .classes("w-96")
            .mark("folder-primary")
        )
        ui.label(_("Papers this source doesn't list for a person are not counted")).classes(
            "text-sm text-grey"
        )
        citations_save = folder_citations_section(ref)
    with panel("models"):
        model_fields = models_section(ref)

    def save() -> bool | None:
        if report_save() is False:
            tabs.set_value("report")
            return False
        if citations_save() is False:
            tabs.set_value("citations")
            return False
        reports.save_influence_mode(ref, influence.value)
        reports.save_markers(ref, marker_fields())
        reports.save_levels(ref, *level_fields())
        reports.save_language(ref, lang.value or None)
        llm.save_folder_models(ref, *model_fields())
        folders.set_primary_source(ref, primary.value)
        return None

    return save


def settings_dialog(
    settings_id: int, changed: Callable[[], None] | None = None, section: str = "categories"
) -> None:
    """Edit some settings (see settings_fields; ``section``: the one shown first);
    ``changed``: once saved."""
    name = next((x.name for x in folders.named_settings() if x.id == settings_id), "")
    with transient_dialog(_("Settings “{name}”").format(name=name), width="w-full max-w-5xl") as (
        dlg,
        card,
    ):
        card.style("max-width: min(64rem, 100%)")  # (not Quasar's 560px)
        save = settings_fields(settings_id, section)

        def ok() -> bool | None:
            if save() is False:
                return False
            if changed:
                changed()

        actions(dlg, _("Save"), ok, mark="settings-save")


def settings_choice(chosen: int | None, on_change: Callable[[], None] | None = None) -> ui.select:
    """A select of the settings a folder uses (``chosen``; None: the default ones), with a
    link to them in the Parameters."""
    named = folders.named_settings()
    default = next((x for x in named if x.default), None)
    options: dict[int | None, str] = {
        None: _("The default ones ({name})").format(name=default.name if default else "")
    }
    options |= {x.id: x.name for x in named if not x.default or x.id == chosen}
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        select = (
            ui.select(
                options,
                value=chosen if chosen in options else None,
                label=_("Settings"),
                on_change=on_change,
            )
            .classes("grow")
            .tooltip(_("Its categories, star lists, how its notes cite papers…"))
            .mark("folder-settings-choice")
        )
        ui.link(_("Edit them in the Parameters"), URL).classes("text-sm").mark(
            "folder-settings-link"
        )
    return select


def _name_dialog(title: str, value: str, ok_label: str, apply: Callable[[str], None]) -> None:
    """Ask for a name, then ``apply`` it (refused, said: ValueError)."""
    with transient_dialog(title, width="w-96") as (dlg, _card):
        name = ui.input(_("Name"), value=value).classes("w-full").props("autofocus")
        name.mark("settings-name-input")

        def ok() -> bool | None:
            try:
                apply(name.value or "")
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return False

        actions(dlg, ok_label, ok, mark="settings-name-ok")


def settings_tab() -> None:
    """The settings of folders, the default ones first: each one's name and the folders
    using it; created (empty, or a copy of others), renamed, made the default, edited (with
    their categories, their report templates), deleted (unless used, or holding
    excerpts)."""
    ui.label(
        _(
            "A folder uses the settings chosen in its dialog (its categories, star lists, how "
            "its notes cite papers…), else the default ones; changing them changes them for "
            "every folder using them."
        )
        + " "
        + _(
            "The report templates (the texts a report starts from) are "
            "in each settings' “Report” section."
        )
    ).classes("text-sm text-grey").mark("folder-settings-help")

    def create(source: folders.NamedSettings | None = None) -> None:
        def apply(name: str) -> None:
            folders.create_settings(name, source.id if source else None)
            listing.refresh()

        _name_dialog(
            _("New settings") if source is None else _("Copy of “{name}”").format(name=source.name),
            "" if source is None else _("{name} (copy)").format(name=source.name),
            _("Create"),
            apply,
        )

    def rename(x: folders.NamedSettings) -> None:
        def apply(name: str) -> None:
            folders.rename_settings(x.id, name)
            listing.refresh()

        _name_dialog(_("Rename the settings"), x.name, _("Rename"), apply)

    def make_default(x: folders.NamedSettings) -> None:
        folders.set_default_settings(x.id)
        listing.refresh()

    def delete(x: folders.NamedSettings) -> None:
        def ok() -> bool | None:
            try:
                folders.delete_settings(x.id)
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return False
            listing.refresh()

        confirm(
            _("Delete the settings “{name}”, with their categories and star lists?").format(
                name=x.name
            ),
            _("Delete"),
            ok,
            mark="settings-delete-ok",
        )

    def button(icon: str, tip: str, mark: str, act: Callable[[], None]) -> ui.button:
        return (
            ui.button(icon=icon, on_click=act)
            .props("flat dense round size=sm")
            .tooltip(tip)
            .mark(mark)
        )

    @ui.refreshable
    def listing() -> None:
        for x in folders.named_settings():
            with (
                ui.card().classes("w-full p-2 gap-1").mark(f"named-settings-{x.id}"),
                ui.row().classes("w-full items-center no-wrap gap-1"),
            ):
                with ui.column().classes("grow min-w-0 gap-0"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(x.name).classes("font-medium").mark(f"named-settings-name-{x.id}")
                        if x.default:
                            ui.badge(_("default")).props("outline").mark(
                                f"named-settings-default-{x.id}"
                            )
                    ui.label(
                        ngettext(
                            "Used by {n} folder: {folders}",
                            "Used by {n} folders: {folders}",
                            len(x.folders),
                        ).format(n=len(x.folders), folders=", ".join(x.folders))
                        if x.folders
                        else _("Used by no folder")
                    ).classes("text-sm text-grey").mark(f"named-settings-folders-{x.id}")
                button(
                    "edit",
                    _("Edit them"),
                    f"settings-edit-{x.id}",
                    lambda x=x: settings_dialog(x.id, listing.refresh),
                )
                button(
                    "category",
                    _("Their categories"),
                    f"settings-categories-{x.id}",
                    lambda x=x: settings_dialog(x.id, listing.refresh, "categories"),
                )
                button(
                    "article",
                    _("Their report: templates and language"),
                    f"settings-report-{x.id}",
                    lambda x=x: settings_dialog(x.id, listing.refresh, "report"),
                )
                button(
                    "content_copy",
                    _("Copy them (new settings)"),
                    f"settings-duplicate-{x.id}",
                    lambda x=x: create(x),
                )
                button(
                    "drive_file_rename_outline",
                    _("Rename them"),
                    f"settings-rename-{x.id}",
                    lambda x=x: rename(x),
                )
                if not x.default:
                    button(
                        "star_outline",
                        _("Make them the default ones (of the folders that chose none)"),
                        f"settings-make-default-{x.id}",
                        lambda x=x: make_default(x),
                    )
                    delete_button = button(
                        "delete",
                        _("Delete them"),
                        f"settings-delete-{x.id}",
                        lambda x=x: delete(x),
                    )
                    if x.folders or x.filed:
                        delete_button.disable()
                        delete_button.tooltip(
                            _("Used by folders: to delete them, choose others for these first")
                            if x.folders
                            else _("Excerpts or report texts are filed in their categories")
                        )

    with ui.column().classes("w-full gap-2"):
        listing()
    ui.button(_("New settings"), icon="add", on_click=lambda: create()).props("flat").mark(
        "settings-new"
    )
