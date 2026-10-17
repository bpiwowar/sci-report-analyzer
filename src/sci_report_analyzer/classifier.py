"""Suggesting the category of an excerpt from its text's embedding (see llm.embed): trained
on the excerpts already filed in a folder's categories (those of its settings, possibly
shared). A category's excerpts are grouped in clusters, DP-means style (Kulis & Jordan,
ICML 2012; as the infinite mixture prototypes of Allen et al., ICML 2019): one opened for
an excerpt too far from the others; each cluster's mean is a prototype, and a passage gets
the categories whose closest prototype is the closest. How far is "too far" is chosen by
leave-one-out (each excerpt hidden, then suggested), a single mean per category if it does
as well. The embeddings are kept (by model and text), so training again only embeds the
new excerpts."""

from __future__ import annotations

import hashlib
from array import array
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import select

from . import folders, llm
from .db.models import Category, Embedding, Excerpt, ExcerptClassifier, utcnow
from .db.session import session_scope

BATCH = 32  # (texts embedded per request)
ITERATIONS = 10  # (of the clustering, at most)
# The thresholds tried (quantiles of the similarities of the excerpts to their category's
# mean): the lower, the fewer clusters.
QUANTILES = (0.1, 0.25, 0.5, 0.75)


@dataclass
class Trained:
    model: str  # (the embedding model)
    trained_at: datetime  # (UTC)
    count: int  # (the excerpts it learnt from)
    categories: int
    prototypes: int = 0  # (the clusters, over all categories)
    # Leave-one-out: the share of excerpts whose category is suggested first, among the
    # first three (None: trained before it was computed).
    top1: float | None = None
    top3: float | None = None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _unit(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norms == 0, 1.0, norms)


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
                .order_by(Excerpt.id)
            )
        )
    category = {i: c for i, c, _g, _t in rows}
    return [(category.get(g, c) if g else c, t.strip()) for _i, c, g, t in rows if t and t.strip()]


