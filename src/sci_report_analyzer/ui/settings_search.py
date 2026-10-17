"""The search of the settings page: the settings whose text (a header, a label, a help
text, a tooltip…) holds what is typed. Read from the screens as built (every tab, shown or
not: no index to maintain); the tabs without any hidden, the others counting theirs; in a
tab, the sections (a header and what follows it, up to the next one) without any hidden,
what matches highlighted. Cleared: everything back."""

from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import ui
from nicegui.element import Element

from ..i18n import _, ngettext

# A section's header in a screen (see the screens: ``ui.label(...).classes("text-lg")``).
HEADER = "text-lg"
HIT = "vr-search-hit"

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
    badges: dict[str, ui.badge] = field(default_factory=dict)

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
        self.hidden.clear()
        self.hits.clear()
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

    def search(self, name: str, query: str) -> int:
        """In a screen: what matches highlighted, the sections without any hidden (all of it
        shown when its tab's name matches); returns how many matched."""
        panel = self.panels[name]
        hits = []
        for el in panel.descendants():
            if el is panel or not matches(el, query):
                continue
            # (a tooltip: what it is on)
            hits.append(el.parent_slot.parent if isinstance(el, ui.tooltip) else el)
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
        for part in sections(panel):
            if not any(id(el) in holding for el in part):
                for el in part:
                    self.hide(el)
        return len(hits)
