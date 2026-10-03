"""Translations: the French catalog covers the UI, and the language switches the app."""

import re
from pathlib import Path

import pytest
from babel.messages.extract import extract_from_dir
from nicegui.testing import User

from sci_report_analyzer import i18n

PKG = Path(i18n.__file__).parent
KEYWORDS = {"_": None, "N_": None, "ngettext": (1, 2)}


@pytest.fixture
def english():
    yield
    i18n.set_language("en")


def _source_msgids() -> set[str]:
    return {
        msg if isinstance(msg, str) else msg[0]
        for _file, _line, msg, _comments, _ctx in extract_from_dir(
            str(PKG), method_map=[("**.py", "python")], keywords=KEYWORDS
        )
    }


def test_every_string_has_a_french_translation(english) -> None:
    """Run scripts/i18n.sh, then translate the new entries of locales/fr/…/messages.po."""
    fr = i18n.catalog("fr")
    missing = sorted(m for m in _source_msgids() if fr.gettext(m) == m and m.strip())
    # (a few strings are the same in French: listed in the catalog with msgstr == msgid)
    po = (PKG / "locales/fr/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    missing = [m for m in missing if f'msgstr "{m}"' not in po]
    assert not missing, f"{len(missing)} untranslated strings, e.g. {missing[:5]}"


def test_french_keeps_the_placeholders() -> None:
    from babel.messages.pofile import read_po

    with (PKG / "locales/fr/LC_MESSAGES/messages.po").open("rb") as f:
        cat = read_po(f, locale="fr")
    field = re.compile(r"\{[^{}]*\}")
    for msg in cat:
        if not msg.id or not msg.string:
            continue
        ids = msg.id if isinstance(msg.id, tuple) else (msg.id,)
        strs = msg.string if isinstance(msg.string, tuple) else (msg.string,)
        want = set(field.findall(ids[-1])) | set(field.findall(ids[0]))
        for s in strs:
            assert set(field.findall(s)) <= want, (msg.id, s)


def test_labels_translate_when_read(english) -> None:
    labels = i18n.Labels(settings=i18n.N_("Settings"))
    assert labels["settings"] == "Settings"
    i18n.set_language("fr")
    assert labels["settings"] == "Paramètres"
    assert dict(labels) == {"settings": "Paramètres"}


@pytest.mark.nicegui_main_file("tests/app_main.py")
async def test_language_menu_switches_the_app(user: User, english) -> None:
    await user.open("/help")
    await user.should_see("What SciReport Analyzer does")
    user.find(marker="language").click()
    user.find(marker="language-fr").click()
    await user.open("/help")
    await user.should_see("Paramètres")
    assert i18n.language() == "fr"
    i18n.save_language("en")
