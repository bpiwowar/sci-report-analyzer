"""Built-in items (rules, tracks) as saved: one as its default is saved as its id only, so
that a newer default (a new release) reaches it; one changed, in full."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from ..authorship import without_meta


def is_default(item: BaseModel, defaults: Mapping[str, BaseModel]) -> bool:
    """A built-in item as its default (who set it and when aside)."""
    d = defaults.get(getattr(item, "id", None))
    return d is not None and without_meta(item.model_dump()) == without_meta(d.model_dump())


def refs(items: Iterable[BaseModel], defaults: Mapping[str, BaseModel]) -> list[Any]:
    """The items as saved: each as its default by its id, the others dumped."""
    return [x.id if is_default(x, defaults) else x.model_dump() for x in items]


def unrefs(items: Iterable[Any], defaults: Mapping[str, BaseModel]) -> list[Any]:
    """The items saved (``refs``): an id as its default (one no longer built in dropped)."""
    return [
        defaults[x].model_dump() if isinstance(x, str) else x
        for x in items
        if not isinstance(x, str) or x in defaults
    ]


def all_default(items: Iterable[BaseModel], defaults: Mapping[str, BaseModel]) -> bool:
    """The items as the defaults, in their order (none added nor removed)."""
    return refs(items, defaults) == list(defaults)
