"""How papers are cited in the notes: the templates (general, or a folder's), a folder's
numbering, and whether its notes cite its papers (the hint on the editor's "Papers")."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import annotations, reports
from ..i18n import N_, _
from . import unsaved
from .mdedit import MarkdownEditor

TEMPLATES_HELP = N_(
    "How a paper is cited: `[@key]` followed by the template. `{.notes}`, `{.tags}`, "
    "`{.full}` (title, venue…) give its entry; otherwise the fields `.number` (the number, "
    "formatted), `.index` (the bare number), `.title`, `.venue`, `.short-venue` (its "
    "acronym), `.year`, `.tags`, `.notes` are replaced and the rest kept, a parenthesis "
    "without a value dropped: `{.number (.short-venue .year)}` gives **#2** (EMNLP "
    "2026). A citation without a template (`[@key]`, as inserted) is written as the folder "
    "says (its settings: Notes and citations). A template with a name is used as `.name`, "
    "alone (`[@key]{.starred}`) or within another one (`{.starred: .notes}`)."
)


def template_rows(
    items: list[reports.Template],
    *,
    mark: str,
    labels: bool = True,
) -> Callable[[], None]:
    """The templates to edit (``labels``: with their labels); returns the refresh of the
    rows."""

    @ui.refreshable
    def rows() -> None:
        for i, t in enumerate(items):
            with ui.row().classes("items-center gap-2 no-wrap").mark(f"{mark}-{i}"):
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
        t.attrs = attrs

    def remove(i: int) -> None:
        del items[i]
        rows.refresh()

    rows()
    return rows.refresh


def templates_section() -> None:
    """Settings → Citation templates: the general ones."""
    cfg = reports.load_templates()
    ui.markdown(_(TEMPLATES_HELP)).classes("text-sm text-grey")
    with ui.column().classes("gap-1"):
        refresh = template_rows(cfg.items, mark="report-template")

    def add() -> None:
        cfg.items.append(reports.Template(_("New template"), "{.short-venue .year}"))
        refresh()

    def reset() -> None:
        cfg.items[:] = reports.default_templates().items
        refresh()

    def save() -> bool:
        try:
            reports.save_templates(cfg)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return False
        refresh()
        ui.notify(_("Saved"), type="positive")
        return True

    save = unsaved.track(lambda: cfg.items, save)
    with ui.row().classes("mt-2"):
        ui.button(_("Add a template"), icon="add", on_click=add).props("flat").mark(
            "report-template-add"
        )
        ui.button(_("Defaults"), icon="restart_alt", on_click=reset).props("flat")
        unsaved.cancel_button()
        ui.button(_("Save"), icon="save", on_click=save).mark("report-templates-save")


# ---- A folder's citations ---------------------------------------------------------------------


def folder_citations_section(folder_id: int) -> Callable[[], bool | None]:
    """A folder's settings of its notes (in its dialog): its star lists (their papers
    numbered there, as each says; to cite or not), how the other papers cited are numbered,
    the template of ``[@key]``, with examples; its own templates (over the general ones, by
    name). Returns its save (False: refused, said)."""
    from .. import folders

    settings = folders.settings_of(folder_id)
    n = reports.numbering(folder_id)
    own = reports.folder_templates(folder_id)
    general = reports.load_templates().names
    lists = [
        {
            "id": t.id,
            "name": t.name,
            "format": t.number_format or reports.NUMBER_FORMAT,
            "template": t.cite_template or "",
            "cite": t.must_cite,
            "builtin": t.key is not None,
            "colour": t.colour,
        }
        for t in annotations.star_lists(settings_id=settings)
    ]
    removed: list[int] = []
    with ui.column().classes("w-full gap-2"):
        ui.label(_("Star lists")).classes("font-medium")
        ui.label(
            _(
                "The papers starred within a person's period (★: the first list), in order: "
                "numbered in the first list they are in, written as it says ({index}: the "
                "number: its reference), and cited by [@key] (as inserted) as it says. Those "
                "of the lists to cite are the papers to discuss, each to be cited in the notes "
                "(without: those of the period's years)."
            )
        ).classes("text-sm text-grey")

        @ui.refreshable
        def list_rows() -> None:
            for i, x in enumerate(lists):
                with ui.row().classes("items-center gap-2 no-wrap").mark(f"folder-list-{i}"):
                    ui.icon("star", color="amber-8" if i == 0 else "grey").tooltip(
                        _("The ★ button's") if i == 0 else ""
                    )
                    ui.input(
                        _("Name"),
                        value=x["name"],
                        on_change=lambda e, x=x: (x.update(name=e.value or ""), shown.refresh()),
                    ).props("dense outlined").classes("w-40").mark(f"folder-list-name-{i}")
                    ui.input(
                        _("Reference"),
                        value=x["format"],
                        on_change=lambda e, x=x: (
                            x.update(format=e.value or ""),
                            shown.refresh(),
                        ),
                    ).props("dense outlined").classes("w-36").tooltip(
                        _(
                            "How the number of its papers is written ({index}: the number), in "
                            "the references and as .number"
                        )
                    ).mark(f"folder-list-format-{i}")
                    ui.input(
                        _("Citation"),
                        value=x["template"],
                        placeholder=_("the number"),
                        on_change=lambda e, x=x: (
                            x.update(template=e.value or ""),
                            shown.refresh(),
                        ),
                        validation=lambda v: reports.check_template(v or ""),
                    ).props("dense outlined").classes("w-72").tooltip(
                        _(
                            "How its papers are cited by [@key] (e.g. {.number (.short-venue "
                            ".year)}, .number: its reference; empty: the reference)"
                        )
                    ).mark(f"folder-list-template-{i}")
                    ui.checkbox(
                        _("to cite"),
                        value=x["cite"],
                        on_change=lambda e, x=x: x.update(cite=bool(e.value)),
                    ).tooltip(_("Its papers are to discuss: each cited in the notes")).mark(
                        f"folder-list-cite-{i}"
                    )
                    up = ui.button(icon="arrow_upward", on_click=lambda i=i: move(i, -1))
                    up.props("flat round dense size=sm").mark(f"folder-list-up-{i}")
                    if i == 0:
                        up.disable()
                    remove_button = ui.button(icon="delete", on_click=lambda i=i: remove(i))
                    remove_button.props("flat round dense size=sm color=negative").mark(
                        f"folder-list-delete-{i}"
                    )
                    if x["builtin"]:
                        remove_button.disable()
                        remove_button.tooltip(_("Built in: it can be renamed, not removed"))
                    else:
                        remove_button.tooltip(_("Remove the list (and its papers' stars)"))

        def move(i: int, delta: int) -> None:
            lists[i + delta], lists[i] = lists[i], lists[i + delta]
            list_rows.refresh()
            shown.refresh()

        def remove(i: int) -> None:
            x = lists.pop(i)
            if x["id"] is not None:
                removed.append(x["id"])
            list_rows.refresh()
            shown.refresh()

        def add_list() -> None:
            lists.append(
                {
                    "id": None,
                    "name": "",
                    "format": reports.NUMBER_FORMAT,
                    "template": "",
                    "cite": False,
                    "builtin": False,
                    "colour": None,
                }
            )
            list_rows.refresh()
            shown.refresh()

        with ui.column().classes("gap-1"):
            list_rows()
        with ui.row().classes("items-center gap-2"):
            ui.button(_("Add a list"), icon="add", on_click=add_list).props("flat").mark(
                "folder-list-add"
            )
        with ui.row().classes("items-center gap-2 no-wrap mt-1"):
            ui.label(_("Cited only")).classes("w-48 text-sm text-right").tooltip(
                _("The papers in no list")
            )
            fmt = (
                ui.input(
                    _("Reference"),
                    value=n.format,
                    placeholder=reports.REFERENCE_FORMAT,
                    on_change=lambda: shown.refresh(),
                )
                .props("dense outlined")
                .classes("w-36")
                .tooltip(
                    _(
                        "The papers in no list: numbered apart, from 1, as first cited "
                        "({index}: the number; default: {default})"
                    ).format(index="{index}", default=reports.REFERENCE_FORMAT)
                )
                .mark("folder-number-format")
            )
            cite = (
                ui.input(
                    _("Citation"),
                    value=n.cite,
                    placeholder=_("the number"),
                    on_change=lambda: shown.refresh(),
                    validation=lambda v: reports.check_template(v or ""),
                )
                .props("dense outlined")
                .classes("w-72")
                .tooltip(
                    _(
                        "How the papers in no list are cited by [@key], as inserted (e.g. "
                        "{.number (.short-venue .year)}, .number: their reference; empty: the "
                        "reference)"
                    )
                )
                .mark("folder-cite")
            )

        @ui.refreshable
        def shown() -> None:
            numbering = reports.Numbering(fmt.value or "", cite.value or "")
            star = [
                reports.StarList(
                    i + 1,
                    x["name"] or "…",
                    x["format"] or reports.NUMBER_FORMAT,
                    False,
                    x["template"],
                )
                for i, x in enumerate(lists)
            ]
            names = {**general, **{t.name: t.attrs for t in own if t.name}}
            with (
                ui.grid(columns="auto auto 1fr")
                .classes("w-full gap-x-4 gap-y-1 text-sm items-baseline")
                .mark("folder-examples")
            ):
                for h in (_("A paper"), "[@doe2026neural]", _("In the references")):
                    ui.label(h).classes("text-xs text-grey uppercase")
                for name, cited, entry in reports.examples(numbering, star, names):
                    ui.label(_("in “{list}”").format(list=name) if name else _("cited only"))
                    ui.markdown(cited).classes("vr-note")
                    ui.markdown(entry).classes("vr-note text-grey-8")

            formats = [(x["format"] or reports.NUMBER_FORMAT).strip() for x in lists]
            formats.append((fmt.value or reports.REFERENCE_FORMAT).strip())
            if len(set(formats)) < len(formats):
                ui.label(
                    _(
                        "Some formats are the same: their papers, each numbered from 1, "
                        "cannot be told apart."
                    )
                ).classes("text-sm text-warning").mark("folder-same-formats")

        shown()
        ui.label(_("Templates of the folder")).classes("font-medium mt-2")
        ui.markdown(
            _(
                "Each named: it is used as `.name` in the folder's notes, instead of the "
                "general template of that name (Settings → Citation templates)."
            )
            + " "
            + _(
                "`.number`: the number written as its list says (e.g. `{(.number .short-venue "
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

        def add() -> None:
            own.append(reports.Template("", "{.number (.short-venue .year)}", ""))
            refresh()

        def save() -> bool | None:
            try:
                reports.save_folder_templates(folder_id, own)
            except ValueError as e:
                ui.notify(str(e), type="negative")
                return False
            if any(not x["name"].strip() for x in lists):
                ui.notify(_("A star list needs a name"), type="negative")
                return False
            names = [x["name"].strip() for x in lists]
            if len(set(names)) < len(names):
                ui.notify(_("Two star lists have the same name"), type="negative")
                return False
            for template in [cite.value, *(x["template"] for x in lists)]:
                if (err := reports.check_template((template or "").strip())) is not None:
                    ui.notify(err, type="negative")
                    return False
            ids = []
            for x in lists:
                try:
                    ids.append(
                        annotations.save_list(
                            settings,
                            x["name"],
                            colour=x["colour"],
                            number_format=x["format"],
                            cite_template=x["template"],
                            must_cite=x["cite"],
                            list_id=x["id"],
                        )
                    )
                except ValueError:
                    ui.notify(
                        _("“{name}” is already a tag, or another folder's list").format(
                            name=x["name"]
                        ),
                        type="negative",
                    )
                    return False
            for list_id in removed:
                annotations.delete_tag(list_id)
            annotations.order_lists(ids)
            reports.save_numbering(folder_id, reports.Numbering(fmt.value or "", cite.value or ""))
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
        else _("No paper to discuss (a star list to cite: the folder's settings)")
    )
    editor.mode.hint("side", st.colour, " · ".join([text, *rendered.errors]))
