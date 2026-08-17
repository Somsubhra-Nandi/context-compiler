"""Batch chunking policy. No HydraDB required."""
from __future__ import annotations

from context_compiler.graph.client import (
    DEFAULT_BATCH,
    MAX_MESSAGE_BYTES,
    chunk_rows,
    row_bytes,
)


def test_chunks_respect_the_row_count_limit():
    rows = [{"v": i} for i in range(1050)]
    chunks = list(chunk_rows(rows, 500))
    assert [len(c) for c in chunks] == [500, 500, 50]


def test_every_row_appears_exactly_once_and_in_order():
    rows = [{"v": i} for i in range(997)]
    flat = [r for c in chunk_rows(rows, 250) for r in c]
    assert flat == rows


def test_chunks_respect_the_payload_budget():
    """Bolt rejects a message over 2 MiB; row count alone cannot prevent that."""
    rows = [{"v": i, "txt": "x" * 100_000} for i in range(40)]
    chunks = list(chunk_rows(rows, DEFAULT_BATCH, budget=1_500_000))
    assert len(chunks) > 1
    for c in chunks:
        assert sum(row_bytes(r) for r in c) <= 1_500_000
        assert sum(row_bytes(r) for r in c) < MAX_MESSAGE_BYTES


def test_an_oversized_single_row_is_still_yielded_alone():
    """Splitting further is impossible; a clear engine error beats a silent drop."""
    rows = [{"v": 1, "txt": "x" * 3_000_000}, {"v": 2, "txt": "y"}]
    chunks = list(chunk_rows(rows, DEFAULT_BATCH, budget=1_500_000))
    assert chunks[0] == [rows[0]]
    assert rows[1] in chunks[-1]


def test_budget_none_disables_payload_chunking():
    rows = [{"v": i, "txt": "x" * 100_000} for i in range(40)]
    assert len(list(chunk_rows(rows, 500, budget=None))) == 1


def test_empty_input_yields_nothing():
    assert list(chunk_rows([], 500)) == []


def test_row_bytes_counts_utf8_not_characters():
    assert row_bytes({"t": "e"}) < row_bytes({"t": "éé"})
