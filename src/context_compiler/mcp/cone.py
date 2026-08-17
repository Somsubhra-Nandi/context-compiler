"""``impact_cone`` (Item 7 Sec 5): a bounded, honest over-approximation.

Reverse ``CALLS`` closure off one symbol -- what could be affected if it
changes, never "what breaks". A3.1 applies and bites harder here than in
packing: there is no batched reverse read, so each hop costs one round trip
*per frontier node*, and a hub can cost seconds on its own (measured 9s at
2,824 in-degree, docs/spikes/graph-item-5-results.md). Four bounds keep that
survivable, all required by the task and none negotiable:

* ``max_depth`` capped at 2
* hub skip: no reverse read above ``HUB_SKIP_DEGREE`` in-degree -- reported as
  skipped, not silently dropped
* frontier cap: at most ``FRONTIER_CAP`` nodes expanded per hop
* hard deadline: partial results with ``truncated=True`` rather than hanging

Ranking reuses ``graph.pack.idf`` -- the same hub-suppression term packing
already uses, applied to the same kind of candidate (a caller, reached over
``CALLS``), so there is one definition of "relevant" in the codebase rather
than two similar ones.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from ..graph.pack import HUB_SKIP_DEGREE, idf
from ..graph.sidecar import SymbolMeta

MAX_DEPTH_CAP = 2
FRONTIER_CAP = 200
DEADLINE_SECONDS = 10.0
TOP_K = 30

#: The edge this tool traverses reversed. Packing's ``StaticCallerSource`` is
#: the only reverse-read consumer this codebase has validated against the
#: engine (A3.1); widening to every ``HARD_EDGES`` type would multiply the
#: per-node round-trip cost six-fold for edges nobody has measured here.
EDGE_TYPE = "CALLS"


class ReverseRead(Protocol):
    def read(self, edge_type: str, node: int) -> list[int]: ...


@dataclass
class ConeEntry:
    node: int
    fqn: str
    file: str
    depth: int
    idf: float


@dataclass
class ConeResult:
    root: int
    depth_reached: int
    counts_by_depth: dict[int, int] = field(default_factory=dict)
    top: list[ConeEntry] = field(default_factory=list)
    hubs_skipped: list[int] = field(default_factory=list)
    truncated: bool = False
    truncation_reason: str = ""
    seconds: float = 0.0


def compute_impact_cone(
    root: int,
    max_depth: int,
    reverse: ReverseRead,
    sidecar: Mapping[int, SymbolMeta],
    degrees: Mapping[int, int],
    in_degrees: Mapping[int, int],
    n_symbols: int,
    *,
    frontier_cap: int = FRONTIER_CAP,
    deadline_seconds: float = DEADLINE_SECONDS,
    clock: Callable[[], float] | None = None,
) -> ConeResult:
    """Bounded reverse-``CALLS`` BFS from ``root``. Pure over ``reverse``.

    ``clock`` is a hook for tests that need to force a deadline without a real
    sleep; production callers omit it and get ``time.perf_counter``.
    """
    depth_cap = min(max_depth, MAX_DEPTH_CAP)
    clock = clock or time.perf_counter
    t0 = clock()

    seen = {root}
    depth_of: dict[int, int] = {}
    hubs_skipped: list[int] = []
    truncated = False
    reason = ""
    frontier = [root]
    reached = 0

    for d in range(1, depth_cap + 1):
        if clock() - t0 > deadline_seconds:
            truncated, reason = True, "deadline"
            break
        if len(frontier) > frontier_cap:
            frontier = frontier[:frontier_cap]
            truncated, reason = True, "frontier cap"

        next_frontier: list[int] = []
        for node in frontier:
            if clock() - t0 > deadline_seconds:
                truncated, reason = True, "deadline"
                break
            if in_degrees.get(node, 0) > HUB_SKIP_DEGREE:
                hubs_skipped.append(node)
                continue
            for caller in reverse.read(EDGE_TYPE, node):
                if caller in seen or caller not in sidecar:
                    continue
                seen.add(caller)
                depth_of[caller] = d
                next_frontier.append(caller)

        reached = d
        if truncated:
            break
        frontier = next_frontier
        if not frontier:
            break

    scored = sorted(
        depth_of.items(), key=lambda kv: (-idf(kv[0], degrees, n_symbols), kv[0])
    )
    top: list[ConeEntry] = []
    for node, depth in scored[:TOP_K]:
        meta = sidecar.get(node)
        if meta is None:
            continue
        top.append(
            ConeEntry(
                node=node,
                fqn=meta.fqn,
                file=meta.file,
                depth=depth,
                idf=round(idf(node, degrees, n_symbols), 3),
            )
        )

    counts_by_depth = {d: sum(1 for v in depth_of.values() if v == d) for d in range(1, reached + 1)}

    return ConeResult(
        root=root,
        depth_reached=reached,
        counts_by_depth=counts_by_depth,
        top=top,
        hubs_skipped=hubs_skipped,
        truncated=truncated,
        truncation_reason=reason,
        seconds=clock() - t0,
    )


def render_cone(root_fqn: str, result: ConeResult) -> str:
    total = sum(result.counts_by_depth.values())
    lines = [
        f"Potentially affected by changes to {root_fqn}",
        "(reverse CALLS closure, an over-approximation of what is potentially affected)",
        "",
        f"{total} symbol(s) found, depth reached {result.depth_reached}:",
    ]
    for d in sorted(result.counts_by_depth):
        lines.append(f"  depth {d}: {result.counts_by_depth[d]}")
    if result.hubs_skipped:
        lines.append(
            f"  {len(result.hubs_skipped)} hub(s) skipped (>{HUB_SKIP_DEGREE} callers each) -- truncated, not dropped"
        )
    if result.truncated:
        lines.append(f"  TRUNCATED ({result.truncation_reason}) -- partial result")
    lines.append("")
    lines.append(f"Top {len(result.top)} by relevance, grouped by file:")
    by_file: dict[str, list[ConeEntry]] = {}
    for entry in result.top:
        by_file.setdefault(entry.file, []).append(entry)
    for file in sorted(by_file):
        lines.append(f"# {file}")
        for entry in by_file[file]:
            leaf = entry.fqn.rsplit(".", 1)[-1]
            lines.append(f"  {leaf}  depth {entry.depth}  idf {entry.idf}")
    return "\n".join(lines)
