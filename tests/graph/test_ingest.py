"""Item 3 ingest against a live HydraDB node.

Requires a running node (``scripts/run_hydradb.sh start``) with the Django
graph already ingested::

    bash scripts/run_hydradb.sh reset
    python -m context_compiler.graph.ingest \
        --symbols ~/out/django/symbols.jsonl --edges ~/out/django/edges.jsonl

Tests skip rather than fail when the node is unreachable or the graph is
empty, so the suite is safe in environments without HydraDB.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

from context_compiler.graph.client import GraphClient
from context_compiler.graph.ingest import (
    MANDATORY_EDGES,
    Q_SYMBOL,
    Q_TEST_LABEL,
    edge_row,
    eid,
    q_edge,
    verify,
)

SYMBOLS = Path(os.environ.get("CC_SYMBOLS", "~/out/django/symbols.jsonl")).expanduser()
EDGES = Path(os.environ.get("CC_EDGES", "~/out/django/edges.jsonl")).expanduser()

EXPECTED_SYMBOLS = 43_420
EXPECTED_INHERITS_FROM = 7_149


@pytest.fixture(scope="module")
def client():
    try:
        c = GraphClient()
        c.verify()
    except ServiceUnavailable:
        pytest.skip("HydraDB not reachable - run scripts/run_hydradb.sh start")
    yield c
    c.close()


@pytest.fixture(scope="module")
def node_counts(client):
    """Node counts only. A labelled node count stays inside the deadline."""
    c = {"Symbol": client.count("(n:Symbol)"), "Test": client.count("(n:Test)")}
    if c["Symbol"] == 0:
        pytest.skip("graph is empty - run the ingest CLI first")
    return c


@pytest.fixture(scope="module")
def counts(client):
    """Full read-back counts, nodes and every relationship type.

    A whole-graph relationship count exceeds the engine's 29,999 ms deadline at
    Django scale, so `verify()` sums out-degree through the chunked, label-free
    A1.1 batch form over every node id. That is ~600 requests and takes about
    11 minutes, so it is cached for the module and skipped unless
    CC_FULL_VERIFY=1 is set.
    """
    if not os.environ.get("CC_FULL_VERIFY"):
        pytest.skip("set CC_FULL_VERIFY=1 for the full read-back (~11 min)")
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    ids = [json.loads(line)["id"] for line in open(SYMBOLS, "rb")]
    c = verify(client, ids)
    if c["Symbol"] == 0:
        pytest.skip("graph is empty - run the ingest CLI first")
    return c


# -- eid derivation (Sec 2.1.3) -----------------------------------------


def test_eid_is_deterministic_across_calls():
    a = eid("CALLS", 111, 222)
    b = eid("CALLS", 111, 222)
    assert a == b


def test_eid_is_a_non_negative_63_bit_integer():
    for etype in MANDATORY_EDGES:
        v = eid(etype, 1, 2)
        assert 0 <= v < 2**63


def test_eid_separates_type_src_and_dst():
    assert eid("CALLS", 1, 2) != eid("REFERENCES_TYPE", 1, 2)
    assert eid("CALLS", 1, 2) != eid("CALLS", 2, 1)
    assert eid("CALLS", 1, 2) != eid("CALLS", 1, 3)


def test_eid_matches_the_spec_formula():
    """Pin the exact digest so a refactor cannot silently change edge identity."""
    from hashlib import blake2b

    expected = int.from_bytes(blake2b(b"CALLS|1|2", digest_size=8).digest(), "big") >> 1
    assert eid("CALLS", 1, 2) == expected


def test_eid_revision_scoping_is_available_for_runtime_edges():
    """I5: runtime edges must not collapse onto one stable id (Sec 2.1.3)."""
    assert eid("OBSERVED_CALLS", 1, 2, "rev-a") != eid("OBSERVED_CALLS", 1, 2, "rev-b")
    assert eid("OBSERVED_CALLS", 1, 2, "rev-a") != eid("OBSERVED_CALLS", 1, 2)


def test_eid_is_stable_across_a_fresh_process():
    """blake2b is not salted per-process; re-derivation must reproduce ids."""
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from context_compiler.graph.ingest import eid; print(eid('CALLS', 7, 9))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(out.stdout.strip()) == eid("CALLS", 7, 9)


def test_edge_rows_derive_ids_reproducibly_from_the_jsonl():
    if not EDGES.exists():
        pytest.skip(f"{EDGES} not present")
    rows_a, rows_b = [], []
    with open(EDGES, "rb") as fh:
        for i, line in enumerate(fh):
            if i >= 500:
                break
            rec = json.loads(line)
            rows_a.append(edge_row(rec))
            rows_b.append(edge_row(json.loads(line)))
    assert rows_a == rows_b
    assert len({r["eid"] for r in rows_a}) == len(rows_a), "eid collision in first 500 edges"


# -- node and edge counts land and read back ----------------------------


def test_all_symbols_landed(node_counts):
    assert node_counts["Symbol"] == EXPECTED_SYMBOLS


def test_mandatory_edge_count_matches_the_contract(counts):
    for etype in MANDATORY_EDGES:
        assert counts[etype] >= 0, f"{etype} count query hit the engine deadline"
    if not EDGES.exists():
        pytest.skip(f"{EDGES} not present")
    expected = 0
    with open(EDGES, "rb") as fh:
        for line in fh:
            if json.loads(line)["type"] in MANDATORY_EDGES:
                expected += 1
    assert counts["mandatory_edges"] == expected


def test_inherits_from_is_stored_but_separate(counts):
    """Sec 2.2: stored for display and ranking, never traversed."""
    assert counts["INHERITS_FROM"] == EXPECTED_INHERITS_FROM
    assert "INHERITS_FROM" not in MANDATORY_EDGES


def test_per_type_edge_counts_match_the_jsonl(client, counts):
    if not EDGES.exists():
        pytest.skip(f"{EDGES} not present")
    from collections import Counter

    src = Counter()
    with open(EDGES, "rb") as fh:
        for line in fh:
            src[json.loads(line)["type"]] += 1
    for etype, n in src.items():
        assert counts[etype] == n, f"{etype}: graph {counts[etype]} != jsonl {n}"


def test_node_scalars_read_back(client):
    """Spot-check a real Django symbol's scalar properties against the JSONL."""
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    with open(SYMBOLS, "rb") as fh:
        rec = json.loads(fh.readline())
    got = client.read(
        "MATCH (n:Symbol {id: $v}) RETURN n.fqn AS fqn, n.kind AS kind, "
        "n.file AS file, n.start_line AS sl, n.body_hash AS bh, "
        "n.repr_L2_tokens AS t2, n.repr_L3_tokens AS t3, "
        "n.identity_tokens AS it, n.provenance_tokens AS pt",
        v=rec["id"],
    )
    assert len(got) == 1
    row = got[0]
    assert row["fqn"] == rec["fqn"]
    assert row["kind"] == rec["kind"]
    assert row["file"] == rec["file"]
    assert row["sl"] == rec["start_line"]
    assert row["bh"] == rec["body_hash"]
    assert row["t2"] == rec["repr_L2_tokens"]
    assert row["t3"] == rec["repr_L3_tokens"]
    assert row["it"] == rec["identity_tokens"]
    assert row["pt"] == rec["provenance_tokens"]


