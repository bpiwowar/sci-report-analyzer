"""How well the categories suggested for an excerpt (see classifier.py) match those its
excerpts are filed in, in some folders' settings: the rate, overall and by category; the
excerpts suggested elsewhere, moved there (or to another category), or kept where they are."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nicegui import ui

from .. import categories, classifier, folders, inline_md, llm
from ..i18n import _, ngettext
from .dialogs import transient_dialog

LLM_URL = "/settings?tab=llm"


def _path_line(what: str, path: str, colour: str, mark: str) -> None:
    """A category on its own line, wrapped (never cut): its parents, then its name in bold."""
    *parents, leaf = path.split(" › ")
    with ui.element("div").classes(f"text-xs {colour} break-words").mark(mark):
        ui.label(what + " ").classes("inline")
        if parents:
            ui.label(" › ".join(parents) + " › ").classes("inline")
        ui.label(leaf).classes("inline font-bold")


def excerpt_card(
    excerpt_id: int,
    text: str,
    category_id: int,
    suggested: int | None,
    names: dict[int, str],
    move: Callable[[int], Awaitable[None] | None],
    keep: Callable[[], Awaitable[None] | None] | None = None,
    others: dict[int, str] | None = None,
) -> None:
    """An excerpt to file again: its text, its category and the one suggested (``names``:
    the categories' paths); moved there, or to another one (among ``others``: else all but
    its own), or kept (if ``keep``)."""
    with ui.card().classes("w-full p-2 gap-1").mark(f"reclassify-excerpt-{excerpt_id}"):
        ui.label(text).classes("text-sm line-clamp-3").tooltip(text)
        _path_line(
            _("In:"), names.get(category_id, "?"), "text-grey-8", f"reclassify-in-{excerpt_id}"
        )
        if suggested is not None:
            _path_line(
                _("Suggested:"),
                names.get(suggested, "?"),
                "text-purple",
                f"reclassify-suggested-{excerpt_id}",
            )
        else:
            ui.label(_("no suggestion")).classes("text-xs text-purple")
        with ui.row().classes("w-full items-center gap-2"):
            if suggested is not None:
                leaf = names.get(suggested, "?").split(" › ")[-1]
                ui.button(
                    _("Move to {category}").format(category=leaf),
                    icon="drive_file_move",
                    on_click=lambda: move(suggested),
                ).props("flat dense no-caps size=sm color=purple").mark(
                    f"reclassify-move-{excerpt_id}"
                )
            ui.select(
                others
                if others is not None
                else {c: p for c, p in names.items() if c != category_id},
                label=_("Another category"),
                with_input=True,
                on_change=lambda ev: ev.value and move(ev.value),
            ).props("dense options-dense").classes("w-56").mark(f"reclassify-other-{excerpt_id}")
            if keep:
                ui.button(_("Keep it"), icon="check", on_click=keep).props(
                    "flat dense no-caps size=sm color=positive"
                ).tooltip(_("Its category is the right one: no longer listed")).mark(
                    f"reclassify-keep-{excerpt_id}"
                )


def classification_dialog(settings_id: int) -> None:
    """The classification of the excerpts filed in some settings' categories: each one
    hidden, then its category suggested (see classifier.evaluate); those suggested elsewhere
    listed, to move or keep (then no longer listed); trained again on them. Says why when
    it cannot be done (no embedding model, category or excerpt)."""
    ref = folders.SettingsRef(settings_id)
    name = next((x.name for x in folders.named_settings() if x.id == settings_id), "")
    with transient_dialog(
        _("Classification “{name}”").format(name=name), width="w-full max-w-4xl"
    ) as (dlg, card):
        card.style("max-width: min(56rem, 100%)")
        body = ui.column().classes("w-full gap-2").mark("classification")
        with ui.row().classes("w-full justify-end"):
            train_button = (
                ui.button(_("Train on them"), icon="model_training")
                .props("flat no-caps color=purple")
                .tooltip(_("Learn again from the excerpts as filed now (for the suggestions)"))
                .mark("classification-train")
            )
            ui.button(_("Close"), on_click=dlg.close).props("flat")

    def unavailable(text: str, link: tuple[str, str] | None = None) -> None:
        train_button.set_visibility(False)
        with body, ui.row().classes("items-center gap-2").mark("classification-off"):
            ui.icon("info", color="grey")
            ui.label(text).classes("text-sm text-grey")
            if link:
                ui.link(link[0], link[1]).classes("text-sm")

    async def load() -> None:
        body.clear()
        tree = categories.tree(ref)
        if not llm.embed_model():
            unavailable(
                _(
                    "No suggestion: turn on the local LLM and choose an embedding model "
                    "(Settings → Local LLM; or these settings' Models section)"
                ),
                (_("Local LLM"), LLM_URL),
            )
            return
        if not tree:
            unavailable(_("No category yet: add some in these settings' Categories section"))
            return
        with body:
            ui.spinner().mark("classification-loading")
        try:
            ev = await classifier.evaluate(ref)
        except ValueError:
            body.clear()
            unavailable(
                _("No excerpt filed in these categories yet: file some from people's documents")
            )
            return
        except Exception as e:
            body.clear()
            unavailable(_("Cannot evaluate: {error}").format(error=e))
            return
        body.clear()
        train_button.set_visibility(True)
        with body:
            shown(ev, {n.id: n.path for n in tree})

    def shown(ev: classifier.Evaluation, names: dict[int, str]) -> None:
        right = sum(e.right for e in ev.excerpts)
        ui.label(
            ngettext(
                "{right} of {n} excerpt suggested in its category: {rate:.0%}",
                "{right} of {n} excerpts suggested in their category: {rate:.0%}",
                len(ev.excerpts),
            ).format(right=right, n=len(ev.excerpts), rate=ev.rate)
        ).classes("text-base font-medium").mark("classification-rate").tooltip(
            _(
                "Each excerpt hidden from what is learnt, then its category suggested: the "
                "share suggested first in the category it is filed in"
            )
        )
        if len({e.category_id for e in ev.excerpts}) < 2:
            ui.label(_("A single category with excerpts: nothing to tell apart")).classes(
                "text-sm text-grey"
            )
        with ui.column().classes("w-full gap-0"):
            for cat, (ok, n) in sorted(ev.rates().items(), key=lambda x: names.get(x[0], "")):
                with (
                    ui.row()
                    .classes("w-full items-center gap-2 no-wrap")
                    .mark(f"classification-category-{cat}")
                ):
                    ui.label(names.get(cat, "?")).classes("grow min-w-0 ellipsis text-sm")
                    ui.label(f"{ok}/{n}").classes("text-xs text-grey")
                    ui.linear_progress(ok / n, show_value=False, size="6px").props(
                        "rounded color=positive track-color=red-2"
                    ).classes("w-24")
                    ui.label(f"{ok / n:.0%}").classes("text-xs w-10 text-right")
        wrong = ev.wrong
        held = sum(e.kept and not e.right for e in ev.excerpts)
        ui.label(
            ngettext(
                "{n} excerpt suggested elsewhere", "{n} excerpts suggested elsewhere", len(wrong)
            ).format(n=len(wrong))
            if wrong
            else _("No excerpt suggested elsewhere")
        ).classes("text-sm font-medium text-primary mt-2").mark("classification-wrong-count")
        if held:
            ui.label(
                ngettext(
                    "({n} kept in its category, not listed)",
                    "({n} kept in their category, not listed)",
                    held,
                ).format(n=held)
            ).classes("text-xs text-grey").mark("classification-kept-count")
        with ui.column().classes("w-full gap-1 max-h-96 overflow-auto"):
            for e in wrong:
                excerpt_card(
                    e.excerpt_id,
                    inline_md.plain(e.text),
                    e.category_id,
                    e.suggested,
                    names,
                    lambda c, e=e: move(e, c),
                    lambda e=e: keep(e),
                )

    async def move(e: classifier.Evaluated, category_id: int) -> None:
        categories.move_excerpt(e.lead_id, category_id)
        await load()

    async def keep(e: classifier.Evaluated) -> None:
        classifier.keep(ref, e.excerpt_id)
        await load()

    async def train() -> None:
        train_button.props("loading")
        try:
            t = await classifier.train(ref)
        except Exception as e:
            ui.notify(_("Cannot train: {error}").format(error=e), type="negative")
        else:
            ui.notify(
                ngettext("Learnt from {n} excerpt", "Learnt from {n} excerpts", t.count).format(
                    n=t.count
                ),
                type="positive",
            )
        finally:
            train_button.props(remove="loading")

    train_button.on_click(train)
    with card:
        ui.timer(0.05, load, once=True)
