"""The rules in force (detection rules, tracks): those of the settings, compiled on first
use, which a preview can replace for a while."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any


class InForce[T]:
    """What ``compile`` makes of the rules of a source (``use``), compiled on first use."""

    def __init__(self, compile: Callable[[Iterable[Any]], T]) -> None:
        self._compile = compile
        self._source: Callable[[], Iterable[Any]] | None = None
        self._compiled: T | None = None

    def use(self, source: Callable[[], Iterable[Any]] | None) -> None:
        """Take the rules in force from ``source`` (the settings), from their next use."""
        self._source = source
        self.reset()

    def reset(self) -> None:
        """Forget the compiled rules (the settings changed)."""
        self._compiled = None

    @contextmanager
    def using(self, rules: Iterable[Any]) -> Iterator[None]:
        """The given rules in force meanwhile (a preview of edited ones)."""
        before = self._compiled
        self._compiled = self._compile(rules)
        try:
            yield
        finally:
            self._compiled = before

    def get(self) -> T:
        if self._compiled is None:
            self._compiled = self._compile(self._source() if self._source else ())
        return self._compiled


def leftmost(regexes: Iterable[re.Pattern[str]], text: str, *args) -> re.Match[str] | None:
    """The leftmost match of the regexes in ``text`` (the first one's, on a tie)."""
    found = (rx.search(text, *args) for rx in regexes)
    return min((m for m in found if m), key=lambda m: m.start(), default=None)
