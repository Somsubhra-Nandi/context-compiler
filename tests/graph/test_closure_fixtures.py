"""Fixpoint semantics on hand-built graphs. No HydraDB, no I/O.

Each fixture states an exact expected level map. `expand()` is stubbed, so this
suite pins the propagation semantics independently of the engine, the ingest
layer and the sidecar.
"""
from __future__ import annotations

import pytest

from context_compiler.graph.closure import (
    MAX_HOPS,
    PROPAGATION,
    L0,
    L1,
    L2,
    L3,
    Level,
    closure,
)
from context_compiler.graph.expand import HARD_EDGES


def stub_expand(edges: list[tuple[int, str, int]]):
    """Return an `expand()` that serves a fixed edge list, plus a call log."""
    by_src: dict[int, list[tuple[int, str, int]]] = {}
    for src, et, dst in edges:
        by_src.setdefault(src, []).append((src, et, dst))

    calls: list[list[int]] = []

    def _expand(frontier: list[int]) -> list[tuple[int, str, int]]:
        calls.append(list(frontier))
        out: list[tuple[int, str, int]] = []
        for n in frontier:
            out.extend(by_src.get(n, ()))
        return out

    _expand.calls = calls  # type: ignore[attr-defined]
    return _expand


# -- the propagation table itself ---------------------------------------


def test_propagation_table_strictly_decreases():
    """Spec Sec 4: every row strictly decreases. No exceptions."""
    for edge_type, rules in PROPAGATION.items():
        for source, target in rules.items():
            if source == L0:
                continue
            assert target < source, f"{edge_type}: {source} -> {target} does not decrease"


def test_propagation_covers_exactly_the_hard_edges():
    assert set(PROPAGATION) == set(HARD_EDGES)


def test_inherits_from_is_not_a_propagation_relation():
    """Sec 3.2: consumed by MRO flattening at ingest, never traversed."""
    assert "INHERITS_FROM" not in PROPAGATION


def test_unknown_edge_type_never_propagates():
    """An evidence relation reaching `closure()` must not raise or propagate."""
    expand = stub_expand([(1, "OBSERVED_CALLS", 2), (1, "COVERS", 3)])
    result = closure({1: L3}, expand)
    assert result.levels == {1: L3}


# -- linear chain -------------------------------------------------------


def test_linear_chain_A_to_D():
    """A->B->C->D with A at L3 gives B=L2, C=L1, and D absent."""
    expand = stub_expand(
        [(1, "CALLS", 2), (2, "CALLS", 3), (3, "CALLS", 4)]
    )
    result = closure({1: L3}, expand)

    assert result.levels == {1: L3, 2: L2, 3: L1}
    assert 4 not in result.levels
    assert result.hops_run == MAX_HOPS


def test_linear_chain_stops_at_two_productive_hops():
    """I1: mandatory depth from an L3 seed is at most two productive hops."""
    expand = stub_expand([(1, "CALLS", 2), (2, "CALLS", 3), (3, "CALLS", 4)])
    closure({1: L3}, expand)
    # hop 1 expands the seed; hop 2 expands only the L2 node. C is L1 and
    # terminal, so it is never handed to expand().
    assert expand.calls == [[1], [2]]


# -- diamond ------------------------------------------------------------


def test_diamond_converging_paths_take_the_maximum():
    """D is reachable as L1 via B and as L2 via a direct L3 edge; L2 wins."""
    expand = stub_expand(
        [
            (1, "CALLS", 2),  # seed -> B  => L2
            (1, "CALLS", 3),  # seed -> C  => L2
            (2, "CALLS", 4),  # B(L2) -> D => L1
            (1, "REFERENCES_TYPE", 4),  # seed -> D => L2   (must win)
        ]
    )
    result = closure({1: L3}, expand)
    assert result.levels == {1: L3, 2: L2, 3: L2, 4: L2}


