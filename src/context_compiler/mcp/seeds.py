"""PLACEHOLDER(item-8): minimal seed resolution.

Item 8 replaces this with the hybrid resolver (BM25 + embeddings + traceback +
LLM proposal + connectivity rerank). This is deliberately small:

* an explicit seed resolves as an exact FQN, else a unique dotted-suffix match
  (``QuerySet.filter`` -> ``django.db.models.query.QuerySet.filter``); ambiguous
  or missing raises with the offending name in the message.
* a ``task`` string is tokenized, scored against each symbol's FQN segments by
  overlap count, and the top 6 win. No BM25, no embeddings, no ranking beyond
  a match count -- Item 8's job, not this one's.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from ..graph.sidecar import SymbolMeta

TASK_TOP_K = 6
_WORD = re.compile(r"[A-Za-z0-9_]+")


class SeedResolutionError(ValueError):
    """Raised with the offending name(s) in the message -- never a bare KeyError."""


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def resolve_seed(query: str, by_fqn: Mapping[str, int]) -> int:
    """One seed: exact FQN, else a unique dotted-suffix match."""
    if query in by_fqn:
        return by_fqn[query]
    suffix = f".{query}"
    matches = sorted(fqn for fqn in by_fqn if fqn == query or fqn.endswith(suffix))
    if not matches:
        raise SeedResolutionError(f"no symbol matches {query!r}")
    if len(matches) > 1:
        shown = ", ".join(matches[:5])
        more = f" and {len(matches) - 5} more" if len(matches) > 5 else ""
        raise SeedResolutionError(f"{query!r} is ambiguous: matches {shown}{more}")
    return by_fqn[matches[0]]


def resolve_seeds(queries: Sequence[str], by_fqn: Mapping[str, int]) -> list[int]:
    """Every query resolved, or every failure reported together."""
    ids: list[int] = []
    errors: list[str] = []
    for query in queries:
        try:
            ids.append(resolve_seed(query, by_fqn))
        except SeedResolutionError as exc:
            errors.append(str(exc))
    if errors:
        raise SeedResolutionError("; ".join(errors))
    return ids


def resolve_task(
    task: str, sidecar: Mapping[int, SymbolMeta], top_k: int = TASK_TOP_K
) -> list[int]:
    """Tokenize ``task``, rank symbols by FQN-segment token overlap, take top ``top_k``."""
    tokens = _tokenize(task)
    if not tokens:
        raise SeedResolutionError(f"{task!r} produced no tokens to match against symbol names")
    scored: list[tuple[int, str, int]] = []
    for node, meta in sidecar.items():
        overlap = len(tokens & _tokenize(meta.fqn))
        if overlap:
            scored.append((overlap, meta.fqn, node))
    if not scored:
        raise SeedResolutionError(f"no symbols matched any token in {task!r}")
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [node for _, _, node in scored[:top_k]]
