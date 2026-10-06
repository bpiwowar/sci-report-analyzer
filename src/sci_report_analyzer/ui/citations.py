"""How papers are cited in the notes: the templates (general, or a folder's), a folder's
numbering, and whether its notes cite its papers (the hint on the editor's "Papers")."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import annotations, reports
from ..i18n import N_, _
from . import unsaved
from .categories_editor import shared_note
from .mdedit import MarkdownEditor

TEMPLATES_HELP = N_(
    "How a paper is cited: `[@key]` followed by the template. `{.notes}`, `{.tags}`, "
    "`{.full}` (title, venue…) give its entry; otherwise the fields `.number` (the number, "
    "formatted), `.index` (the bare number), `.title`, `.venue`, `.short-venue` (its "
    "acronym), `.year`, `.tags`, `.notes` are replaced and the rest kept, a parenthesis "
    "without a value dropped: `{**#.index** (.short-venue .year)}` gives **#2** (EMNLP "
    "2026). Empty: the number. A template with a name is used as `.name`, alone "
    "(`[@key]{.starred}`) or within another one (`{.starred: .notes}`)."
)


def template_rows(
    items: list[reports.Template],
    *,
    mark: str,
    default: dict | None = None,
    labels: bool = True,
) -> Callable[[], None]:
    """The templates to edit (``default``: {"attrs": the default's}, to choose it;
    ``labels``: with their labels); returns the refresh of the rows."""

    @ui.refreshable
    def rows() -> None:
        for i, t in enumerate(items):
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"{mark}-{i}"):
                if default is not None:
                    on = default["attrs"] == t.attrs
                    ui.button(
                        icon="star" if on else "star_border",
                        on_click=lambda t=t: (default.update(attrs=t.attrs), rows.refresh()),
                    ).props("flat round dense color=amber-8").tooltip(
                        _("The default") if on else _("Make it the default")
                    ).mark(f"{mark}-default-{i}")
                if labels:
                    ui.input(
                        _("Label"),
                        value=t.label,
                        on_change=lambda e, t=t: setattr(t, "label", e.value),
                    ).props("dense outlined").classes("w-48")
                ui.input(
                    _("Name"),
                    value=t.name,
                    on_change=lambda e, t=t: setattr(t, "name", e.value or ""),
                    validation=reports.check_name,
                ).props("dense outlined prefix=.").classes("w-32").tooltip(
                    _("Its name, to use it as .name (optional)")
                ).mark(f"{mark}-name-{i}")
                ui.input(
                    _("Template"),
                    value=t.attrs,
                    on_change=lambda e, t=t: set_attrs(t, e.value or ""),
                    validation=reports.check_template,
                ).props("dense outlined").classes("w-80").mark(f"{mark}-attrs-{i}")
                ui.button(icon="delete", on_click=lambda i=i: remove(i)).props(
                    "flat round dense color=negative"
                ).mark(f"{mark}-delete-{i}")

    def set_attrs(t: reports.Template, attrs: str) -> None:
        if default is not None and default["attrs"] == t.attrs:
            default["attrs"] = attrs
        t.attrs = attrs

    def remove(i: int) -> None:
        del items[i]
        rows.refresh()

    rows()
    return rows.refresh


def templates_section() -> None:
    """Settings → Citation templates: the general ones."""
    cfg = reports.load_templates()
    default = {"attrs": cfg.default}
    ui.markdown(_(TEMPLATES_HELP)).classes("text-sm text-grey")
    with ui.column().classes("gap-1"):
        refresh = template_rows(cfg.items, mark="report-template", default=default)

    def add() -> None:
        cfg.items.append(reports.Template(_("New template"), "{.short-venue .year}"))
        refresh()

    def reset() -> None:
        d = reports.default_templates()
        cfg.items[:] = d.items
        default["attrs"] = d.default
        refresh()

    def save() -> bool:
        cfg.default = default["attrs"]
        try:
            reports.save_templates(cfg)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return False
        default["attrs"] = cfg.default
        refresh()
        ui.notify(_("Saved"), type="positive")
        return True

    save = unsaved.track(lambda: (cfg.items, default["attrs"]), save)
    with ui.row().classes("mt-2"):
        ui.button(_("Add a template"), icon="add", on_click=add).props("flat").mark(
            "report-template-add"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save).mark("report-templates-save")


# ---- A folder's citations ---------------------------------------------------------------------


def folder_citations_section(folder_id: int) -> Callable[[], bool | None]:
    """A folder's settings of its notes (in its dialog): the numbering (the tag whose papers
    are numbered, the number's format), its own templates (over the general ones, by name),
    the notes it starts with. Returns its save (False: refused, said)."""
    n = reports.numbering(folder_id)
    own = reports.folder_templates(folder_id)
    general = reports.load_templates().names
    tags = {t.id: ("⏱ " if t.per_period else "") + t.name for t in annotations.all_tags()}
    with ui.column().classes("w-full gap-2"):
        shared_note(folder_id)
        with ui.row().classes("w-full items-start gap-3"):
            tag = (
                ui.select(
                    {0: _("None: as first cited"), **tags},
                    value=n.tag_id if n.tag_id in tags else 0,
                    label=_("Numbered papers: with the tag"),
                )
                .props("dense outlined")
                .classes("w-64")
                .tooltip(
                    _(
                        "Their numbers (.index, .number, [@key]): their numbers within the tag "
                        "(put from a list, or set by hand: Alt-click a #number, or order the "
                        "tag's papers), else by year; the other papers apart, from 1, as first "
                        "cited. They are the papers to discuss (without: those of the period's "
                        "years)."
                    )
                )
                .mark("folder-number-tag")
            )
            fmt = (
                ui.input(
                    _("Number format"),
                    value=n.format,
                    placeholder=reports.REFERENCE_FORMAT,
                )
                .props("dense outlined")
                .classes("w-48")
                .tooltip(
                    _(
                        "How a number is written ({index}: the number; default: {default}): "
                        "[@key], and .number in the templates"
                    ).format(index="{index}", default=reports.REFERENCE_FORMAT)
                )
                .mark("folder-number-format")
            )
            listed = (
                ui.input(
                    _("Number of a listed paper"),
                    value=n.listed_format,
                    placeholder=reports.NUMBER_FORMAT,
                )
                .props("dense outlined")
                .classes("w-48")
                .tooltip(
                    _(
                        "How the number of a paper with the tag is written ({index}: the "
                        "number; default: {default}): [@key], and .number in the templates; "
                        "the others: the number format"
                    ).format(index="{index}", default=reports.NUMBER_FORMAT)
                )
                .mark("folder-listed-format")
            )
            ui.button(
                icon="restart_alt",
                on_click=lambda: (
                    fmt.set_value(reports.REFERENCE_FORMAT),
                    listed.set_value(reports.NUMBER_FORMAT),
                ),
            ).props("flat round dense").tooltip(_("The default formats"))
        same = (
            ui.label(
                _(
                    "Both formats are the same: the papers with the tag and the others, each "
                    "numbered from 1, cannot be told apart."
                )
            )
            .classes("text-sm text-warning")
            .mark("folder-same-formats")
        )

        def check_formats() -> None:
            same.visible = (
                bool(tag.value)
                and (fmt.value or reports.REFERENCE_FORMAT).strip()
                == (listed.value or reports.NUMBER_FORMAT).strip()
            )

        for el in (tag, fmt, listed):
            el.on_value_change(check_formats)
        check_formats()
        ui.label(_("Templates of the folder")).classes("font-medium mt-2")
        ui.markdown(
            _(
                "Each named: it is used as `.name` in the folder's notes, instead of the "
                "general template of that name (Settings → Citation templates)."
            )
            + " "
            + _(
                "`.number`: the number written as set above (e.g. `{(.number .short-venue "
                ".year)}`), `.index`: the bare number (e.g. `{**#.index**}`)."
            )
            + (
                " "
                + _("General: {names}").format(
                    names=", ".join(f"`.{k}` = `{v or '∅'}`" for k, v in sorted(general.items()))
                )
                if general
                else ""
            )
        ).classes("text-sm text-grey")
        with ui.column().classes("gap-1"):
            refresh = template_rows(own, mark="folder-template", labels=False)
        skeleton = (
            ui.textarea(
                _("Starting notes"),
                value=reports.skeleton(folder_id),
                placeholder=f"## {_('Publications')}\n[]{{.publications}}\n\n"
                f"## {_('Activities')}\n[]{{.excerpts}}",
            )
            .props("dense outlined autogrow")
            .classes("w-full font-mono text-sm mt-2")
            .tooltip(
                _(
                    "The text a person's notes start with, while empty (e.g. headings and the "
                    "blocks []{.publications}: the summary of their publications, "
                    "[]{.excerpts}: their excerpts by category)"
                )
            )
            .mark("folder-skeleton")
        )

        def add() -> None:
            own.append(reports.Template("", "{.index (.short-venue .year)}", ""))
            refresh()

        def save() -> bool | None:
            try:
                reports.save_folder_templates(folder_id, own)
            except ValueError as e:
                ui.notify(str(e), type="negative")
                return False
            reports.save_numbering(
                folder_id,
                reports.Numbering(tag.value or None, fmt.value or "", listed.value or ""),
            )
            reports.save_skeleton(folder_id, skeleton.value or "")
            return None

        ui.button(_("Add a template"), icon="add", on_click=add).props("flat").mark(
            "folder-template-add"
        )
    return save


# ---- Whether the notes cite the papers --------------------------------------------------------


def _paper(p: reports.Paper, ctx: reports.Context) -> str:
    """Its number (if numbered, or cited), title and year."""
    p = ctx.by_key.get(p.key, p)
    number = ctx.number(p).replace("*", "") + " " if p.key in ctx.by_key else ""
    year = f" ({p.stat.year})" if p.stat.year else ""
    return f"{number}{p.stat.title or _('(untitled)')}{year}"


def citation_hint(editor: MarkdownEditor, ctx: reports.Context, rendered: reports.Rendered) -> None:
    """On the editor's "Papers" (the papers to discuss, beside it): coloured by whether the
    notes cite them (all of them, some not of the period's years, some not), how many on
    hover (and the notes' errors, e.g. a template cycle)."""
    st = reports.citation_status(ctx, rendered.cited)
    cited = len(st.discuss) - len(st.missing)
    text = (
        _("{cited} of the {n} papers to discuss cited").format(cited=cited, n=len(st.discuss))
        if st.discuss
        else _("No paper to discuss (set the folder's numbered tag)")
    )
    editor.mode.hint("side", st.colour, " · ".join([text, *rendered.errors]))