def test_diamond_maximum_holds_regardless_of_edge_order():
    """The L1 assignment must not stick if the L2 rule is seen second."""
    low_first = [(2, "CALLS", 4), (1, "REFERENCES_TYPE", 4), (1, "CALLS", 2)]
    high_first = [(1, "REFERENCES_TYPE", 4), (2, "CALLS", 4), (1, "CALLS", 2)]
    for edges in (low_first, high_first):
        result = closure({1: L3}, stub_expand(edges))
        assert result.levels[4] == L2, edges


def test_level_never_falls():
    """Monotonicity: no assignment in the run may lower an existing level."""
    edges = [
        (1, "CALLS", 2),
        (1, "REFERENCES_TYPE", 2),
        (2, "CALLS", 3),
        (2, "IMPLEMENTS", 3),
        (1, "DECORATED_BY", 3),
    ]
    seen: dict[int, Level] = {}

    def watching_expand(frontier):
        return stub_expand(edges)(frontier)

    result = closure({1: L3}, watching_expand)
    for node, lv in result.levels.items():
        assert lv >= seen.get(node, L0)
    assert result.levels == {1: L3, 2: L2, 3: L2}


# -- cycles -------------------------------------------------------------


def test_two_cycle_terminates():
    """A->B->A terminates and does not raise; A keeps its seed level."""
    expand = stub_expand([(1, "CALLS", 2), (2, "CALLS", 1)])
    result = closure({1: L3}, expand)
    assert result.levels == {1: L3, 2: L2}


def test_self_loop_terminates():
    expand = stub_expand([(1, "CALLS", 1)])
    result = closure({1: L3}, expand)
    assert result.levels == {1: L3}


def test_three_cycle_terminates():
    expand = stub_expand([(1, "CALLS", 2), (2, "CALLS", 3), (3, "CALLS", 1)])
    result = closure({1: L3}, expand)
    assert result.levels == {1: L3, 2: L2, 3: L1}


def test_dense_cycle_does_not_loop_forever():
    """Fully connected 6-clique: bounded by MAX_HOPS, not by graph shape."""
    edges = [(a, "CALLS", b) for a in range(1, 7) for b in range(1, 7) if a != b]
    result = closure({1: L3}, stub_expand(edges))
    assert result.levels[1] == L3
    assert all(result.levels[n] == L2 for n in range(2, 7))


# -- multi-seed ---------------------------------------------------------


def test_multi_seed_overlapping_neighbourhoods_merge():
    """Two L3 seeds sharing a neighbour: the shared node takes the maximum."""
    expand = stub_expand(
        [
            (1, "CALLS", 10),  # seed 1 -> shared  => L2
            (2, "CALLS", 10),  # seed 2 -> shared  => L2
            (1, "CALLS", 11),
            (2, "CALLS", 12),
            (10, "CALLS", 20),  # shared(L2) -> 20 => L1
        ]
    )
    result = closure({1: L3, 2: L3}, expand)
    assert result.levels == {1: L3, 2: L3, 10: L2, 11: L2, 12: L2, 20: L1}


def test_multi_seed_seed_level_is_never_lowered_by_a_rule():
    """A seed reached by an L2->L1 rule keeps its L3 seed level."""
    expand = stub_expand([(1, "CALLS", 5), (5, "CALLS", 2)])
    result = closure({1: L3, 2: L3}, expand)
    assert result.levels[2] == L3
    assert 2 not in result.provenance


def test_seed_at_L2_produces_only_L1_neighbours():
    expand = stub_expand([(1, "CALLS", 2), (2, "CALLS", 3)])
    result = closure({1: L2}, expand)
    assert result.levels == {1: L2, 2: L1}


# -- L1 terminality -----------------------------------------------------


def test_L1_is_terminal_nothing_propagates_from_it():
    expand = stub_expand([(1, "CALLS", 2), (2, "CALLS", 3), (3, "CALLS", 4)])
    result = closure({1: L3}, expand)
    assert result.levels[3] == L1
    assert 3 not in expand.calls[-1]
    assert 4 not in result.levels


