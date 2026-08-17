"""Item 4 against the real Django graph in HydraDB.

Cross-validates the fixpoint against a prediction made before this code
existed (see `context_compiler.graph.validate.PREDICTION`), and pins the
`expand()` query shape and round-trip cost.

Requires a running node with Django ingested::

    bash scripts/run_hydradb.sh reset
    python -m context_compiler.graph.ingest \
        --symbols ~/out/django/symbols.jsonl --edges ~/out/django/edges.jsonl
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from neo4j.exceptions import ClientError, CypherSyntaxError, ServiceUnavailable

from context_compiler.graph.client import GraphClient
from context_compiler.graph.closure import L1, L2, L3, closure, source_cost
from context_compiler.graph.expand import HARD_EDGES, QUERIES, Expander, expected_round_trips
from context_compiler.graph.sidecar import load_sidecar
from context_compiler.graph.validate import (
    PREDICTION,
    compare,
    eligible_seeds,
    provenance_is_complete,
    run_trials,
    sample_seed_sets,
    within_an_order_of_magnitude,
)

SYMBOLS = Path(os.environ.get("CC_SYMBOLS", "~/out/django/symbols.jsonl")).expanduser()

#: 200 x 6 seeds takes a few minutes; the default here is a fast subsample that
#: still reproduces the distribution. Set CC_TRIALS=200 for the full run.
TRIALS = int(os.environ.get("CC_TRIALS", "60"))


@pytest.fixture(scope="module")
def client():
    try:
        c = GraphClient()
        c.verify()
    except ServiceUnavailable:
        pytest.skip("HydraDB not reachable - run scripts/run_hydradb.sh start")
    if c.count("(n:Symbol)") == 0:
        c.close()
        pytest.skip("graph is empty - run the ingest CLI first")
    yield c
    c.close()


@pytest.fixture(scope="module")
def sidecar():
    if not SYMBOLS.exists():
        pytest.skip(f"{SYMBOLS} not present")
    return load_sidecar(SYMBOLS)


@pytest.fixture(scope="module")
def expander(client, sidecar):
    with Expander(client, membership=sidecar) as e:
        yield e


@pytest.fixture(scope="module")
def distribution(expander, sidecar):
    pool = eligible_seeds(SYMBOLS)
    sets = sample_seed_sets(pool, TRIALS, PREDICTION["seeds_per_trial"])
    return run_trials(sets, expander, sidecar).summary()


# -- the expand() query shape -------------------------------------------


def test_expand_query_shape_is_the_amended_form(client):
    """A1.1: no labels on either endpoint, exactly two projections."""
    for et, q in QUERIES.items():
        assert f"MATCH (x {{id: row.v}})-[:{et}]->(y)" in q
        assert "RETURN row.v AS src, y.id AS dst" in q
        assert ":Symbol" not in q


def test_verbatim_spec_form_is_still_rejected(client):
    """Pins the A1.1 rejection. An upgrade that accepts it flags the amendment."""
    q = (
        "UNWIND $rows AS row "
        "MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol) "
        "RETURN x.id AS src, y.id AS dst, "
        "y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3"
    )
    with client.session() as s:
        with pytest.raises((CypherSyntaxError, ClientError)):
            s.run(q, rows=[{"v": 1}]).consume()


def test_expand_returns_real_edges(expander, sidecar):
    """Pick a well-connected seed and confirm expand() finds its callees."""
    pool = eligible_seeds(SYMBOLS)
    for seed in pool[:200]:
        edges = expander.expand([seed])
        if edges:
            for src, et, dst in edges:
                assert src == seed
                assert et in HARD_EDGES
                assert dst in sidecar
            return
    pytest.fail("no eligible seed in the first 200 produced any hard edge")


def test_expand_costs_six_round_trips_for_a_single_source(client, sidecar):
    """Sec 5.1 / A1.1 cost model: six typed queries per productive hop."""
    with Expander(client, membership=sidecar) as e:
        e.expand([eligible_seeds(SYMBOLS)[0]])
        assert e.stats.round_trips == len(HARD_EDGES) == 6


def test_two_hop_closure_costs_twelve_round_trips(client, sidecar):
    """12 batched requests for an unchunked two-hop closure."""
    pool = eligible_seeds(SYMBOLS)
    for seed in pool[:200]:
        with Expander(client, membership=sidecar) as e:
            result = closure({seed: L3}, e)
            if e.stats.hops == 2:
                assert e.stats.round_trips == 12, e.stats.per_hop
                assert result.hops_run == 2
                return
    pytest.fail("no seed in the first 200 produced a second productive hop")


def test_round_trip_count_matches_the_chunking_formula(client, sidecar):
    """6 * ceil(|frontier|/B) per hop, per Sec 5.1."""
    frontier = eligible_seeds(SYMBOLS)[:1200]
    with Expander(client, membership=sidecar, batch_size=500) as e:
        e.expand(frontier)
        assert e.stats.round_trips == expected_round_trips([1200], 500) == 18


def test_expand_filters_non_symbol_destinations(expander, sidecar):
    """A1.1: membership filtering happens application-side, via the sidecar."""
    pool = eligible_seeds(SYMBOLS)[:300]
    edges = expander.expand(pool)
    assert all(dst in sidecar for _s, _e, dst in edges)


def test_inherits_from_is_never_traversed():
    assert "INHERITS_FROM" not in HARD_EDGES
    assert "INHERITS_FROM" not in QUERIES


# -- fixpoint behaviour on real data ------------------------------------


def test_closure_respects_the_two_hop_bound(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    result = closure({s: L3 for s in seeds}, expander)
    assert result.hops_run <= 2


def test_closure_contains_its_seeds_at_L3(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    result = closure({s: L3 for s in seeds}, expander)
    for s in seeds:
        assert result.levels[s] == L3


def test_every_closure_member_is_a_known_symbol(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    result = closure({s: L3 for s in seeds}, expander)
    assert all(n in sidecar for n in result.levels)


def test_no_closure_member_sits_below_L1(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    result = closure({s: L3 for s in seeds}, expander)
    assert all(lv >= L1 for lv in result.levels.values())


def test_provenance_present_on_every_non_seed_entry(expander, sidecar):
    """Acceptance gate item 6, on real data rather than a fixture."""
    pool = eligible_seeds(SYMBOLS)
    for seeds in sample_seed_sets(pool, 10, 6):
        result = closure({s: L3 for s in seeds}, expander)
        assert provenance_is_complete(result), seeds
        for node in result.non_seeds():
            for reason in result.explain(node):
                assert reason.via in result.levels
                assert reason.edge in HARD_EDGES
                assert "->" in reason.rule


def test_closure_is_deterministic(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    a = closure({s: L3 for s in seeds}, expander)
    b = closure({s: L3 for s in seeds}, expander)
    assert a.levels == b.levels


def test_source_cost_only_charges_L2_and_L3(expander, sidecar):
    seeds = eligible_seeds(SYMBOLS)[:6]
    result = closure({s: L3 for s in seeds}, expander)
    expected = sum(
        sidecar[n].repr_L3_tokens if lv == L3 else sidecar[n].repr_L2_tokens
        for n, lv in result.levels.items()
        if lv >= L2
    )
    assert source_cost(result, sidecar) == expected


# -- the cross-validation itself ----------------------------------------


def test_distribution_matches_the_prediction(distribution):
    """The headline check: an independent prediction, made before the code."""
    ratios = compare(distribution)
    assert within_an_order_of_magnitude(distribution), (
        f"observed {distribution} vs predicted {PREDICTION}, ratios {ratios}"
    )


def test_median_closure_size_lands_near_the_prediction(distribution):
    assert 0.25 <= compare(distribution)["closure_size_median"] <= 4.0


def test_median_token_cost_lands_near_the_prediction(distribution):
    assert 0.25 <= compare(distribution)["tokens_median"] <= 4.0


def test_provenance_complete_across_the_whole_distribution(distribution):
    assert distribution["provenance_complete"]


def test_round_trips_per_trial_never_exceed_the_chunked_bound(distribution):
    """No trial may cost more than the Sec 5.1 formula allows for two hops."""
    assert distribution["round_trips"]["max"] <= 12 * 40
