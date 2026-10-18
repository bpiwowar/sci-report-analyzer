"""The pages' addresses in the browser's history: each change a step (pushState), a burst of
typing one; Back or Forward, the view of that address (see ui/nav.py)."""

import json
import re
import shutil
import subprocess

import pytest
from helpers import add_source, make_person, pub
from nicegui import Client, ui
from nicegui.testing import User

from sci_report_analyzer import folders
from sci_report_analyzer.ui import nav
from sci_report_analyzer.urls import folder_url, person_url

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")

_SET = re.compile(r'window\.vrNav\?\.set\(("(?:[^"\\]|\\.)*"), "(\w+)"\)')


@pytest.fixture
def addresses(monkeypatch) -> list[tuple[str, str]]:
    """The addresses shown (url, how), as sent to the browser."""
    out: list[tuple[str, str]] = []
    run = Client.run_javascript

    def recorded(self, code, **kw):
        if m := _SET.fullmatch(code):
            out.append((json.loads(m[1]), m[2]))
        return run(self, code, **kw)

    monkeypatch.setattr(Client, "run_javascript", recorded)
    return out


def _pop(user: User, url: str) -> None:
    """Back or Forward to ``url``, on a page shown again in place (see nav.on_pop)."""
    layout = user.client.layout
    for lid, listener in list(layout._event_listeners.items()):
        if listener.type == "vrNav":  # (as NiceGUI names it)
            layout._handle_event({"listener_id": lid, "args": url})


def _settings_tabs(user: User) -> ui.tabs:
    return next(t for t in user.find(kind=ui.tabs).elements if "vr-settings-nav" in t.classes)


async def test_settings_tab_in_the_address(user: User, addresses) -> None:
    await user.open("/settings?tab=data")
    assert _settings_tabs(user).value == "data"
    assert addresses[-1] == ("/settings?tab=data", "replace")
    # A tab chosen: a step in the history.
    user.find(marker="settings-io").click()
    assert addresses[-1] == ("/settings?tab=io", "push")
    user.find(marker="settings-sources").click()
    assert addresses[-1] == ("/settings", "push")  # (the default tab: left out)
    # Back: the tab of the address, in place (no step added).
    n = len(addresses)
    _pop(user, "/settings?tab=io")
    assert _settings_tabs(user).value == "io"
    assert len(addresses) == n
    # A tab that moved: the address completed.
    await user.open("/settings?tab=skeletons")
    assert _settings_tabs(user).value == "folders"
    assert addresses[-1] == ("/settings?tab=folders", "replace")


async def test_settings_search_in_the_address(user: User, addresses) -> None:
    await user.open("/settings?tab=data")
    user.find(marker="settings-search").type("cleaning")
    assert addresses[-1][1] == "typing"
    assert "q=cleaning" in addresses[-1][0]
    await user.open("/settings?tab=rules&q=cleaning")
    assert _settings_tabs(user).value == "rules"
    assert addresses[-1] == ("/settings?tab=rules&q=cleaning", "replace")


def _person() -> int:
    pid = make_person("Ann Example")
    add_source(
        pid,
        "dblp",
        "x/9",
        [
            pub("a", "Neural search basics", 2019, "Journal of Made-up Results"),
            pub("b", "A workshop contribution", 2021, "Workshop on Imaginary Data"),
        ],
    )
    return pid


async def test_publication_filters_round_trip(user: User, addresses) -> None:
    pid = _person()
    url = f"{person_url(pid)}?from=2018&to=2020&q=neural"
    await user.open(url)
    await user.should_see("Neural search basics")
    await user.should_not_see("A workshop contribution")
    # The address given is that of the view (nothing to complete).
    assert addresses[-1] == (url, "replace")
    # A filter changed: a step in the history.
    user.find(kind=ui.switch, content="hide preprints").click()
    assert addresses[-1] == (url + "&pre=1", "push")
    # Typing a search: a step for a burst of typing.
    user.find(marker="pub-search").type(" basics")
    assert addresses[-1] == (f"{person_url(pid)}?from=2018&to=2020&q=neural+basics&pre=1", "typing")


async def test_person_tab_is_a_step(user: User, addresses) -> None:
    pid = _person()
    await user.open(person_url(pid))
    await user.should_see("Neural search basics")
    tabs = next(t for t in user.find(kind=ui.tabs).elements if t.value == "publications")
    tabs.set_value("starred")
    assert addresses[-1] == (f"{person_url(pid)}?tab=starred", "push")


