"""Checks a folder of shared settings (a group's repository), e.g. in its CI.

    sci-report-analyzer-shared check [FOLDER] [--fix]

Exits with 1 when a file is invalid (JSON, fields, regexes, levels that overlap, a venue
named but missing) or not written canonically; ``--fix`` rewrites the files canonically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import shared


def read_folder(folder: Path) -> shared.Files:
    files = {}
    for name in shared.MANAGED:
        path = folder / name
        if path.is_file():
            files[name] = path.read_text(encoding="utf-8")
    for path in sorted((folder / shared.VENUES_DIR).glob("*.json")):
        files[f"{shared.VENUES_DIR}/{path.name}"] = path.read_text(encoding="utf-8")
    return files


def fix(folder: Path, files: shared.Files) -> int:
    """Rewrite the files canonically; returns how many changed."""
    canonical = shared.files_from_items(shared.items_from_files(files))
    n = 0
    for path, text in canonical.items():
        if files.get(path) != text:
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="check the files of shared settings")
    p.add_argument("folder", nargs="?", type=Path, default=Path())
    p.add_argument("--fix", action="store_true", help="rewrite the files canonically")
    args = parser.parse_args(argv)
    files = read_folder(args.folder)
    if not files:
        print(f"{args.folder}: no shared settings found", file=sys.stderr)
        return 1
    if args.fix:
        try:
            n = fix(args.folder, files)
        except shared.FormatError as err:
            print(err, file=sys.stderr)
            return 1
        if n:
            print(f"{n} file(s) rewritten")
        files = read_folder(args.folder)
    problems = shared.check(files)
    for line in problems:
        print(line, file=sys.stderr)
    if not problems:
        print(f"{len(files)} file(s): OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
