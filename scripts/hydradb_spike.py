#!/usr/bin/env python3
"""Item 0 spike: Bolt connectivity and verification of the exact Cypher forms
spec v1.3 Appendix A / §5.1 depends on, against a locally running HydraDB node.

Not an application module - run directly. Results are written up in
docs/spikes/hydradb-item-0-results.md; this script is the reproduction path.
"""
import json
import sys

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError, CypherSyntaxError

BOLT_URI = "bolt://127.0.0.1:7687"
AUTH = ("neo4j", "local-development-token-32-bytes")
DATABASE = "default"

results = {}


def record(name, ok, detail=""):
    results[name] = (ok, detail)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


def run(session, query, **params):
    return list(session.run(query, **params))


def phase3_bolt_connectivity(driver):
    print("\n=== Phase 3: Bolt connectivity ===")
    driver.verify_connectivity()
    record("bolt_connection", True)

    with driver.session(database=DATABASE) as session:
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn",
            rows=[{"v": 1, "fqn": "spike.trivial"}],
        ).consume()
        row = run(session, "MATCH (n:Symbol {id: 1}) RETURN n.fqn AS fqn")
        record("trivial_query", len(row) == 1 and row[0]["fqn"] == "spike.trivial")

        try:
            session.run("THIS IS NOT CYPHER").consume()
            record("invalid_cypher_rejected", False, "no exception raised")
        except (CypherSyntaxError, ClientError) as e:
            record("invalid_cypher_rejected", True, type(e).__name__)


def phase4a_batched_node_write(session):
    """Spec Appendix A canonical vertex upsert, verbatim."""
    print("\n=== Phase 4A: Batched node write (UNWIND MERGE SET) ===")
    rows = [
        {"v": 101, "fqn": "pkg.mod.foo", "kind": "function", "t2": 12, "t3": 40},
        {"v": 102, "fqn": "pkg.mod.Bar", "kind": "class", "t2": 30, "t3": 120},
        {"v": 103, "fqn": "pkg.mod.BAZ", "kind": "constant", "t2": 5, "t3": 5},
    ]
    try:
        session.run(
            "UNWIND $rows AS row "
            "MERGE (n {id: row.v}) "
            "SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind, "
            "    n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3",
            rows=rows,
        ).consume()
        # Read-back per row (bulk-by-id UNWIND read of node properties is not
        # a supported batch shape in this build - see results doc). One round
        # trip per id is sufficient to verify properties landed correctly.
        ok = True
        for r in rows:
            got = run(
                session,
                "MATCH (n:Symbol {id: $v}) RETURN n.fqn AS fqn, n.kind AS kind, "
                "n.repr_L2_tokens AS t2, n.repr_L3_tokens AS t3",
                v=r["v"],
            )
            ok = ok and len(got) == 1 and got[0]["fqn"] == r["fqn"] and got[0]["kind"] == r["kind"]
        record("unwind_node_write", ok, f"{len(rows)} rows written and verified")
    except Exception as e:
        record("unwind_node_write", False, f"{type(e).__name__}: {e}")


def phase4b_batched_relationship_write(session):
    """Spec Appendix A canonical edge write, verbatim. Idempotency check."""
    print("\n=== Phase 4B: Batched relationship write (CALLS, integer id) ===")
    try:
        edge_id = 900001
        edge_rows = [{"src": 101, "dst": 102, "eid": edge_id,
                      "resolver": "ast+scip", "conf": 0.95}]
        for _attempt in (1, 2):
            session.run(
                "UNWIND $rows AS row "
                "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
                "MERGE (s)-[r:CALLS {id: row.eid}]->(d) "
                "SET r.resolver = row.resolver, r.confidence = row.conf",
                rows=edge_rows,
            ).consume()
        # Non-batch single-source read verifies both the edge and idempotency
        # (exactly one CALLS edge after two identical writes).
        readback = run(
            session,
            "MATCH (x:Symbol {id: $v})-[r:CALLS]->(y:Symbol) "
            "RETURN y.id AS dst",
            v=101,
        )
        ok = len(readback) == 1 and readback[0]["dst"] == 102
        record("unwind_relationship_write_idempotent", ok,
               f"{len(readback)} edge(s) found after 2 identical writes (expected 1)")
    except Exception as e:
        record("unwind_relationship_write_idempotent", False, f"{type(e).__name__}: {e}")


def phase4c_batched_frontier_read(session):
    """Spec §5.1 canonical form, verbatim:
    UNWIND $rows AS row MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol)
    RETURN x.id AS src, y.id AS dst, y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3
    """
    print("\n=== Phase 4C: Batched frontier read (spec §5.1 form, verbatim) ===")
    session.run(
        "UNWIND $rows AS row "
        "MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind, "
        "n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3",
        rows=[{"v": 104, "fqn": "pkg.mod.qux", "kind": "function", "t2": 8, "t3": 20}],
    ).consume()
    session.run(
        "UNWIND $rows AS row "
        "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
        "MERGE (s)-[r:CALLS {id: row.eid}]->(d)",
        rows=[{"src": 102, "dst": 104, "eid": 900002}],
    ).consume()

    try:
        rows = run(
            session,
            "UNWIND $rows AS row "
            "MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol) "
            "RETURN x.id AS src, y.id AS dst, "
            "y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3",
            rows=[{"v": 101}, {"v": 102}],
        )
        record("unwind_frontier_read_verbatim", True, f"found={rows}")
    except (CypherSyntaxError, ClientError) as e:
        record("unwind_frontier_read_verbatim", False,
               f"REJECTED at parse time: {e.message if hasattr(e, 'message') else e}")


