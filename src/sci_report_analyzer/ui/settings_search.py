"""The search of the settings page: the settings whose text (a header, a label, a help
text, a tooltip…) holds what is typed. Read from the screens as built (every tab, shown or
not: no index to maintain); the tabs without any hidden, the others counting theirs; in a
tab, the sections (a header and what follows it, up to the next one) without any hidden,
what matches highlighted; a screen without headers, its items (a field, a row, a card…)
without any hidden. A dialog opened from a screen (see searched): its content searched as
what opens it, what matched said next to it. Cleared: everything back."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from nicegui import ui
from nicegui.element import Element
from nicegui.functions.refreshable import RefreshableContainer

from ..i18n import _, ngettext

# A section's header in a screen (see the screens: ``ui.label(...).classes("text-lg")``).
HEADER = "text-lg"
HIT = "vr-search-hit"
# What holds a screen's items, one below the other (narrowed: see narrow).
LISTS = (RefreshableContainer, ui.column, ui.list, ui.tab_panel)

ui.add_css(
    f"""
.vr-settings-panels .{HEADER} {{
  color:var(--q-primary); font-weight:500;
  border-bottom:1px solid color-mix(in srgb, var(--q-primary) 30%, transparent);
  padding-bottom:2px;
}}
.vr-settings-group {{ color:var(--q-primary); }}
.{HIT} {{
  background:rgba(255, 202, 40, .3); box-shadow:0 0 0 3px rgba(255, 202, 40, .3);
  border-radius:3px;
}}
""",
    shared=True,
)


def own_texts(el: Element) -> list[str]:
    """What an element says itself (not its children's, nor a field's value): its text, its
    label (a field's), its Markdown."""
    out = []
    if isinstance(text := getattr(el, "text", None), str):
        out.append(text)
    if isinstance(label := el.props.get("label"), str):
        out.append(label)
    if isinstance(content := getattr(el, "content", None), str) and isinstance(el, ui.markdown):
        out.append(content)
    return out


def matches(el: Element, query: str) -> bool:
    return any(query in t.casefold() for t in own_texts(el))


def searched(opener: Element, build: Callable[[], object]) -> Element:
    """The content of a dialog searched as what opens it (``opener``): ``build`` makes it
    (the dialog's content, not the dialog), out of sight, when searched."""
    opener.searched_dialog = build  # type: ignore[attr-defined]
    return opener


def dialog_texts(opener: Element) -> list[str]:
    """What the dialog an element opens says (see searched), its text fields' values too
    (e.g. a category's name): its content built hidden next to it, read, then deleted."""
    with opener.parent_slot.parent:
        box = ui.element()
        box.set_visibility(False)
        with box:
            opener.searched_dialog()  # type: ignore[attr-defined]
    texts = [
        t
        for el in box.descendants()
        if el is not box
        for t in (
            *own_texts(el),
            *([el.value] if isinstance(el, (ui.input, ui.textarea)) and el.value else []),
        )
        if isinstance(t, str)
    ]
    box.delete()
    return [t for t in texts if t.strip()]


def snippet(text: str, query: str, width: int = 40) -> str:
    """Where a text holds what is searched (about ``width`` characters around it)."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    at = max(text.casefold().find(query) - (width - len(query)) // 2, 0)
    start = max(min(at, len(text) - width), 0)
    return (
        ("…" if start else "")
        + text[start : start + width]
        + ("…" if start + width < len(text) else "")
    )


def target(el: Element) -> Element:
    """What a hit is on: a tooltip, the element it is on (its target), else its parent."""
    if isinstance(el, ui.tooltip):
        aim = el.props.get("target")
        found = (
            el.client.elements.get(int(aim[2:]))
            if isinstance(aim, str) and aim.startswith("#c") and aim[2:].isdigit()
            else None
        )
        return found if found is not None else el.parent_slot.parent
    return el


def items(parent: Element) -> list[list[Element]]:
    """The items listed by an element (see LISTS): each child, with what goes with it (its
    tooltips, what follows it without a text of its own, e.g. a field under its label, and
    its help: a small label under it)."""
    out: list[list[Element]] = []
    for child in parent.default_slot.children:
        attached = isinstance(child, (ui.tooltip, ui.timer)) or (
            isinstance(child, ui.label) and "text-xs" in child.classes
        )
        unlabelled = _field(child) and not any(t.strip() for t in own_texts(child))
        if out and (attached or unlabelled):
            out[-1].append(child)
        else:
            out.append([child])
    return out


def _field(el: Element) -> bool:
    return isinstance(el, (ui.input, ui.textarea, ui.select, ui.number, ui.codemirror))


def sections(panel: Element) -> list[list[Element]]:
    """A screen's parts: in each element holding headers, what is before the first one, then
    each header with what follows it (up to the next one)."""
    parents = {id(h.parent_slot.parent): h.parent_slot.parent for h in _headers(panel)}
    out: list[list[Element]] = []
    for parent in parents.values():
        part: list[Element] = []
        for child in parent.default_slot.children:
            if HEADER in child.classes and part:
                out.append(part)
                part = []
            part.append(child)
        if part:
            out.append(part)
    return out


def _headers(panel: Element) -> list[Element]:
    return [
        e
        for e in panel.descendants()
        if HEADER in e.classes and e.parent_slot is not None and isinstance(e, ui.label)
    ]


@dataclass
class Search:
    """The search over the screens (``panels``, by tab), its tabs (``tabs``, the nav's) and
    their groups' titles (``groups``: each with its tabs)."""

    tab_bar: ui.tabs
    panels: dict[str, Element]
    tabs: dict[str, ui.tab]
    labels: dict[str, str]  # (each tab's, translated)
    groups: list[tuple[ui.label, list[str]]]
    hidden: list[Element] = field(default_factory=list)
    hits: list[Element] = field(default_factory=list)
    notes: list[Element] = field(default_factory=list)  # (what matched in dialogs)
    badges: dict[str, ui.badge] = field(default_factory=dict)
    # (the texts of the dialogs, by what opens them: read once while searching)
    dialogs: dict[int, list[str]] = field(default_factory=dict)

    def box(self) -> None:
        """The search field (and below it, what it found)."""
        for name, tab in self.tabs.items():
            with tab:
                self.badges[name] = (
                    ui.badge().props("color=amber-8 floating").mark(f"settings-search-count-{name}")
                )
                self.badges[name].set_visibility(False)
        self.input = (
            ui.input(placeholder=_("Search the settings"), on_change=lambda: self.run())
            .props("dense outlined clearable debounce=250")
            .classes("w-full px-2")
            .mark("settings-search")
        )
        with self.input.add_slot("prepend"):
            ui.icon("search")
        self.found = ui.label().classes("text-xs text-grey px-2").mark("settings-search-found")

    def clear(self) -> None:
        """Everything shown again, nothing highlighted."""
        for el in self.hidden:
            if not el.is_deleted:
                el.set_visibility(True)
        for el in self.hits:
            if not el.is_deleted:
                el.classes(remove=HIT)
        for el in self.notes:
            if not el.is_deleted:
                el.delete()
        self.hidden.clear()
        self.hits.clear()
        self.notes.clear()
        for tab in self.tabs.values():
            tab.set_visibility(True)
        for label, _names in self.groups:
            label.set_visibility(True)
        for badge in self.badges.values():
            badge.set_visibility(False)
        self.found.text = ""

    def hide(self, el: Element) -> None:
        if el.visible and not isinstance(el, ui.timer):
            el.set_visibility(False)
            self.hidden.append(el)

    def run(self) -> None:
        self.clear()
        query = (self.input.value or "").strip().casefold()
        if not query:
            self.dialogs.clear()  # (read again on the next search: they may have changed)
            return
        counts = {name: self.search(name, query) for name in self.panels}
        for name, n in counts.items():
            self.tabs[name].set_visibility(n > 0)
            self.badges[name].text = str(n)
            self.badges[name].set_visibility(n > 0)
        for label, names in self.groups:
            label.set_visibility(any(counts.get(n) for n in names))
        found = [n for n, c in counts.items() if c]
        if found and self.tab_bar.value not in found:
            self.tab_bar.set_value(found[0])
        self.found.text = (
            ngettext("Found in {n} screen", "Found in {n} screens", len(found)).format(n=len(found))
            if found
            else _("No setting matches")
        )

    def in_dialog(self, opener: Element, query: str) -> list[str]:
        """What matches in the dialog an element opens (see searched)."""
        if (texts := self.dialogs.get(opener.id)) is None:
            texts = self.dialogs[opener.id] = dialog_texts(opener)
        return [t for t in texts if query in t.casefold()]

    def note(self, opener: Element, found: list[str], query: str) -> None:
        """Next to what opens a dialog: what matched in it."""
        shown = list(dict.fromkeys(snippet(t, query) for t in found))
        text = "; ".join(shown[:3]) + ("; …" if len(shown) > 3 else "")
        parent = opener.parent_slot.parent
        with parent:
            note = (
                ui.label(_("In its dialog: {found}").format(found=text))
                .classes("text-xs text-amber-10 min-w-0 truncate")
                .mark("settings-search-dialog")
            )
        note.move(parent, parent.default_slot.children.index(opener) + 1)
        with note:
            ui.tooltip(text)
        self.notes.append(note)

    def search(self, name: str, query: str) -> int:
        """In a screen: what matches highlighted (what opens a dialog, when its content
        does), the sections without any hidden, a screen without headers narrowed to its
        items with one (all of it shown when its tab's name matches); returns how many
        matched."""
        panel = self.panels[name]
        hits = []
        for el in list(panel.descendants()):  # (a dialog's content built while searching)
            if el is panel:
                continue
            if matches(el, query):
                hits.append(target(el))
            elif hasattr(el, "searched_dialog") and (found := self.in_dialog(el, query)):
                hits.append(el)
                self.note(el, found, query)
        hits = list({id(el): el for el in hits}.values())
        for el in hits:
            el.classes(add=HIT)
            self.hits.append(el)
        if query in self.labels[name].casefold():
            return len(hits) or 1
        holding = set()  # (the hits and the elements they are in)
        for el in hits:
            while el is not None and el is not panel and id(el) not in holding:
                holding.add(id(el))
                el = el.parent_slot.parent if el.parent_slot is not None else None
        parts = sections(panel)
        for part in parts:
            if not any(id(el) in holding for el in part):
                for el in part:
                    self.hide(el)
        if not parts:
            self.narrow(panel, holding, {id(el) for el in hits})
        return len(hits)

    def narrow(self, parent: Element, holding: set[int], hits: set[int]) -> None:
        """The items of an element (see items) without any hidden; in those listing others
        (see LISTS, not a hit themselves), theirs too."""
        for item in items(parent):
            if not any(id(el) in holding for el in item):
                for el in item:
                    self.hide(el)
            elif len(item) == 1 and isinstance(item[0], LISTS) and id(item[0]) not in hits:
                self.narrow(item[0], holding, hits)
