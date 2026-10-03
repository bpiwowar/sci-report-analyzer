"""Shared page frame and small UI helpers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from html import escape

import markdown2
from nicegui import ui

from ..ranking.badge import LEVEL_HELP, Badge, category_of, text_colour

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
CSS += f".vr-dim {{ opacity:{DIM_OPACITY}; }}\n"


@contextmanager
def frame(title: str) -> Iterator[None]:
    ui.add_css(CSS)
    ui.page_title(f"{title} · SciReport Analyzer")
    with ui.header().classes("items-center justify-between py-1"):
        with ui.row().classes("items-center gap-4"):
            ui.link("SciReport Analyzer", "/").classes("text-white text-lg font-bold no-underline")
            ui.link("People", "/").classes("text-white no-underline")
            ui.link("Venues", "/venues").classes("text-white no-underline")
            ui.link("Settings", "/settings").classes("text-white no-underline")
            ui.link("Help", "/help").classes("text-white no-underline")
        with ui.row().classes("items-center gap-1"):
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
                    "Set your email before fetching anything: it identifies you to the sources."
                )
                ui.link("Settings → API keys", "/settings?tab=keys")
        yield


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


def rank_chip(badge: Badge | None, track: str | None = None, kind: str | None = None) -> ui.html:
    """Rank chip; an unranked venue shows its kind (e.g. "Natl. conf.") when known."""
    cat = category_of(badge, track, kind)
    if cat.base_key.startswith("k_"):
        label = cat.label
    elif not badge:
        label = "not ranked"
    else:
        label = badge.rank_label if cat.base_key == "other" else cat.label
    if badge and badge.predatory:
        label = f"⚠ {label}"
    cls = "vr-chip vr-track" if cat.striped else "vr-chip"
    colour = cat.colour
    el = span(
        f'<span class="{cls}" style="background:{colour};color:{text_colour(colour)}">'
        f"{escape(label)}</span>"
    )
    if badge:
        with el:
            ui.tooltip(badge_details(badge)).style("white-space:pre-line")
    return el


def badge_details(b: Badge) -> str:
    how = (
        "manual"
        if b.manual
        else "forced"
        if b.forced
        else "corrected"
        if b.corrected
        else "exact"
        if b.exact
        else f"fuzzy {round(b.score * 100)}%"
    )
    lines = [b.name or "(unnamed)", f"matched via {b.source} ({how})"]
    if b.quartile:
        lines.append(f"Quartile {b.quartile}" + (f" ({b.sjrYear})" if b.sjrYear else ""))
    if latest := b.extra.get("sjrLatest"):
        lines.append(f"Latest ({latest[0]}): {latest[1] or 'no quartile'}")
    if b.coreRank:
        lines.append(f"CORE {b.coreRank} ({b.coreEdition or ''})")
    elif b.coreHistory and b.coreEdition:
        lines.append(f"Not in {b.coreEdition} (dropped from CORE)")
    if latest := b.extra.get("coreLatest"):
        now = f"CORE {latest[1]}" if latest[1] else "not ranked"
        lines.append(f"Latest listing ({latest[0]}): {now}")
    if b.sjr is not None:
        lines.append(f"SJR {b.sjr}")
    if b.hindex is not None:
        lines.append(f"h-index {b.hindex}")
    if b.impactFactor is not None:
        lines.append(f"IF {b.impactFactor}")
    if b.twoYearMeanCitedness is not None:
        lines.append(f"2y mean citedness {b.twoYearMeanCitedness:.2f}")
    if b.findings:
        lines.append("Findings track (host conference rank)")
    if b.predatory:
        lines.append("⚠ listed as predatory")
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
        .bind_text_from(select, "value", lambda v: LEVEL_HELP.get(v or "", "Custom level."))
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
            ui.label("merged into").classes("text-xs text-grey")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("check_circle", color="positive", size="xs")
            ui.label(target).classes("text-sm font-bold").mark("merge-into")
            ui.label("(kept)").classes("text-xs text-grey")