def test_L1_seed_is_never_expanded():
    """Seeds at L1 do not enter the frontier at all (Sec 5)."""
    expand = stub_expand([(1, "CALLS", 2)])
    result = closure({1: L1}, expand)
    assert result.levels == {1: L1}
    assert expand.calls == []


def test_L1_table_row_maps_to_L0():
    for edge_type in HARD_EDGES:
        assert PROPAGATION[edge_type][L1] == L0


# -- provenance ---------------------------------------------------------


def test_provenance_recorded_for_every_non_seed_entry():
    expand = stub_expand(
        [
            (1, "CALLS", 2),
            (1, "REFERENCES_TYPE", 3),
            (2, "DECORATED_BY", 4),
            (2, "READS_CONSTANT", 5),
        ]
    )
    result = closure({1: L3}, expand)

    non_seeds = result.non_seeds()
    assert non_seeds == {2, 3, 4, 5}
    for node in non_seeds:
        reasons = result.explain(node)
        assert reasons, f"no provenance recorded for {node}"
        for r in reasons:
            assert r.via in result.levels
            assert r.edge in HARD_EDGES
            assert r.rule


def test_provenance_absent_for_seeds():
    expand = stub_expand([(1, "CALLS", 2)])
    result = closure({1: L3}, expand)
    assert 1 not in result.provenance


def test_provenance_rule_string_names_the_transition():
    expand = stub_expand([(1, "CALLS", 2), (2, "IMPLEMENTS", 3)])
    result = closure({1: L3}, expand)
    assert result.explain(2)[0].rule == "CALLS(L3)->L2"
    assert result.explain(3)[0].rule == "IMPLEMENTS(L2)->L1"
    assert result.explain(2)[0].via == 1
    assert result.explain(3)[0].via == 2


def test_provenance_accumulates_on_each_rise():
    """A node raised L1 then L2 records both reasons, in order."""
    expand = stub_expand(
        [
            (1, "CALLS", 2),
            (2, "CALLS", 9),  # hop 1 also fires: 2 is not yet L2 when hop 1 runs
            (1, "REFERENCES_TYPE", 3),
            (3, "CALLS", 9),
        ]
    )
    # Hop 1 only expands the seed, so 9 is first reached in hop 2 at L1.
    result = closure({1: L3}, expand)
    assert result.levels[9] == L1
    assert len(result.explain(9)) == 1


def test_provenance_records_both_paths_when_level_rises_twice():
    expand = stub_expand(
        [
            (1, "CALLS", 2),  # hop 1: 2 -> L2
            (2, "CALLS", 7),  # hop 2: 7 -> L1
            (1, "CALLS", 7),  # hop 1: 7 -> L2 already
        ]
    )
    result = closure({1: L3}, expand)
    # 7 is raised to L2 in hop 1 by the seed, so hop 2's L1 rule cannot lower it.
    assert result.levels[7] == L2
    assert len(result.explain(7)) == 1


# -- structural bound ---------------------------------------------------


def test_expand_called_at_most_twice():
    expand = stub_expand([(a, "CALLS", a + 1) for a in range(1, 10)])
    closure({1: L3}, expand)
    assert len(expand.calls) <= MAX_HOPS


def test_empty_seeds_returns_empty():
    expand = stub_expand([(1, "CALLS", 2)])
    result = closure({}, expand)
    assert result.levels == {}
    assert expand.calls == []


def test_profile_parameter_is_accepted_and_ignored():
    """Item 5 owns profiles; Item 4 must not silently apply one."""
    expand = stub_expand([(1, "CALLS", 2)])
    a = closure({1: L3}, expand)
    b = closure({1: L3}, stub_expand([(1, "CALLS", 2)]), profile=object())
    assert a.levels == b.levels


@pytest.mark.parametrize("edge_type", HARD_EDGES)
def test_every_hard_edge_fires_L3_to_L2(edge_type):
    result = closure({1: L3}, stub_expand([(1, edge_type, 2)]))
    assert result.levels[2] == L2
