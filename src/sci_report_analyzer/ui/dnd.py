"""Drag and drop: an item (of a kind, by its id) dragged onto another one, dropped before it,
after it (its top or bottom edge) or onto it (its middle; anywhere on a whole drop zone)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

# (window.vrDnd: also used by the Vue templates, e.g. the venues table's rows)
ui.add_head_html(
    """<script>
window.vrDnd = {
  zone(ev, zoned) {
    if (!zoned) return 'on';
    const r = ev.currentTarget.getBoundingClientRect(), f = (ev.clientY - r.top) / r.height;
    return f < 0.3 ? 'before' : f > 0.7 ? 'after' : 'on';
  },
  clear(ev) { ev.currentTarget.classList.remove('vr-drop-before', 'vr-drop-on', 'vr-drop-after'); },
  start(ev, data) {
    ev.stopPropagation();
    ev.dataTransfer.setData('text/plain', data);
    ev.dataTransfer.effectAllowed = 'move';
  },
  over(ev, zoned) {
    ev.preventDefault();
    vrDnd.clear(ev);
    ev.currentTarget.classList.add('vr-drop-' + vrDnd.zone(ev, zoned));
  },
  // The item dropped (of this kind, else null): {id, where}.
  drop(ev, kind, zoned) {
    ev.preventDefault();
    vrDnd.clear(ev);
    const [k, id] = ev.dataTransfer.getData('text/plain').split(':');
    return k === kind ? {id: +id, where: vrDnd.zone(ev, zoned)} : null;
  },
};
</script>""",
    shared=True,
)


def draggable(el: ui.element, kind: str, item_id: int) -> None:
    """``el`` dragged: the item ``item_id`` of ``kind`` (e.g. "category")."""
    el.props('draggable="true"')
    el.on("dragstart", js_handler=f"(ev) => vrDnd.start(ev, '{kind}:{item_id}')")


def drop_zone(
    el: ui.element,
    kind: str,
    on_drop: Callable[[int, str], Any],
    *,
    middle: str = "on",
    zoned: bool = True,
) -> None:
    """Items of ``kind`` dropped onto ``el``: ``on_drop(id, where)``, where is "before" or
    "after" (its top or bottom edge), else ``middle``; not ``zoned``: always ``middle``."""
    z = "true" if zoned else "false"

    def dropped(e) -> None:
        if isinstance(e.args, dict) and e.args.get("id"):
            where = e.args.get("where") or "on"
            on_drop(int(e.args["id"]), middle if where == "on" else where)

    el.on("dragover", js_handler=f"(ev) => vrDnd.over(ev, {z})")
    el.on("dragleave", js_handler="(ev) => vrDnd.clear(ev)")
    el.on(
        "drop",
        dropped,
        js_handler=f"(ev) => {{ const d = vrDnd.drop(ev, '{kind}', {z}); if (d) emit(d); }}",
    )


def vue_attrs(kind: str, item_id: str, on_drop: str) -> str:
    """The same, in a Vue template: ``item_id`` (an expression) dragged and dropped onto
    (anywhere), ``on_drop`` (a statement) run with ``d``: the item dropped, ``{id}``."""
    return (
        f'draggable="true" @dragstart="e => vrDnd.start(e, \'{kind}:\' + {item_id})" '
        '@dragover="e => vrDnd.over(e, false)" @dragleave="e => vrDnd.clear(e)" '
        f"@drop=\"e => {{ const d = vrDnd.drop(e, '{kind}', false); if (d) {{ {on_drop} }} }}\""
    )
