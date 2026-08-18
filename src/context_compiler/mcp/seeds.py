"""Scoped Item 8 seed resolution.

Explicit names still resolve through the application-side FQN map. Task text
now has two useful paths: CPython traceback frames are mapped through the
sidecar's file/line ranges and returned innermost-first; otherwise the small
deterministic identifier matcher supplies candidates and an explicitly passed
graph handle can connectivity-rerank its top candidates within two hops.

BM25, embedding search, and LLM proposal are deliberately not implemented in
this session. Similarity is entry-point ranking only; it is never used to
expand the structural closure.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ..graph.pack import HUB_SKIP_DEGREE
from ..graph.sidecar import SymbolMeta

TASK_TOP_K = 6
CONNECTIVITY_CAP = 20
_WORD = re.compile(r"[A-Za-z0-9_]+")
_TRACEBACK_FRAME = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<name>[^\s]+)',
    re.MULTILINE,
)


class SeedResolutionError(ValueError):
    """Raised with the offending name(s) in the message -- never a bare KeyError."""


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def _append_diagnostic(diagnostics: list[str] | None, message: str) -> None:
    if diagnostics is not None:
        diagnostics.append(message)


def _same_path(frame_file: str, symbol_file: str) -> bool:
    """Match absolute traceback paths to the sidecar's repo-relative paths."""
    frame = frame_file.replace("\\", "/").lstrip("./")
    symbol = symbol_file.replace("\\", "/").lstrip("./")
    return (
        frame == symbol
        or frame.endswith("/" + symbol)
        or symbol.endswith("/" + frame)
    )


def _traceback_frames(task: str) -> list[tuple[str, int, str]]:
    """Parse CPython frames and reverse them to innermost-first order."""
    frames = [
        (m.group("file"), int(m.group("line")), m.group("name"))
        for m in _TRACEBACK_FRAME.finditer(task)
    ]
    frames.reverse()
    return frames


def _resolve_traceback(
    task: str,
    sidecar: Mapping[int, SymbolMeta],
    top_k: int,
    diagnostics: list[str] | None,
) -> tuple[list[int], bool]:
    """Return ``(seeds, parsed)`` for traceback text, without graph access."""
    frames = _traceback_frames(task)
    if not frames:
        return [], False

    by_file: dict[str, list[tuple[int, SymbolMeta]]] = {}
    for node, meta in sidecar.items():
        symbol_file = meta.file.replace("\\", "/").lstrip("./")
        by_file.setdefault(symbol_file, []).append((node, meta))

    ids: list[int] = []
    seen: set[int] = set()
    for frame_file, line, frame_name in frames:
        matches = [
            (node, meta)
            for symbol_file, entries in by_file.items()
            if _same_path(frame_file, symbol_file)
            for node, meta in entries
            if meta.start_line
            and meta.start_line <= line <= (meta.end_line or meta.start_line)
        ]
        if not matches:
            indexed = any(_same_path(frame_file, symbol_file) for symbol_file in by_file)
            reason = "line is inside no known symbol" if indexed else "file is not indexed"
            _append_diagnostic(
                diagnostics,
                f'traceback frame {frame_file}:{line} ({frame_name}) skipped: {reason}',
            )
            continue

        named = [
            (node, meta)
            for node, meta in matches
            if meta.fqn.rsplit(".", 1)[-1] == frame_name
            or (frame_name == "<module>" and meta.kind == "module")
        ]
        pool = named or matches
        node, _meta = min(
            pool,
            key=lambda item: (
                (item[1].end_line or item[1].start_line) - item[1].start_line,
                item[1].fqn,
                item[0],
            ),
        )
        if node not in seen:
            seen.add(node)
            ids.append(node)
            if len(ids) >= top_k:
                break
    return ids, True


def _edges_from_graph(graph: object, nodes: list[int]) -> dict[int, set[int]]:
    """Read forward CALLS edges from a fixture mapping or Expander-shaped handle."""
    if isinstance(graph, Mapping):
        result: dict[int, set[int]] = {}
        for src in nodes:
            values = graph.get(src, ())
            destinations: set[int] = set()
            for value in values:
                if isinstance(value, tuple):
                    if len(value) >= 2 and value[0] == "CALLS":
                        destinations.add(value[1])
                else:
                    destinations.add(value)
            result[src] = destinations
        return result

    reader: Any = graph
    if hasattr(reader, "expand"):
        rows = reader.expand(nodes)
    elif callable(reader):
        rows = reader(nodes)
    else:
        return {}
    result = {node: set() for node in nodes}
    for row in rows:
        if len(row) == 3:
            src, edge_type, dst = row
            if edge_type == "CALLS" and src in result:
                result[src].add(dst)
    return result


