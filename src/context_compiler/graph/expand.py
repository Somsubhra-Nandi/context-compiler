"""Frontier expansion in the amended Amendment A1.1 form.

Spec Sec 5.1's canonical query is rejected at parse time. The only shape this
engine's ``UNWIND`` batch-read classifier accepts is::

    UNWIND $rows AS row
      MATCH (x {id: row.v})-[:CALLS]->(y)      -- NO labels
      RETURN row.v AS src, y.id AS dst         -- EXACTLY two projections

so the edge type comes from the loop variable, destination scalars come from
the sidecar, and non-``Symbol`` destinations are filtered application-side by
``SIDECAR`` membership.

Cost model: six typed queries per productive hop, twelve for an unchunked
two-hop closure, zero property fetches.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .client import DEFAULT_BATCH, GraphClient

HARD_EDGES = [
    "REFERENCES_TYPE",
    "CALLS",
    "OVERRIDES",
    "IMPLEMENTS",
    "DECORATED_BY",
    "READS_CONSTANT",
]

#: One template per hard edge type, built once.
QUERIES = {
    et: (
        "UNWIND $rows AS row "
        f"MATCH (x {{id: row.v}})-[:{et}]->(y) "
        "RETURN row.v AS src, y.id AS dst"
    )
    for et in HARD_EDGES
}


@dataclass
class ExpandStats:
    """Round-trip accounting -- the figure the acceptance gate asks for."""

    round_trips: int = 0
    hops: int = 0
    frontier_rows: int = 0
    edges_returned: int = 0
    filtered_out: int = 0
    seconds: float = 0.0
    per_hop: list[dict] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return (
            f"{self.hops} hop(s)  {self.round_trips} round trips  "
            f"{self.edges_returned:,} edges  {self.seconds * 1000:.1f} ms"
        )


@dataclass
class Expander:
    """Callable frontier reader bound to one HydraDB session."""

    client: GraphClient
    membership: set[int] | dict | None = None
    batch_size: int = DEFAULT_BATCH
    stats: ExpandStats = field(default_factory=ExpandStats)
    _session: object | None = field(default=None, repr=False)

    def __enter__(self) -> "Expander":
        self._ctx = self.client.session()
        self._session = self._ctx.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.__exit__(*exc)
        self._session = None

    def __call__(self, frontier: list[int]) -> list[tuple[int, str, int]]:
        return self.expand(frontier)

    def expand(self, frontier: list[int]) -> list[tuple[int, str, int]]:
        """Return ``(src, edge_type, dst)`` for every hard edge off the frontier."""
        rows = [{"v": n} for n in frontier]
        out: list[tuple[int, str, int]] = []
        if not rows:
            return out

        t0 = time.perf_counter()
        trips = 0
        returned = 0
        filtered = 0
        owns_session = self._session is None
        if owns_session:
            ctx = self.client.session()
            session = ctx.__enter__()
        else:
            session = self._session

        try:
            for et in HARD_EDGES:
                query = QUERIES[et]
                for start in range(0, len(rows), self.batch_size):
                    chunk = rows[start : start + self.batch_size]
                    trips += 1
                    for r in session.run(query, rows=chunk):
                        returned += 1
                        dst = r["dst"]
                        if self.membership is not None and dst not in self.membership:
                            filtered += 1
                            continue
                        out.append((r["src"], et, dst))
        finally:
            if owns_session:
                ctx.__exit__(None, None, None)

        elapsed = time.perf_counter() - t0
        self.stats.round_trips += trips
        self.stats.hops += 1
        self.stats.frontier_rows += len(rows)
        self.stats.edges_returned += returned
        self.stats.filtered_out += filtered
        self.stats.seconds += elapsed
        self.stats.per_hop.append(
            {
                "frontier": len(rows),
                "round_trips": trips,
                "edges": returned,
                "filtered": filtered,
                "ms": round(elapsed * 1000, 2),
            }
        )
        return out


def expected_round_trips(frontier_sizes: list[int], batch_size: int = DEFAULT_BATCH) -> int:
    """``6 * ceil(|frontier|/B)`` summed over productive hops (spec Sec 5.1)."""
    return sum(
        len(HARD_EDGES) * -(-n // batch_size) for n in frontier_sizes if n
    )


class EnvelopeMiss(LookupError):
    """A frozen expander was asked for a node the envelope never fetched.

    Raised rather than silently returning no edges, because an empty answer
    would look like a leaf and would quietly falsify ``is_closed()``.
    """


@dataclass
class CachingExpander:
    """Memoises expansion per source node, so a node is read at most once.

    Sec 6.2 scans four profiles and Sec 6.3 evaluates a bundle per candidate
    per iteration. Every one of those is a closure over a *subset* of the P3
    node set, so without memoisation a demoted compile would pay 12 round trips
    per profile. With it, the whole compile costs one expansion per node.

    It also serves as the edge oracle ``is_closed()`` needs: ``expanded``
    records exactly which nodes have had their out-edges read, so the I6 check
    can refuse to certify a node it has never looked at.
    """

    inner: object
    edges: dict[int, list[tuple[str, int]]] = field(default_factory=dict)
    expanded: set[int] = field(default_factory=set)
    hits: int = 0
    misses: int = 0

    def __call__(self, frontier: list[int]) -> list[tuple[int, str, int]]:
        missing = [n for n in frontier if n not in self.expanded]
        self.hits += len(frontier) - len(missing)
        self.misses += len(missing)
        if missing:
            for src, edge_type, dst in self.inner(missing):
                self.edges.setdefault(src, []).append((edge_type, dst))
            self.expanded.update(missing)
        out: list[tuple[int, str, int]] = []
        for n in frontier:
            out.extend((n, et, dst) for et, dst in self.edges.get(n, ()))
        return out

    @property
    def round_trips(self) -> int:
        stats = getattr(self.inner, "stats", None)
        return stats.round_trips if stats is not None else 0

    def frozen(self) -> "FrozenExpander":
        """A read-only view that raises instead of hitting the database."""
        return FrozenExpander(self.edges, self.expanded)


@dataclass
class FrozenExpander:
    """Envelope-backed expansion. Zero round trips, by construction."""

    edges: dict[int, list[tuple[str, int]]]
    expanded: set[int]

    def __call__(self, frontier: list[int]) -> list[tuple[int, str, int]]:
        out: list[tuple[int, str, int]] = []
        for n in frontier:
            if n not in self.expanded:
                raise EnvelopeMiss(n)
            out.extend((n, et, dst) for et, dst in self.edges.get(n, ()))
        return out


#: Reverse adjacency, single source. The batched A1.1 shape with the arrow
#: reversed is **rejected at parse time** by this engine -- see
#: ``REVERSE_BATCH_QUERY`` and docs/spikes/graph-item-5-results.md A3.1. This
#: non-batched form is the only one that works, and it costs one round trip per
#: source node rather than one per chunk.
REVERSE_QUERY = "MATCH (x {{id: $v}})<-[:{et}]-(y) RETURN y.id AS dst"

#: Pinned for the rejection test. If an engine upgrade starts accepting this,
#: the candidate pool collapses from N round trips to ceil(N/B) and A3.1 can be
#: withdrawn.
REVERSE_BATCH_QUERY = (
    "UNWIND $rows AS row "
    "MATCH (x {{id: row.v}})<-[:{et}]-(y) "
    "RETURN row.v AS src, y.id AS dst"
)


@dataclass
class ReverseReader:
    """Single-source reverse reads for Sec 6.3's candidate pool.

    Not an ``Expander``: the engine's batch-read classifier requires the
    id-bound node to be the *source* of the arrow, so there is no batched form
    to wrap. Round trips are counted here because they land outside the Sec 5.1
    cost model and the acceptance gate asks for the total.
    """

    client: object
    membership: set[int] | dict | None = None
    round_trips: int = 0
    seconds: float = 0.0
    filtered_out: int = 0
    _session: object | None = field(default=None, repr=False)

    def __enter__(self) -> "ReverseReader":
        self._ctx = self.client.session()
        self._session = self._ctx.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.__exit__(*exc)
        self._session = None

    def read(self, edge_type: str, node: int) -> list[int]:
        """Ids of every ``y`` with ``y -[:edge_type]-> node``."""
        query = REVERSE_QUERY.format(et=edge_type)
        owns = self._session is None
        if owns:
            ctx = self.client.session()
            session = ctx.__enter__()
        else:
            session = self._session
        t0 = time.perf_counter()
        try:
            out: list[int] = []
            for r in session.run(query, v=node):
                dst = r["dst"]
                if self.membership is not None and dst not in self.membership:
                    self.filtered_out += 1
                    continue
                out.append(dst)
        finally:
            if owns:
                ctx.__exit__(None, None, None)
        self.seconds += time.perf_counter() - t0
        self.round_trips += 1
        return out