async def test_back_gives_the_address_view(user: User, addresses) -> None:
    """Loaded by Back or Forward: the address is the whole view (the period and the PDFs
    left open elsewhere not taken)."""
    pid = _person()
    folder = folders.save_folder(None, "Imaginary Committee")
    folders.add_person(folder, pid)
    await user.open(person_url(pid, folders.folders()[0].members[0].period_id))
    await user.should_see("Neural search basics")
    # Opened without a period: the one last chosen.
    await user.open(person_url(pid))
    await user.should_see("Neural search basics")
    assert addresses[-1][0].startswith("/person/imaginary-committee/")
    # The same, by Back: none.
    user.http_client.cookies.set(nav.POPPED, "1")
    try:
        await user.open(person_url(pid))
        await user.should_see("Neural search basics")
    finally:
        user.http_client.cookies.delete(nav.POPPED)
    assert addresses[-1] == (person_url(pid), "replace")


async def test_reports_filters_round_trip(user: User, addresses) -> None:
    a, b = make_person("Ann Example"), make_person("Bob Sample")
    folder = folders.save_folder(None, "Imaginary Committee")
    folders.set_tags(folders.add_person(folder, a), ["shortlisted"])
    folders.add_person(folder, b)
    url = folder_url(folder) + "&tags=shortlisted"
    await user.open(url)
    assert addresses[-1] == (url, "replace")
    select = user.find(marker=f"folder-tag-filter-{folder}").elements.pop()
    assert select.value == ["shortlisted"]
    select.set_value([])
    assert addresses[-1] == (folder_url(folder), "push")
    # All people: the search and the switch.
    url = "/?folder=0&q=ann&orphans=1"
    await user.open(url)
    assert addresses[-1] == (url, "replace")
    assert user.find(marker="people-filter").elements.pop().value == "ann"
    user.find(marker="orphans-only").click()
    assert addresses[-1] == ("/?folder=0&q=ann", "push")
    # The last folder, given by none: in the address.
    await user.open(folder_url(folder))
    await user.open("/")
    assert addresses[-1] == (folder_url(folder), "replace")


async def test_venues_filters_round_trip(user: User, addresses) -> None:
    _person()
    url = "/venues?tab=journals&q=made&sort=-pubs"
    await user.open(url)
    await user.should_see(marker="venue-list-filter-journals")
    assert addresses[-1] == (url, "replace")
    assert user.find(marker="venue-list-filter-journals").elements.pop().value == "made"
    user.find(marker="venue-list-to-check-journals").click()
    assert addresses[-1] == ("/venues?tab=journals&q=made&check=journals&sort=-pubs", "push")


_JS = """
const steps = [];
const loc = {pathname: '/settings', search: '', hash: ''};
global.location = loc;
global.document = {cookie: ''};
const listeners = {};
global.window = global;
global.addEventListener = (t, f) => { listeners[t] = f; };
global.URL = URL;
global.emitEvent = (name, arg) => steps.push(['emit', name, arg]);
loc.reload = () => steps.push(['reload', document.cookie]);
const go = (how) => (s, t, url) => {
  const u = new URL(url, 'http://x'); loc.pathname = u.pathname; loc.search = u.search;
  steps.push([how, url]);
};
global.history = {state: null, pushState: go('push'), replaceState: go('replace')};
Object.defineProperty(loc, 'href', {get: () => 'http://x' + loc.pathname + loc.search});
eval(SCRIPT);
vrNav.set('/settings?tab=io', 'push');
vrNav.set('/settings?tab=io', 'push');  // (the same: nothing)
vrNav.set('/settings?tab=io&q=a', 'typing');
vrNav.set('/settings?tab=io&q=ab', 'typing');
vrNav.set('/settings?tab=data', 'replace');
loc.search = '?tab=io';
listeners.popstate();
vrNav.soft = true;
loc.search = '?tab=data';
listeners.popstate();
loc.hash = '#x';
listeners.popstate();  // (the same address: nothing)
console.log(JSON.stringify(steps));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_history_steps_in_the_browser() -> None:
    script = nav.SCRIPT.removeprefix("<script>").removesuffix("</script>")
    out = subprocess.run(
        ["node", "-e", f"const SCRIPT = {json.dumps(script)};\n{_JS}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == [
        ["push", "/settings?tab=io"],
        ["push", "/settings?tab=io&q=a"],
        ["replace", "/settings?tab=io&q=ab"],
        ["replace", "/settings?tab=data"],
        ["reload", f"{nav.POPPED}=1; path=/; max-age=30; samesite=lax"],
        ["emit", "vr-nav", "/settings?tab=data"],
    ]
