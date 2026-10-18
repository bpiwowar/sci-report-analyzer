"""The address of a page, following what it shows (its tab, its filters…): each change a step
in the browser's history, so that Back and Forward go through them; a burst of typing (a
search) one step. Back or Forward to another address: the page built again from it
(reloaded; with ``on_pop``, shown again in place, e.g. the settings and their unsaved
edits), the address taken as it is (``popped``: e.g. no filter in it, none)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request
from nicegui import context, ui
from nicegui.element import Element

# (the cookie set on Back or Forward, for the page loaded then)
POPPED = "vr_nav"
# (a search typed within this long after the last change: the same step)
TYPING_MS = 2000

SCRIPT = f"""<script>
window.vrNav = {{
  shown: location.pathname + location.search,  // (the address the page shows)
  typed: 0,
  soft: false,  // (Back or Forward: shown again in place, see on_pop)
  // 'push': a step of its own; 'replace': in place of the current one; 'typing': a step for
  // a burst of typing (the first change pushed, the next ones within it replacing it).
  set(url, how) {{
    const u = new URL(url, location.href), now = Date.now();
    const target = u.pathname + u.search;
    if (how === 'typing') {{
      how = now - this.typed < {TYPING_MS} ? 'replace' : 'push';
      this.typed = now;
    }} else {{
      this.typed = 0;
    }}
    this.shown = target;
    if (target === location.pathname + location.search) return;
    if (how === 'push') history.pushState(null, '', target);
    else history.replaceState(history.state, '', target);
  }},
}};
document.cookie = '{POPPED}=; path=/; max-age=0';
window.addEventListener('popstate', () => {{
  const now = location.pathname + location.search;
  if (now === vrNav.shown) return;  // (a hash, a PDF's own steps)
  vrNav.shown = now;
  vrNav.typed = 0;
  if (vrNav.soft) {{ emitEvent('vr-nav', now); return; }}
  document.cookie = '{POPPED}=1; path=/; max-age=30; samesite=lax';
  location.reload();
}});
</script>"""


def script() -> None:
    """On a page (see theme.frame)."""
    ui.add_head_html(SCRIPT)


def address(url: str, how: str = "push", *, on: Element | None = None) -> None:
    """Show ``url`` in the address bar, without reloading (``how``: see SCRIPT's set);
    ``on``: an element of the page (e.g. when the one changed was deleted meanwhile)."""
    with contextlib.suppress(Exception):  # e.g. the page was left meanwhile
        client = on.client if on is not None else context.client
        client.run_javascript(f"window.vrNav?.set({json.dumps(url)}, {json.dumps(how)})")


def popped(request: Request) -> bool:
    """Loaded by Back or Forward: its address is the whole state (nothing as left)."""
    return request.cookies.get(POPPED) == "1"


def on_pop(handler: Callable[[dict[str, str]], None]) -> None:
    """Back or Forward within the page: ``handler`` with its new query, instead of a reload."""
    ui.on("vr-nav", lambda e: handler(dict(parse_qsl(urlsplit(str(e.args)).query))))
    with contextlib.suppress(Exception):
        ui.run_javascript("if (window.vrNav) vrNav.soft = true")


def csv(value: str | None) -> list[str]:
    """A list in the address (``a,b``)."""
    return [x for x in (value or "").split(",") if x]


def sort_param(pagination: dict | None) -> str | None:
    """A table's sort in the address (``pubs``, ``-pubs``: descending; none: not sorted)."""
    by = (pagination or {}).get("sortBy")
    return f"{'-' if pagination.get('descending') else ''}{by}" if by else None


def pagination_of(sort: str | None, rows: int) -> dict:
    """A table's pagination (``rows`` a page), sorted as ``sort`` (see sort_param)."""
    out: dict = {"rowsPerPage": rows}
    if sort:
        out |= {"sortBy": sort.lstrip("-"), "descending": sort.startswith("-")}
    return out
