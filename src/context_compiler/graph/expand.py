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