def _refit(x: np.ndarray, centres: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each row in its closest centre, then the centres the (normalised) means of theirs;
    those left empty dropped."""
    assign = (x @ centres.T).argmax(1)
    kept = [k for k in range(len(centres)) if (assign == k).any()]
    centres = _unit(np.stack([x[assign == k].mean(0) for k in kept]))
    return centres, (x @ centres.T).argmax(1)


def clusters(x: np.ndarray, threshold: float | None, least: int, most: int) -> list[np.ndarray]:
    """A category's excerpts (normalised embeddings, one per row) grouped: a cluster opened
    for an excerpt less similar than ``threshold`` to every centre (None: a single one), at
    most ``most``, each of at least ``least`` (the smallest dissolved in the others first).
    The rows of each."""
    n = len(x)
    most = max(1, min(most, n // max(least, 1)))
    if threshold is None or most == 1:
        return [np.arange(n)]
    centres = _unit(x.mean(0, keepdims=True))
    for _ in range(ITERATIONS):
        before = centres
        for i in range(n):
            if len(centres) < most and (centres @ x[i]).max() < threshold:
                centres = np.vstack([centres, x[i]])
        centres, assign = _refit(x, centres)
        if centres.shape == before.shape and np.allclose(centres, before):
            break
    while len(centres) > 1:
        sizes = np.bincount(assign, minlength=len(centres))
        if sizes.min() >= least:
            break
        centres, assign = _refit(x, np.delete(centres, int(sizes.argmin()), 0))
    return [np.flatnonzero(assign == k) for k in range(len(centres))]


@dataclass
class _Fit:
    threshold: float | None
    prototypes: dict[int, np.ndarray]  # (a category's, normalised, one per row)
    top1: float
    top3: float


def _fit(x: np.ndarray, labels: np.ndarray, threshold: float | None, least: int, most: int) -> _Fit:
    """The prototypes of each category, and how well they do: each excerpt taken out of its
    cluster (the clusters kept, so a bit optimistic) then its category ranked."""
    n = len(x)
    groups, sums, owners = np.empty(n, dtype=int), [], []
    for c in np.unique(labels):
        rows = np.flatnonzero(labels == c)
        for members in clusters(x[rows], threshold, least, most):
            groups[rows[members]] = len(sums)
            sums.append(x[rows[members]].sum(0))
            owners.append(c)
    total, owners = np.stack(sums), np.array(owners)
    sizes = np.bincount(groups, minlength=len(total))
    sq = (total * total).sum(1)
    dots = x @ total.T
    sims = dots / np.sqrt(sq)
    every = np.arange(n)
    own = dots[every, groups]
    # (its own prototype without it: |U - x|² = |U|² - 2 U·x + 1; none if it was alone)
    rest = np.sqrt(np.maximum(sq[groups] - 2 * own + 1, 1e-12))
    sims[every, groups] = np.where(sizes[groups] > 1, (own - 1) / rest, -np.inf)
    cats = np.unique(owners)
    scores = np.stack([sims[:, owners == c].max(1) for c in cats], 1)
    truth = scores[every, np.searchsorted(cats, labels)]
    better = (scores > truth[:, None]).sum(1)
    found = np.isfinite(truth)
    return _Fit(
        threshold,
        {int(c): _unit(total[owners == c]) for c in cats},
        float((found & (better == 0)).mean()),
        float((found & (better < 3)).mean()),
    )


def fit(x: np.ndarray, labels: np.ndarray, least: int, most: int) -> _Fit:
    """The best prototypes for excerpts (normalised embeddings, one per row) and their
    categories: the threshold that does best by leave-one-out, first suggested then among the
    first three (a single mean per category unless it does better)."""
    means = {c: _unit(x[labels == c].mean(0)) for c in np.unique(labels)}
    big = [c for c in means if (labels == c).sum() >= 2 * least]
    close = np.concatenate([x[labels == c] @ means[c] for c in big]) if big else np.array([])
    tried = [None] + (sorted({float(q) for q in np.quantile(close, QUANTILES)}) if big else [])
    best = None
    for t in tried:
        f = _fit(x, labels, t, least, most)
        if best is None or (f.top1, f.top3) > (best.top1, best.top3):
            best = f
    return best


def _prototypes(stored: dict[str, Any]) -> dict[int, np.ndarray]:
    """What was stored: {"prototypes": {category: its rows}, …}, or (before) {category: its
    mean}."""
    if "prototypes" in stored:
        return {int(c): np.array(v, dtype=float) for c, v in stored["prototypes"].items()}
    return {int(c): np.array([v], dtype=float) for c, v in stored.items()}


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
    st = llm.settings()
    best = fit(
        _unit(np.array(vectors, dtype=float)),
        np.array([c for c, _t in rows]),
        st.cluster_min,
        st.cluster_max,
    )
    stored = {
        "prototypes": {str(c): np.round(p, 6).tolist() for c, p in best.prototypes.items()},
        "threshold": best.threshold,
        "top1": best.top1,
        "top3": best.top3,
    }
    with session_scope() as s:
        row = s.get(ExcerptClassifier, settings_id) or ExcerptClassifier(settings_id=settings_id)
        row.model, row.trained_at, row.count, row.centroids = model, utcnow(), len(rows), stored
        s.merge(row)
    return _trained(row)


def _trained(row: ExcerptClassifier) -> Trained:
    prototypes = _prototypes(row.centroids)
    return Trained(
        row.model,
        row.trained_at,
        row.count,
        len(prototypes),
        sum(len(p) for p in prototypes.values()),
        row.centroids.get("top1") if "prototypes" in row.centroids else None,
        row.centroids.get("top3") if "prototypes" in row.centroids else None,
    )


def trained(folder_id: int) -> Trained | None:
    """When the folder's classifier was trained, on what (none: never)."""
    with session_scope() as s:
        row = s.get(ExcerptClassifier, folders.settings_id(s, folder_id))
        return None if row is None else _trained(row)


async def suggest(folder_id: int, text: str, k: int = 3) -> list[tuple[int, float]]:
    """The categories closest to a passage (id, cosine similarity to its closest prototype),
    the closest first; none if not trained, or with another model than the folder's now."""
    model = llm.embed_model(folder_id)
    with session_scope() as s:
        settings_id = folders.settings_id(s, folder_id)
        row = s.get(ExcerptClassifier, settings_id)
        if row is None or row.model != model or not text.strip():
            return []
        prototypes = _prototypes(row.centroids)
        existing = set(s.scalars(select(Category.id).where(Category.settings_id == settings_id)))
    (v,) = await embeddings([text.strip()], folder_id)
    v = _unit(np.array(v, dtype=float))
    scores = [(c, float((p @ v).max())) for c, p in prototypes.items() if c in existing and len(p)]
    return sorted(scores, key=lambda x: -x[1])[:k]
