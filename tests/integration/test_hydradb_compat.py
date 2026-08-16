"""HydraDB compatibility guardrails for Context Compiler spec v1.3.

Requires a running local HydraDB node (scripts/run_hydradb.sh start).
Tests are skipped, not failed, if the node is unreachable, so this suite is
safe to run in environments without HydraDB.

See docs/spikes/hydradb-item-0-results.md for the full investigation these
tests are distilled from, including why unwind_frontier_read is xfail.
"""
import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError, CypherSyntaxError, ServiceUnavailable

BOLT_URI = "bolt://127.0.0.1:7687"
AUTH = ("neo4j", "local-development-token-32-bytes")
DATABASE = "default"


@pytest.fixture(scope="module")
def driver():
    drv = GraphDatabase.driver(BOLT_URI, auth=AUTH)
    try:
        drv.verify_connectivity()
    except ServiceUnavailable:
        drv.close()
        pytest.skip("HydraDB node not reachable at 127.0.0.1:7687 - run scripts/run_hydradb.sh start")
    yield drv
    drv.close()


@pytest.fixture()
def session(driver):
    with driver.session(database=DATABASE) as s:
        yield s


def rejects(session, query):
    with pytest.raises((CypherSyntaxError, ClientError)):
        session.run(query).consume()


# --- Supported subset: UNWIND $rows AS row -----------------------------


def test_unwind_node_write_and_readback(session):
    rows = [
        {"v": 100101, "fqn": "compat.write.a", "kind": "function", "t2": 12, "t3": 40},
        {"v": 100102, "fqn": "compat.write.b", "kind": "class", "t2": 30, "t3": 120},
    ]
    session.run(
        "UNWIND $rows AS row "
        "MERGE (n {id: row.v}) "
        "SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind, "
        "    n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3",
        rows=rows,
    ).consume()
    for r in rows:
        got = list(session.run(
            "MATCH (n:Symbol {id: $v}) RETURN n.fqn AS fqn, n.kind AS kind, "
            "n.repr_L2_tokens AS t2, n.repr_L3_tokens AS t3",
            v=r["v"],
        ))
        assert len(got) == 1
        assert got[0]["fqn"] == r["fqn"]
        assert got[0]["kind"] == r["kind"]
        assert got[0]["t2"] == r["t2"]
        assert got[0]["t3"] == r["t3"]


def test_unwind_relationship_write_is_idempotent(session):
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn",
        rows=[{"v": 100201, "fqn": "compat.edge.src"}, {"v": 100202, "fqn": "compat.edge.dst"}],
    ).consume()
    edge_rows = [{"src": 100201, "dst": 100202, "eid": 100901,
                  "resolver": "ast+scip", "conf": 0.95}]
    for _ in range(2):
        session.run(
            "UNWIND $rows AS row "
            "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
            "MERGE (s)-[r:CALLS {id: row.eid}]->(d) "
            "SET r.resolver = row.resolver, r.confidence = row.conf",
            rows=edge_rows,
        ).consume()
    readback = list(session.run(
        "MATCH (x:Symbol {id: $v})-[r:CALLS]->(y:Symbol) RETURN y.id AS dst",
        v=100201,
    ))
    assert len(readback) == 1, "CALLS edge must stay a single relationship across re-ingest"
    assert readback[0]["dst"] == 100202


def test_unwind_frontier_read_verbatim_spec_form_is_rejected(session):
    """DOCUMENTED FAILURE, not a regression to fix here.

    Spec v1.3 §5.1's canonical batched frontier read:

        UNWIND $rows AS row
          MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol)
          RETURN x.id AS src, y.id AS dst,
                 y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3

    is rejected by HydraDB @ 6a2fbb192f37f51a93690a2ae2d2f5e27e6e4219: the
    UNWIND-batch read path forbids ANY label on the matched nodes and allows
    exactly two projections (row.<field>, dest.id) - see results doc for the
    full grammar this build actually accepts for UNWIND batch reads.

    This test pins that rejection so a future HydraDB upgrade that starts
    accepting the verbatim form is caught (xfail -> unexpected pass), rather
    than silently reintroducing an assumption the spec author needs to know
    about.
    """
    query = (
        "UNWIND $rows AS row "
        "MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol) "
        "RETURN x.id AS src, y.id AS dst, "
        "y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3"
    )
    with pytest.raises((CypherSyntaxError, ClientError)):
        session.run(query, rows=[{"v": 100201}]).consume()


def test_unwind_frontier_read_minimal_supported_form(session):
    """The form HydraDB actually accepts: no labels, exactly two projections
    (row.<sourceField>, dest.id)."""
    rows = list(session.run(
        "UNWIND $rows AS row "
        "MATCH (x {id: row.v})-[:CALLS]->(y) "
        "RETURN row.v AS src, y.id AS dst",
        rows=[{"v": 100201}],
    ))
    assert rows == [{"src": 100201, "dst": 100202}]