# -- dual labelling (A1.2, I6) ------------------------------------------


def test_dual_label_on_a_real_test_symbol(client, node_counts):
    """A ``kind == "test"`` node must match both ``(x:Symbol)`` and ``(x:Test)``."""
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    target = None
    with open(SYMBOLS, "rb") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["kind"] == "test":
                target = rec
                break
    assert target is not None, "no kind == 'test' symbol in the fixture"

    as_symbol = client.read("MATCH (x:Symbol {id: $v}) RETURN x.fqn AS fqn", v=target["id"])
    as_test = client.read("MATCH (x:Test {id: $v}) RETURN x.fqn AS fqn", v=target["id"])
    assert len(as_symbol) == 1 and as_symbol[0]["fqn"] == target["fqn"]
    assert len(as_test) == 1 and as_test[0]["fqn"] == target["fqn"]


def test_test_label_covers_exactly_the_test_kind_subset(client, node_counts):
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    n_test = sum(
        1 for line in open(SYMBOLS, "rb") if json.loads(line)["kind"] == "test"
    )
    assert node_counts["Test"] == n_test


def test_non_test_symbol_does_not_carry_the_test_label(client):
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    target = None
    with open(SYMBOLS, "rb") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["kind"] == "function":
                target = rec
                break
    assert target is not None
    assert client.read("MATCH (x:Test {id: $v}) RETURN x.fqn AS fqn", v=target["id"]) == []


# -- idempotency ---------------------------------------------------------


def test_reingesting_a_slice_creates_no_duplicates(client, node_counts):
    """Re-running the real upsert queries over real rows must be a no-op.

    This uses genuine Django rows against the already-populated graph, so it
    exercises the same MERGE paths the CLI uses rather than a synthetic case.
    """
    if not (SYMBOLS.exists() and EDGES.exists()):
        pytest.skip("fixtures not present")

    ids = []
    with open(SYMBOLS, "rb") as fh:
        for i, line in enumerate(fh):
            if i >= 200:
                break
            ids.append(json.loads(line)["id"])
    keep = set(ids)

    edges_by_type: dict[str, list[dict]] = {}
    with open(EDGES, "rb") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["src"] in keep and rec["dst"] in keep:
                edges_by_type.setdefault(rec["type"], []).append(rec)
    if not edges_by_type:
        pytest.skip("no intra-slice edges in the first 200 symbols")

    probe_src = next(iter(edges_by_type.values()))[0]["src"]

    def fanout() -> dict[str, int]:
        return {
            et: len(
                client.read(
                    f"MATCH (x:Symbol {{id: $v}})-[:{et}]->(y:Symbol) RETURN y.id AS dst",
                    v=probe_src,
                )
            )
            for et in edges_by_type
        }

    before_nodes = client.count("(n:Symbol)")
    before = fanout()

    from context_compiler.graph.client import BatchStats
    from context_compiler.graph.ingest import symbol_row

    rows = []
    with open(SYMBOLS, "rb") as fh:
        for i, line in enumerate(fh):
            if i >= 200:
                break
            rows.append(symbol_row(json.loads(line)))
    client.run_batches(Q_SYMBOL, rows, BatchStats("reingest-nodes"))
    for etype, recs in edges_by_type.items():
        client.run_batches(
            q_edge(etype, etype == "CALLS"),
            [edge_row(r) for r in recs],
            BatchStats(f"reingest-{etype}"),
        )

    assert client.count("(n:Symbol)") == before_nodes, "re-ingest created duplicate nodes"
    assert fanout() == before, "re-ingest created duplicate relationships"


def test_reingesting_the_test_label_pass_is_idempotent(client, node_counts):
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    from context_compiler.graph.client import BatchStats

    target = None
    with open(SYMBOLS, "rb") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["kind"] == "test":
                target = rec
                break
    before = node_counts["Test"]
    client.run_batches(Q_TEST_LABEL, [{"v": target["id"]}], BatchStats("reingest-test"))
    assert client.count("(n:Test)") == before