def _edges_from_reverse(
    reverse: object,
    nodes: list[int],
    in_degrees: Mapping[int, int],
) -> dict[int, set[int]]:
    """Read the induced candidate CALLS graph with pack.py's hub policy."""
    result = {node: set() for node in nodes}
    candidate_set = set(nodes)
    if not hasattr(reverse, "read"):
        return result
    for target in nodes:
        if in_degrees.get(target, 0) > HUB_SKIP_DEGREE:
            continue
        callers = reverse.read("CALLS", target)
        for caller in callers:
            if caller in candidate_set:
                result.setdefault(caller, set()).add(target)
    return result


def _connectivity_scores(adjacency: Mapping[int, set[int]]) -> dict[int, int]:
    """Count candidate peers reachable in either direction within two hops."""
    nodes = set(adjacency)
    reverse: dict[int, set[int]] = {node: set() for node in nodes}
    for src, destinations in adjacency.items():
        for dst in destinations & nodes:
            reverse.setdefault(dst, set()).add(src)

    scores: dict[int, int] = {}
    for root in nodes:
        peers: set[int] = set()
        for edges in (adjacency, reverse):
            frontier = {root}
            for _ in range(2):
                frontier = {
                    nxt
                    for node in frontier
                    for nxt in edges.get(node, ())
                    if nxt in nodes
                } - {root}
                peers.update(frontier)
        scores[root] = len(peers)
    return scores


def rerank_connectivity(
    candidates: Sequence[int],
    by_fqn: Mapping[str, int] | None = None,
    *,
    sidecar: Mapping[int, SymbolMeta] | None = None,
    graph: object | None = None,
    reverse: object | None = None,
    in_degrees: Mapping[int, int] | None = None,
) -> list[int]:
    """Rerank at most 20 candidates by induced two-hop connectivity.

    A candidate gets one point for each other candidate reachable in at most two
    CALLS hops in either direction. Ties use FQN, so the result is reproducible.
    ``reverse`` is the production handle; ``graph`` also accepts an Expander or
    a small adjacency mapping for callers and tests.
    """
    if sidecar is None:
        if by_fqn is None:
            raise TypeError("rerank_connectivity needs sidecar or by_fqn")
        sidecar = {
            node: SymbolMeta(fqn, "", "", 0, 0, (), (), 0, 0, None)
            for fqn, node in by_fqn.items()
        }
    pool = list(dict.fromkeys(candidates))[:CONNECTIVITY_CAP]
    if not pool or (graph is None and reverse is None):
        return pool
    degrees = in_degrees or {}
    adjacency = (
        _edges_from_reverse(reverse, pool, degrees)
        if reverse is not None
        else _edges_from_graph(graph, pool)
    )
    scores = _connectivity_scores(adjacency)
    return sorted(
        pool, key=lambda node: (-scores.get(node, 0), sidecar[node].fqn, node)
    )


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
    task: str,
    sidecar: Mapping[int, SymbolMeta],
    top_k: int = TASK_TOP_K,
    *,
    graph: object | None = None,
    reverse: object | None = None,
    in_degrees: Mapping[int, int] | None = None,
    diagnostics: list[str] | None = None,
) -> list[int]:
    """Resolve traceback frames, else rank identifiers and optionally rerank."""
    traceback_ids, traceback_parsed = _resolve_traceback(
        task, sidecar, top_k, diagnostics
    )
    if traceback_parsed:
        return traceback_ids
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
    candidates = [node for _, _, node in scored[:CONNECTIVITY_CAP]]
    if graph is not None or reverse is not None:
        candidates = rerank_connectivity(
            candidates,
            sidecar=sidecar,
            graph=graph,
            reverse=reverse,
            in_degrees=in_degrees,
        )
    return candidates[:top_k]
