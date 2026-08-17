"""Bolt session management, batch chunking and retry for HydraDB.

Everything in this module obeys the accepted-grammar constraints recorded in
docs/spikes/hydradb-item-0-results.md and Amendment A1:

* all vertex/edge upserts go through ``UNWIND $rows AS row`` -- the non-batched
  ``MERGE ... SET ...`` form is rejected outright (A1.2);
* ``UNWIND`` only works over the Bolt/HTTP transport (spec Appendix A);
* parameter values must be boolean, signed integer, finite float or string --
  ``None`` is rejected at the parameter layer, see ``MAX_STRING_PROPERTY``.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from neo4j import GraphDatabase
from neo4j.exceptions import (
    ClientError,
    DatabaseError,
    ServiceUnavailable,
    TransientError,
)

BOLT_URI = os.environ.get("HYDRADB_BOLT_URI", "bolt://127.0.0.1:7687")
AUTH = (
    os.environ.get("HYDRADB_USER", "neo4j"),
    os.environ.get("HYDRADB_TOKEN", "local-development-token-32-bytes"),
)
DATABASE = os.environ.get("HYDRADB_DATABASE", "default")

#: Largest string a property value may hold. Measured by bisection against
#: HydraDB 6a2fbb19: 32,743 bytes accepted with a 3-character property key,
#: 32,744 rejected with ``internal query execution error``. The budget covers
#: the key and framing too, so a longer key lowers the ceiling -- hence the
#: conservative figure used here rather than the measured maximum.
MAX_STRING_PROPERTY = 32_000

#: Default rows per UNWIND batch (spec Sec 5.1 says start at 500).
DEFAULT_BATCH = 500

RETRYABLE = (TransientError, ServiceUnavailable, DatabaseError)


@dataclass
class BatchStats:
    """Round-trip and timing accounting for one logical pass."""

    name: str
    requests: int = 0
    rows: int = 0
    seconds: float = 0.0
    retries: int = 0
    splits: int = 0

    @property
    def rows_per_second(self) -> float:
        return self.rows / self.seconds if self.seconds else 0.0

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return (
            f"{self.name:<28} {self.rows:>9,} rows  {self.requests:>6,} req  "
            f"{self.seconds:>8.2f}s  {self.rows_per_second:>10,.0f} rows/s"
            + (f"  retries={self.retries}" if self.retries else "")
            + (f"  splits={self.splits}" if self.splits else "")
        )


@dataclass
class GraphClient:
    """Thin wrapper owning one driver and the batch/retry policy."""

    uri: str = BOLT_URI
    auth: tuple[str, str] = AUTH
    database: str = DATABASE
    batch_size: int = DEFAULT_BATCH
    max_retries: int = 3
    _driver: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._driver = GraphDatabase.driver(self.uri, auth=self.auth)

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    @contextmanager
    def session(self):
        with self._driver.session(database=self.database) as s:
            yield s

    # -- reads -----------------------------------------------------------

    def read(self, query: str, **params: object) -> list[dict]:
        """Single-source (non-batched) read. Full Cypher subset available."""
        with self.session() as s:
            return [dict(r) for r in s.run(query, **params)]

    def count(self, pattern: str) -> int:
        """``MATCH <pattern> RETURN count(*)``.

        ``count(n)`` on a binding is rejected by this engine; ``count(*)`` with
        a labelled pattern works.
        """
        rows = self.read(f"MATCH {pattern} RETURN count(*) AS c")
        return rows[0]["c"] if rows else 0

    # -- batched writes --------------------------------------------------

    def run_batches(
        self,
        query: str,
        rows: list[dict],
        stats: BatchStats,
        batch_size: int | None = None,
        session=None,
    ) -> None:
        """Execute ``query`` over ``rows`` in chunks, retrying transient errors.

        A batch that keeps failing is split in half and retried, which isolates
        an oversized single row (e.g. a >32 KiB string property) instead of
        losing the whole chunk.
        """
        size = batch_size or self.batch_size
        if session is not None:
            self._run_chunked(session, query, rows, stats, size)
            return
        with self.session() as s:
            self._run_chunked(s, query, rows, stats, size)

    def _run_chunked(self, s, query: str, rows: list[dict], stats: BatchStats, size: int) -> None:
        for start in range(0, len(rows), size):
            self._run_one(s, query, rows[start : start + size], stats)

    def _run_one(self, s, query: str, chunk: list[dict], stats: BatchStats) -> None:
        if not chunk:
            return
        delay = 0.25
        for attempt in range(self.max_retries):
            t0 = time.perf_counter()
            try:
                s.run(query, rows=chunk).consume()
            except RETRYABLE:
                stats.seconds += time.perf_counter() - t0
                if attempt == self.max_retries - 1:
                    if len(chunk) == 1:
                        raise
                    # Isolate the offending row rather than dropping the chunk.
                    stats.splits += 1
                    mid = len(chunk) // 2
                    self._run_one(s, query, chunk[:mid], stats)
                    self._run_one(s, query, chunk[mid:], stats)
                    return
                stats.retries += 1
                time.sleep(delay)
                delay *= 2
            except ClientError:
                stats.seconds += time.perf_counter() - t0
                raise
            else:
                stats.seconds += time.perf_counter() - t0
                stats.requests += 1
                stats.rows += len(chunk)
                return

    # -- batched reads (Amendment A1.1 shape) ----------------------------

    def run_batch_read(
        self,
        query: str,
        rows: list[dict],
        stats: BatchStats,
        batch_size: int | None = None,
        session=None,
    ) -> list[dict]:
        """Execute an ``UNWIND`` batch *read* over ``rows`` in chunks.

        The query must obey A1.1: no labels on either endpoint and exactly two
        projections.
        """
        size = batch_size or self.batch_size
        if session is not None:
            return self._read_chunked(session, query, rows, stats, size)
        with self.session() as s:
            return self._read_chunked(s, query, rows, stats, size)

    def _read_chunked(self, s, query, rows, stats: BatchStats, size: int) -> list[dict]:
        out: list[dict] = []
        for start in range(0, len(rows), size):
            chunk = rows[start : start + size]
            if not chunk:
                continue
            t0 = time.perf_counter()
            out.extend(dict(r) for r in s.run(query, rows=chunk))
            stats.seconds += time.perf_counter() - t0
            stats.requests += 1
            stats.rows += len(chunk)
        return out


def connect(**kwargs: object) -> GraphClient:
    """Open a verified client, or raise ``ServiceUnavailable``."""
    c = GraphClient(**kwargs)
    c.verify()
    return c
