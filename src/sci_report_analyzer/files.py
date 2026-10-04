"""File helpers."""

from __future__ import annotations

from pathlib import Path


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write a file whole or not at all (a temporary file renamed): never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    tmp.replace(path)
