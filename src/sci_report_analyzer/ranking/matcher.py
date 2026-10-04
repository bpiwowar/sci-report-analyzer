"""Fuzzy venue matcher.

Records are plain dicts
(``name, source, type, sjr, quartile, hindex, coreRank, coreEdition, impactFactor,
issn, aliases, sourceId, url, predatory``).
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .normalize import normalize, token_key, tokenize, tokens_match

Record = dict[str, Any]

_ISSN_CHARS = re.compile(r"[^\dxX]", re.ASCII)


def issn_key(issn: str | None) -> str:
    """An ISSN compared ("1234-567X", "1234567x": "1234567x"); "" for none."""
    return _ISSN_CHARS.sub("", str(issn)).lower() if issn else ""


def record_key(rec: Record) -> str:
    """Stable identity of a ranking record, used to persist forced matches."""
    ident = rec.get("sourceId") or normalize(rec.get("name"))
    return f"{rec.get('source')}:{rec.get('type')}:{ident}"


@dataclass(frozen=True)
class MatchResult:
    record: Record
    score: float
    exact: bool


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


class Matcher:
    def __init__(self) -> None:
        self.records: list[Record] = []
        self._by_name: dict[str, int] = {}
        self._by_name_typed: dict[str, int] = {}
        self._by_issn: dict[str, int] = {}
        self._by_key: dict[str, int] = {}
        self._inverted: dict[str, dict[int, None]] = {}
        self._rec_tokens: list[list[str]] = []
        # Records of each token, and the tokens of each index key (those a token can match).
        self._by_token: dict[str, dict[int, None]] = {}
        self._vocab: dict[str, dict[str, None]] = {}
        self._rec_keys: list[frozenset[str]] = []

    def __len__(self) -> int:
        return len(self.records)

    def load(self, dataset: Iterable[Record]) -> Matcher:
        for rec in dataset:
            idx = len(self.records)
            self.records.append(rec)
            self._by_key.setdefault(record_key(rec), idx)
            token_set: dict[str, None] = {}
            for n in [rec.get("name"), *(rec.get("aliases") or [])]:
                norm = normalize(n)
                if norm:
                    self._by_name.setdefault(norm, idx)
                    self._by_name_typed.setdefault(f"{norm} {rec.get('type')}", idx)
                for tok in tokenize(n):
                    token_set[tok] = None
                    self._inverted.setdefault(token_key(tok), {})[idx] = None
                    self._by_token.setdefault(tok, {})[idx] = None
                    self._vocab.setdefault(token_key(tok), {})[tok] = None
            self._rec_tokens.append(list(token_set))
            self._rec_keys.append(frozenset(map(token_key, token_set)))
            issns = rec.get("issn")
            if issns:
                for issn in [issns] if isinstance(issns, str) else issns:
                    key = issn_key(issn)
                    if key:
                        self._by_issn[key] = idx
        return self

    def by_key(self, key: str) -> Record | None:
        idx = self._by_key.get(key)
        return None if idx is None else self.records[idx]

    def _issn_hit(self, issn: str | None) -> MatchResult | None:
        if issn:
            idx = self._by_issn.get(issn_key(issn))
            if idx is not None:
                return MatchResult(self.records[idx], 1.0, True)
        return None

    def _scored(self, q_tokens: list[str]) -> list[tuple[int, float]]:
        # Prefix matching is only safe with multi-token context ("CoRR" ≠ "Corrosion").
        allow_prefix = len(q_tokens) > 1
        # The query tokens each record has (a token matching another shares its index key:
        # only the tokens of that key are compared).
        shared_by: dict[int, int] = {}
        for q in q_tokens:
            if allow_prefix:
                words = [r for r in self._vocab.get(token_key(q), ()) if tokens_match(q, r)]
            else:
                words = [q] if q in self._by_token else []
            hits: set[int] = set()
            for r in words:
                hits.update(self._by_token[r])
            for idx in hits:
                shared_by[idx] = shared_by.get(idx, 0) + 1
        out: list[tuple[int, float]] = []
        for idx in shared_by:
            rec_tokens = self._rec_tokens[idx]
            shared = shared_by[idx]
            union = len(q_tokens) + len(rec_tokens) - shared
            jaccard = 0.0 if union <= 0 else shared / union
            coverage = shared / len(q_tokens)
            out.append((idx, jaccard * 0.7 + coverage * 0.3))
        return out

    def _tie_order(self, q_tokens: list[str]):
        """Order of records of equal scores: that of the index keys' union (like the JS Set
        of Sets), by the first query token whose key the record has, then record."""
        keys = [token_key(q) for q in q_tokens]

        def order(idx: int) -> tuple[int, int]:
            mine = self._rec_keys[idx]
            return next(i for i, k in enumerate(keys) if k in mine), idx

        return order

    def match(
        self, venue: str, issn: str | None = None, prefer_type: str | None = None
    ) -> MatchResult | None:
        if hit := self._issn_hit(issn):
            return hit
        norm = normalize(venue)
        if not norm:
            return None
        idx = self._by_name_typed.get(f"{norm} {prefer_type}") if prefer_type else None
        if idx is None:
            idx = self._by_name.get(norm)
        if idx is not None:
            return MatchResult(self.records[idx], 1.0, True)

        q_tokens = _unique(tokenize(venue))
        if not q_tokens:
            return None
        ranked = [
            (
                score
                + (0.05 if prefer_type and self.records[idx].get("type") == prefer_type else 0.0),
                score,
                idx,
            )
            for idx, score in self._scored(q_tokens)
        ]
        if not (top := max((r[0] for r in ranked), default=0.0)):
            return None
        order = self._tie_order(q_tokens)
        _, best_score, best = min((r for r in ranked if r[0] == top), key=lambda r: order(r[2]))
        if best_score >= 0.6:
            return MatchResult(self.records[best], best_score, False)
        return None

    def candidates(self, venue: str, issn: str | None = None, limit: int = 6) -> list[MatchResult]:
        """Top matches best-first regardless of threshold (for the manual picker)."""
        out: list[MatchResult] = []
        seen: set[int] = set()

        def add(r: MatchResult) -> None:
            if id(r.record) not in seen:
                seen.add(id(r.record))
                out.append(r)

        if hit := self._issn_hit(issn):
            add(hit)
        norm = normalize(venue)
        if norm and (idx := self._by_name.get(norm)) is not None:
            add(MatchResult(self.records[idx], 1.0, True))
        q_tokens = _unique(tokenize(venue))
        if q_tokens:
            order = self._tie_order(q_tokens)
            scored = heapq.nsmallest(
                limit + 2, self._scored(q_tokens), key=lambda t: (-t[1], order(t[0]))
            )
            for idx, score in scored:
                add(MatchResult(self.records[idx], score, False))
        return out[:limit]

    def search(self, text: str, limit: int = 20) -> list[MatchResult]:
        """Free-text search over record names (settings lookup / force picker)."""
        return self.candidates(
            text, text if re.search(r"\d{4}-?\d{3}[\dxX]", text) else None, limit
        )
