"""Suggesting the category of an excerpt from its text's embedding (see llm.embed): trained
on the excerpts already filed in a folder's categories (those of its settings, possibly
shared), each category having the mean of its excerpts' embeddings; a passage's closest ones
are suggested. The embeddings are kept (by model and text), so training again only embeds
the new excerpts."""

from __future__ import annotations

import hashlib
import math
from array import array
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from . import folders, llm
from .db.models import Category, Embedding, Excerpt, ExcerptClassifier, utcnow
from .db.session import session_scope

BATCH = 32  # (texts embedded per request)


@dataclass
class Trained:
    model: str  # (the embedding model)
    trained_at: datetime  # (UTC)
    count: int  # (the excerpts it learnt from)
    categories: int


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _normalised(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _settings(folder_id: int) -> int:
    with session_scope() as s:
        return folders.settings_id(s, folder_id)


async def embeddings(texts: list[str], folder_id: int | None) -> list[list[float]]:
    """The embeddings of ``texts`` with the folder's model: those kept, the others asked
    for (then kept)."""
    model = llm.embed_model(folder_id)
    digests = [_digest(t) for t in texts]
    with session_scope() as s:
        known = {
            e.digest: list(array("f", e.vector))
            for e in s.scalars(
                select(Embedding).where(Embedding.model == model, Embedding.digest.in_(digests))
            )
        }
    missing = list({d: t for d, t in zip(digests, texts, strict=True) if d not in known}.items())
    for i in range(0, len(missing), BATCH):
        chunk = missing[i : i + BATCH]
        vectors = await llm.embed([t for _, t in chunk], folder_id)
        with session_scope() as s:
            for (d, _t), v in zip(chunk, vectors, strict=True):
                known[d] = v
                s.merge(Embedding(model=model, digest=d, vector=array("f", v).tobytes()))
    return [known[d] for d in digests]


def _training_set(settings_id: int) -> list[tuple[int, str]]:
    """The excerpts filed in the settings' categories: (category id, text); merged ones,
    in that of the group (its lead's)."""
    with session_scope() as s:
        rows = list(
            s.execute(
                select(Excerpt.id, Excerpt.category_id, Excerpt.group_id, Excerpt.text)
                .join(Category, Category.id == Excerpt.category_id)
                .where(Category.settings_id == settings_id)
            )
        )
    category = {i: c for i, c, _g, _t in rows}
    return [(category.get(g, c) if g else c, t.strip()) for _i, c, g, t in rows if t and t.strip()]


async def train(folder_id: int) -> Trained:
    """Learn from the excerpts filed in the folder's categories (raises if there is none, or
    no embedding model)."""
    model = llm.embed_model(folder_id)
    if not model:
        raise RuntimeError("No embedding model (Settings → Local LLM)")
    settings_id = _settings(folder_id)
    rows = _training_set(settings_id)
    if not rows:
        raise ValueError("No excerpt filed yet")
    vectors = await embeddings([t for _, t in rows], folder_id)
    sums: dict[int, list[float]] = {}
    for (cat, _t), v in zip(rows, vectors, strict=True):
        v = _normalised(v)
        acc = sums.setdefault(cat, [0.0] * len(v))
        for i, x in enumerate(v):
            acc[i] += x
    centroids = {str(c): _normalised(v) for c, v in sums.items()}
    with session_scope() as s:
        row = s.get(ExcerptClassifier, settings_id) or ExcerptClassifier(settings_id=settings_id)
        row.model, row.trained_at, row.count, row.centroids = model, utcnow(), len(rows), centroids
        s.merge(row)
    return Trained(model, row.trained_at, len(rows), len(centroids))


def trained(folder_id: int) -> Trained | None:
    """When the folder's classifier was trained, on what (none: never)."""
    with session_scope() as s:
        row = s.get(ExcerptClassifier, folders.settings_id(s, folder_id))
        if row is None:
            return None
        return Trained(row.model, row.trained_at, row.count, len(row.centroids))


async def suggest(folder_id: int, text: str, k: int = 3) -> list[tuple[int, float]]:
    """The categories closest to a passage (id, cosine similarity), the closest first; none
    if not trained, or with another model than the folder's now."""
    model = llm.embed_model(folder_id)
    with session_scope() as s:
        settings_id = folders.settings_id(s, folder_id)
        row = s.get(ExcerptClassifier, settings_id)
        if row is None or row.model != model or not text.strip():
            return []
        centroids = dict(row.centroids)
        existing = set(s.scalars(select(Category.id).where(Category.settings_id == settings_id)))
    (v,) = await embeddings([text.strip()], folder_id)
    v = _normalised(v)
    scores = [
        (int(c), sum(a * b for a, b in zip(v, centroid, strict=False)))
        for c, centroid in centroids.items()
        if int(c) in existing
    ]
    return sorted(scores, key=lambda x: -x[1])[:k]
