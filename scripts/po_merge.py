"""Fills a catalog's untranslated entries from JSON files ({msgid: msgstr}; plural entries:
"singular\\u0000plural": [msgstr...]): ``uv run scripts/po_merge.py fr a.json b.json…``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from babel.messages.pofile import read_po, write_po

LOCALES = Path(__file__).parent.parent / "src" / "sci_report_analyzer" / "locales"


def main() -> None:
    lang, *files = sys.argv[1:]
    path = LOCALES / lang / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as f:
        cat = read_po(f, locale=lang)
    tr: dict = {}
    for file in files:
        tr.update(json.loads(Path(file).read_text(encoding="utf-8")))
    filled = 0
    for msg in cat:
        if not msg.id:
            continue
        key = "\0".join(msg.id) if isinstance(msg.id, tuple) else msg.id
        if key in tr and not (all(msg.string) if msg.pluralizable else msg.string):
            msg.string = tuple(tr[key]) if msg.pluralizable else tr[key]
            filled += 1
    missing = [m.id for m in cat if m.id and not (all(m.string) if m.pluralizable else m.string)]
    with path.open("wb") as f:
        write_po(f, cat, omit_header=False, sort_output=True, ignore_obsolete=True)
    print(f"{filled} filled, {len(missing)} still untranslated")
    for m in missing:
        print("  ", m)


if __name__ == "__main__":
    main()