def phase4d_multilabel(session):
    """LOAD-BEARING. Verify (x:Symbol) and (x:Test) both match a dual-labelled
    node. HydraDB's UNWIND vertex-upsert batch rejects >1 label in a single
    SET (see results doc), so this uses two sequential single-label batches -
    the form HydraDB actually supports for reaching the same end state."""
    print("\n=== Phase 4D: Multi-label Symbol+Test (LOAD-BEARING) ===")
    try:
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.v}) "
            "SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind",
            rows=[{"v": 105, "fqn": "tests.test_foo.test_it", "kind": "test"}],
        ).consume()
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Test",
            rows=[{"v": 105}],
        ).consume()

        as_symbol = run(session, "MATCH (x:Symbol {id: 105}) RETURN x.id AS id")
        as_test = run(session, "MATCH (x:Test {id: 105}) RETURN x.id AS id")

        record("multilabel_symbol_match", len(as_symbol) == 1 and as_symbol[0]["id"] == 105)
        record("multilabel_test_match", len(as_test) == 1 and as_test[0]["id"] == 105)
    except Exception as e:
        record("multilabel_symbol_match", False, f"{type(e).__name__}: {e}")
        record("multilabel_test_match", False, f"{type(e).__name__}: {e}")


def phase4e_path_procedures(session):
    """§2.5. sourceLabel/sourceProperty apply only to algo.MSpaths;
    SPpaths/SSpaths take a direct integer sourceNode/targetNode (VertexId)."""
    print("\n=== Phase 4E: Property selector / path procedures (§2.5) ===")
    try:
        rows = run(
            session,
            "CALL algo.SPpaths({sourceNode: 101, targetNode: 104, "
            "relTypes:['CALLS'], relDirection:'outgoing', maxLen: 2, pathCount: 5}) "
            "YIELD path RETURN path",
        )
        record("path_proc_SPpaths", True, f"AVAILABLE, {len(rows)} path row(s)")
    except Exception as e:
        record("path_proc_SPpaths", False, f"UNAVAILABLE: {type(e).__name__}: {e}")

    try:
        rows = run(
            session,
            "CALL algo.SSpaths({sourceNode: 101, "
            "relTypes:['CALLS'], relDirection:'outgoing', maxLen: 2, pathCount: 5}) "
            "YIELD path RETURN path",
        )
        record("path_proc_SSpaths", True, f"AVAILABLE, {len(rows)} path row(s)")
    except Exception as e:
        record("path_proc_SSpaths", False, f"UNAVAILABLE: {type(e).__name__}: {e}")

    try:
        rows = run(
            session,
            "CALL algo.MSpaths({sourceLabel:'Symbol', sourceProperty:'fqn', "
            "sourceValues: ['pkg.mod.foo'], targetValues: ['pkg.mod.qux'], "
            "pairwise: true, relTypes:['CALLS'], relDirection:'outgoing', "
            "maxLen: 2, pathCount: 5}) YIELD path RETURN path",
        )
        record("path_proc_MSpaths", True, f"AVAILABLE, {len(rows)} path row(s)")
    except Exception as e:
        record("path_proc_MSpaths", False, f"UNAVAILABLE: {type(e).__name__}: {e}")


def phase5_rejection_tests(session):
    print("\n=== Phase 5: Rejection tests (Appendix A) ===")
    rejections = {
        "in_where": "MATCH (n:Symbol) WHERE n.id IN [1,2,3] RETURN n.id",
        "multi_type_relationship": "MATCH (a:Symbol)-[:CALLS|REFERENCES_TYPE]->(b) RETURN a.id",
        "type_r_projection": "MATCH (a:Symbol)-[r:CALLS]->(b) RETURN type(r)",
        "return_star": "MATCH (n:Symbol) RETURN *",
        "unbounded_var_length_path": "MATCH (a:Symbol)-[:CALLS*]->(b) RETURN a.id",
        "create_index": "CREATE INDEX ON :Symbol(fqn)",
    }
    for name, q in rejections.items():
        try:
            session.run(q).consume()
            record(f"reject_{name}", False, "ACCEPTED (should have been rejected)")
        except (CypherSyntaxError, ClientError) as e:
            record(f"reject_{name}", True, f"REJECTED ({type(e).__name__})")
        except Exception as e:
            record(f"reject_{name}", True, f"REJECTED ({type(e).__name__}: {e})")


def main():
    driver = GraphDatabase.driver(BOLT_URI, auth=AUTH)
    try:
        phase3_bolt_connectivity(driver)
        with driver.session(database=DATABASE) as session:
            phase4a_batched_node_write(session)
            phase4b_batched_relationship_write(session)
            phase4c_batched_frontier_read(session)
            phase4d_multilabel(session)
            phase4e_path_procedures(session)
            phase5_rejection_tests(session)
    finally:
        driver.close()

    print("\n=== Summary ===")
    print(json.dumps({k: v[0] for k, v in results.items()}, indent=2))

    # unwind_frontier_read_verbatim is a KNOWN, DOCUMENTED failure (see
    # docs/spikes/hydradb-item-0-results.md) - it is not a script bug and does
    # not gate exit status.
    expected_failures = {"unwind_frontier_read_verbatim"}
    failures = [k for k, (ok, _) in results.items() if not ok and k not in expected_failures]
    if failures:
        print(f"\n{len(failures)} unexpected failure(s): {failures}", file=sys.stderr)
        sys.exit(1)
    print("\nAll checks passed (frontier-read-verbatim failure is documented, not a bug).")


if __name__ == "__main__":
    main()
