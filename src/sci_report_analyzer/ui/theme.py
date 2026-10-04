"""Shared page frame and small UI helpers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from html import escape

import markdown2
from nicegui import ui

from .. import i18n
from ..i18n import _
from ..ranking import tracks
from ..ranking.badge import LEVEL_HELP, Badge, Category, category_of, text_colour

SOURCE_SHORT = {
    "dblp": "DBLP",
    "hal": "HAL",
    "openalex": "OA",
    "semanticscholar": "S2",
    "orcid": "ORCID",
    "scholar": "GS",
    "thesesfr": "theses.fr",
    "doi": "DOI",
}
SOURCE_COLOUR = {
    "dblp": "#004f9f",
    "hal": "#c1272d",
    "openalex": "#e36b1e",
    "semanticscholar": "#1857b6",
    "orcid": "#a6ce39",
    "scholar": "#4285f4",
    "thesesfr": "#6d2077",
    "doi": "#fab70c",
}
STATUS_COLOUR = {
    "up to date": "positive",
    "updating…": "info",
    "error": "negative",
    "never synced": "warning",
    "out of date": "warning",
    "candidate": "grey",
    "rejected": "grey",
}

# Opacity of the items not selected in a chart: faint, not to be confused with the selection.
DIM_OPACITY = 0.12

CSS = """
.vr-chip { display:inline-block; padding:0 6px; border-radius:9px; font-size:11px;
           line-height:18px; color:#fff; white-space:nowrap; margin-right:3px; }
.vr-src { display:inline-block; padding:0 4px; border-radius:4px; font-size:10px;
          line-height:16px; color:#fff; margin-right:2px; cursor:default; }
.vr-src.archival { opacity:.55; }
.vr-owner { font-weight:700; }
.vr-student { font-weight:600; color:#6639ba; }
.vr-former { font-style:italic; color:#8a6fc0; }
.vr-maybe { color:#bc4c00; border-bottom:1px dashed #bc4c00; cursor:help; }
.vr-maybe.owner { font-weight:700; }
/* Long venue texts / rules wrap instead of overflowing the details dialog. */
.vr-details { overflow-x: hidden; overflow-wrap: anywhere; }
.vr-details .q-tab-panel, .vr-details .row > *, .vr-details .column > * { min-width: 0; }
/* Tab panels (nested in the details) grow with their content: the dialog scrolls instead
   of clipping them (Quasar's panels hide their overflow). */
.vr-details .q-panel-parent, .vr-details .q-tab-panels, .vr-details .q-panel {
  overflow: visible; height: auto;
}
/* Chips, source badges and icon buttons keep their size next to long texts. */
.vr-details .row > :has(> .vr-chip), .vr-details .row > :has(> .vr-src),
.vr-details .row > .q-btn--round { flex-shrink: 0; min-width: auto; }
.vr-matched { border-bottom:1px dotted #8c959f; }
.vr-venue-link { color:inherit; text-decoration:none; }
.vr-venue-link:hover .vr-matched { border-bottom-style:solid; }
.vr-count-link { color:var(--q-primary); cursor:pointer; text-decoration:underline dotted; }
.vr-drop td { background: rgba(9,105,218,.15) !important; outline: 1px dashed var(--q-primary); }
.vr-row:hover { background: rgba(127,127,127,.08); }
.vr-track { background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.35) 0 3px,
            transparent 3px 7px); }
"""
MARKDOWN_CSS = """
/* Markdown headings at text scale, told apart by bullets: # •, ## ••, ### •••…
   (doubled class: wins over NiceGUI's 3rem h1). */
.nicegui-markdown.nicegui-markdown :is(h1, h2, h3, h4, h5, h6) {
  font-size:1em; font-weight:700; line-height:1.4; letter-spacing:normal; margin:.7em 0 .3em;
}
.nicegui-markdown.nicegui-markdown :is(h1, h2, h3, h4, h5, h6)::before {
  color:var(--q-primary); margin-right:.4em; letter-spacing:.05em;
}
.nicegui-markdown h1::before { content:"•"; }
.nicegui-markdown h2::before { content:"••"; }
.nicegui-markdown h3::before { content:"•••"; }
.nicegui-markdown h4::before { content:"••••"; }
.nicegui-markdown h5::before { content:"•••••"; }
.nicegui-markdown h6::before { content:"••••••"; }
.nicegui-markdown.nicegui-markdown > :first-child { margin-top:.3em; }
"""
CSS += MARKDOWN_CSS
CSS += f".vr-dim {{ opacity:{DIM_OPACITY}; }}\n"


@contextmanager
def frame(title: str) -> Iterator[None]:
    ui.add_css(CSS)
    ui.page_title(f"{title} · SciReport Analyzer")
    with ui.header().classes("items-center justify-between py-1"):
        with ui.row().classes("items-center gap-4"):
            ui.link("SciReport Analyzer", "/").classes("text-white text-lg font-bold no-underline")
            ui.link(_("People"), "/").classes("text-white no-underline")
            ui.link(_("Venues"), "/venues").classes("text-white no-underline")
            ui.link(_("Settings"), "/settings").classes("text-white no-underline")
            ui.link(_("Help"), "/help").classes("text-white no-underline")
        with ui.row().classes("items-center gap-1"):
            language_menu()
            dark = ui.dark_mode()
            ui.button(icon="dark_mode", on_click=dark.toggle).props("flat round color=white dense")
            from ..stop import quit_button

            quit_button()
    with ui.column().classes("w-full max-w-screen-xl mx-auto p-4 gap-3"):
        from .. import livereload

        livereload.banner()
        from ..keys import get_key

        if not get_key("email"):
            with ui.row().classes(
                "w-full items-center gap-3 bg-orange-1 text-orange-10 rounded p-2"
            ):
                ui.icon("mail")
                ui.label(
                    _("Set your email before fetching anything: it identifies you to the sources.")
                )
                ui.link(_("Settings → API keys"), "/settings?tab=keys")
        yield


def language_menu() -> None:
    """The header's language button: the whole app switches (the page reloads)."""

    def choose(lang: str) -> None:
        i18n.save_language(lang)
        ui.navigate.reload()

    with (
        ui.button(i18n.language().upper())
        .props("flat color=white dense")
        .tooltip(_("Language"))
        .mark("language"),
        ui.menu(),
    ):
        for lang, name in i18n.LANGUAGES.items():
            ui.menu_item(name, on_click=lambda lang=lang: choose(lang)).mark(f"language-{lang}")


class _NewlineBreaks(markdown2.Breaks):
    """Line breaks as in Obsidian: a newline within a paragraph is kept (``<br>``)."""

    name = "newline-breaks"

    def __init__(self, md, options) -> None:
        super().__init__(md, {"on_newline": True, **(options or {})})


_NewlineBreaks.register()

# Markdown of notes: line breaks kept (as in Obsidian), with LaTeX ($…$ inline, $$…$$
# displayed), rendered as MathML.
NOTE_EXTRAS = ["fenced-code-blocks", "tables", "latex", "newline-breaks"]


def chip_text(colour: str | None) -> str:
    """The text colour (black or white) that reads on a chip of ``colour`` (#rgb or #rrggbb)."""
    c = (colour or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", c):
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return text_colour(c) if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else "#fff"


def chip_style(colour: str, selected: bool = True) -> str:
    """The style of a selectable q-chip in ``colour``, its text readable when selected."""
    return f"--q-primary:{colour}" + (f";color:{chip_text(colour)}" if selected else "")


def span(html: str) -> ui.html:
    """Inline trusted HTML (callers escape any user/source text)."""
    return ui.html(html, sanitize=False, tag="span")


def stripes(cat: Category) -> str:
    """The style of a striped category's stripes (a track's in its colour), else ""."""
    if not cat.striped:
        return ""
    gradient = f"repeating-linear-gradient(45deg,{cat.stripe} 0 3px,transparent 3px 7px)"
    return f";background-image:{gradient}"


def track_chip_html(track: str | None) -> str:
    """A track's chip, in its colour (none: the main track, discreet)."""
    if track:
        colour = tracks.colour(track)
        style = f"background:{colour};color:{chip_text(colour)}"
        label = tracks.name(track)
    else:
        style = "background:transparent;color:#8c959f;border:1px dashed #c8d1da"
        label = _("main track")
    return f'<span class="vr-chip" style="{style}">{escape(label)}</span>'


def track_chip(track: str | None, mark: str | None = None) -> ui.html:
    el = span(track_chip_html(track))
    return el.mark(mark) if mark else el


def rank_chip(badge: Badge | None, track: str | None = None, kind: str | None = None) -> ui.html:
    """Rank chip; an unranked venue shows its kind (e.g. "Natl. conf.") when known."""
    cat = category_of(badge, track, kind)
    if cat.base_key.startswith("k_"):
        label = cat.label
    elif not badge:
        label = _("not ranked")
    else:
        label = badge.rank_label if cat.base_key == "other" else cat.label
    if badge and badge.predatory:
        label = f"⚠ {label}"
    cls = "vr-chip vr-track" if cat.striped else "vr-chip"
    colour = cat.colour
    el = span(
        f'<span class="{cls}" style="background:{colour};color:{text_colour(colour)}'
        f'{stripes(cat)}">{escape(label)}</span>'
    )
    if badge:
        with el:
            ui.tooltip(badge_details(badge)).style("white-space:pre-line")
    return el


def badge_details(b: Badge) -> str:
    how = (
        _("manual")
        if b.manual
        else _("forced")
        if b.forced
        else _("corrected")
        if b.corrected
        else _("exact")
        if b.exact
        else _("fuzzy {score}%").format(score=round(b.score * 100))
    )
    lines = [
        b.name or _("(unnamed)"),
        _("matched via {source} ({how})").format(source=b.source, how=how),
    ]
    if b.quartile:
        if b.sjrYear:
            lines.append(
                _("Quartile {quartile} ({year})").format(quartile=b.quartile, year=b.sjrYear)
            )
        else:
            lines.append(_("Quartile {quartile}").format(quartile=b.quartile))
    if latest := b.extra.get("sjrLatest"):
        lines.append(
            _("Latest ({year}): {quartile}").format(
                year=latest[0], quartile=latest[1] or _("no quartile")
            )
        )
    if b.coreRank:
        lines.append(f"CORE {b.coreRank} ({b.coreEdition or ''})")
    elif b.coreHistory and b.coreEdition:
        lines.append(_("Not in {edition} (dropped from CORE)").format(edition=b.coreEdition))
    if latest := b.extra.get("coreLatest"):
        now = f"CORE {latest[1]}" if latest[1] else _("not ranked")
        lines.append(_("Latest listing ({year}): {rank}").format(year=latest[0], rank=now))
    if b.sjr is not None:
        lines.append(f"SJR {b.sjr}")
    if b.hindex is not None:
        lines.append(_("h-index {value}").format(value=b.hindex))
    if b.impactFactor is not None:
        lines.append(_("IF {value}").format(value=b.impactFactor))
    if b.twoYearMeanCitedness is not None:
        lines.append(_("2y mean citedness {value:.2f}").format(value=b.twoYearMeanCitedness))
    if b.findings:
        lines.append(_("Findings track (host conference rank)"))
    if b.predatory:
        lines.append(_("⚠ listed as predatory"))
    return "\n".join(lines)


def source_tag(source: str, archival: bool = False, url: str | None = None) -> ui.element:
    """A coloured source badge; with ``url`` it links to the record on that source.

    It is a wrapper element, so that tooltips and menus can be put in it (``ui.html`` has no
    slot: its children are never rendered)."""
    cls = "vr-src archival" if archival else "vr-src"
    style = f"background:{SOURCE_COLOUR.get(source, '#555')}"
    label = escape(SOURCE_SHORT.get(source, source))
    with ui.element("span") as tag:
        if url:
            span(
                f'<a class="{cls}" style="{style};text-decoration:none;cursor:pointer" '
                f'href="{escape(url)}" target="_blank" rel="noopener">{label}</a>'
            )
        else:
            span(f'<span class="{cls}" style="{style}">{label}</span>')
    return tag


def fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def level_options(levels: list[str]) -> dict[str, str]:
    """Select options showing each level with a short meaning."""

    def short(text: str) -> str:
        return text.split(":", 1)[-1].split(",")[0].split("(")[0].strip().rstrip(".")

    return {lv: f"{lv} — {short(LEVEL_HELP[lv])}" if lv in LEVEL_HELP else lv for lv in levels}


def level_hint(select) -> ui.label:
    """A label explaining the level currently chosen in ``select``."""
    return (
        ui.label()
        .bind_text_from(select, "value", lambda v: LEVEL_HELP.get(v or "", _("Custom level.")))
        .classes("text-xs text-grey")
    )


def level_legend(levels: list[str] | None = None) -> None:
    """Always-visible legend of what each level means."""
    with ui.grid(columns="auto 1fr").classes("gap-x-3 gap-y-0 text-sm mt-1"):
        for level, text in LEVEL_HELP.items():
            if levels is None or level in levels:
                ui.label(level).classes("font-bold")
                ui.label(text)


def author_html(name: str, mark: str | None, note: str | None, colour: str | None = None) -> str:
    """An author name styled by its highlight mark (owner / student / former student /
    potential match / author category ``cat:<id>``, drawn in ``colour``)."""
    text = escape(name)
    title = f' title="{escape(note)}"' if note else ""
    if mark and mark.startswith("cat:"):
        c = escape(colour or "#0969da", quote=True)
        return f'<span class="vr-cat" style="color:{c}"{title}>{text}</span>'
    if mark == "owner":
        return f'<span class="vr-owner">{text}</span>'
    if mark == "student":
        return f'<span class="vr-student"{title}>{text}</span>'
    if mark == "owner?":
        return f'<span class="vr-maybe owner"{title}>{text}</span>'
    if mark == "former":
        return f'<span class="vr-former"{title}>{text}</span>'
    if mark == "student?":
        return f'<span class="vr-maybe"{title}>{text}</span>'
    return text


def merge_direction(target: str, others: list[str]) -> None:
    """Show which venues are merged into which: "A → B (kept)"."""
    with ui.column().classes("gap-1 w-full").mark("merge-direction"):
        for name in others:
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon("remove_circle_outline", color="negative", size="xs")
                ui.label(name).classes("text-sm").mark("merge-from")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("south", color="primary")
            ui.label(_("merged into")).classes("text-xs text-grey")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("check_circle", color="positive", size="xs")
            ui.label(target).classes("text-sm font-bold").mark("merge-into")
            ui.label(_("(kept)")).classes("text-xs text-grey")