def test_multilabel_symbol_and_test_both_match(session):
    """LOAD-BEARING for I6 (spec §2.1). A single UNWIND SET with two labels
    is rejected ("UNWIND vertex upsert requires exactly one SET label"), so
    dual-labelling requires two sequential single-label UNWIND batches - the
    form HydraDB supports for this."""
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) "
        "SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind",
        rows=[{"v": 100301, "fqn": "tests.compat.test_it", "kind": "test"}],
    ).consume()
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Test",
        rows=[{"v": 100301}],
    ).consume()

    as_symbol = list(session.run("MATCH (x:Symbol {id: 100301}) RETURN x.id AS id"))
    as_test = list(session.run("MATCH (x:Test {id: 100301}) RETURN x.id AS id"))
    assert len(as_symbol) == 1 and as_symbol[0]["id"] == 100301
    assert len(as_test) == 1 and as_test[0]["id"] == 100301


def test_multilabel_single_set_call_is_rejected(session):
    """Pins the constraint the two tests above work around."""
    with pytest.raises((CypherSyntaxError, ClientError)):
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n:Test",
            rows=[{"v": 100302}],
        ).consume()


def test_bounded_variable_length_path_available(session):
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn",
        rows=[{"v": 100401, "fqn": "a"}, {"v": 100402, "fqn": "b"}, {"v": 100403, "fqn": "c"}],
    ).consume()
    session.run(
        "UNWIND $rows AS row "
        "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
        "MERGE (s)-[r:CALLS {id: row.eid}]->(d)",
        rows=[{"src": 100401, "dst": 100402, "eid": 101001},
              {"src": 100402, "dst": 100403, "eid": 101002}],
    ).consume()
    rows = list(session.run(
        "MATCH (a:Symbol {id: $v})-[:CALLS*1..2]->(b:Symbol) RETURN b.id AS id",
        v=100401,
    ))
    found = {r["id"] for r in rows}
    assert found == {100402, 100403}


# --- Path procedures (§2.5) --------------------------------------------


def test_sppaths_available(session):
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn",
        rows=[{"v": 100501, "fqn": "a"}, {"v": 100502, "fqn": "b"}],
    ).consume()
    session.run(
        "UNWIND $rows AS row "
        "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
        "MERGE (s)-[r:CALLS {id: row.eid}]->(d)",
        rows=[{"src": 100501, "dst": 100502, "eid": 101101}],
    ).consume()
    rows = list(session.run(
        "CALL algo.SPpaths({sourceNode: 100501, targetNode: 100502, "
        "relTypes:['CALLS'], relDirection:'outgoing', maxLen: 2, pathCount: 5}) "
        "YIELD path RETURN path"
    ))
    assert len(rows) == 1


def test_sspaths_available(session):
    rows = list(session.run(
        "CALL algo.SSpaths({sourceNode: 100501, "
        "relTypes:['CALLS'], relDirection:'outgoing', maxLen: 2, pathCount: 5}) "
        "YIELD path RETURN path"
    ))
    assert len(rows) >= 1


def test_mspaths_requires_string_valued_selector_property(session):
    """§2.5: sourceValues/targetValues for algo.MSpaths must resolve to
    STRING-valued properties. `fqn` works; a bare `id` (INT) selector does
    not - see results doc."""
    session.run(
        "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn",
        rows=[{"v": 100601, "fqn": "compat.mspaths.src"},
              {"v": 100602, "fqn": "compat.mspaths.dst"}],
    ).consume()
    session.run(
        "UNWIND $rows AS row "
        "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
        "MERGE (s)-[r:CALLS {id: row.eid}]->(d)",
        rows=[{"src": 100601, "dst": 100602, "eid": 101201}],
    ).consume()
    rows = list(session.run(
        "CALL algo.MSpaths({sourceLabel:'Symbol', sourceProperty:'fqn', "
        "sourceValues: ['compat.mspaths.src'], targetValues: ['compat.mspaths.dst'], "
        "pairwise: true, relTypes:['CALLS'], relDirection:'outgoing', "
        "maxLen: 2, pathCount: 5}) YIELD path RETURN path"
    ))
    assert len(rows) == 1


# --- Rejection tests (Appendix A) - permanent guardrails ----------------


def test_reject_in_where(session):
    rejects(session, "MATCH (n:Symbol) WHERE n.id IN [1,2,3] RETURN n.id")


def test_reject_multiple_relationship_types(session):
    rejects(session, "MATCH (a:Symbol)-[:CALLS|REFERENCES_TYPE]->(b) RETURN a.id")


def test_reject_type_function_projection(session):
    rejects(session, "MATCH (a:Symbol)-[r:CALLS]->(b) RETURN type(r)")


def test_reject_return_star(session):
    rejects(session, "MATCH (n:Symbol) RETURN *")


def test_reject_unbounded_variable_length_path(session):
    rejects(session, "MATCH (a:Symbol)-[:CALLS*]->(b) RETURN a.id")


def test_reject_neo4j_style_create_index(session):
    rejects(session, "CREATE INDEX ON :Symbol(fqn)")
