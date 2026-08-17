"""Item 5 semantics on hand-built graphs. No HydraDB, no I/O.

Profiles, the cost model, bundle arithmetic and the admission scan are pinned
here against exact expected numbers, so they hold independently of the engine,
the ingest layer and the real sidecar. The Django suite then checks the same
invariants at scale.
"""
from __future__ import annotations

import math
import random

import pytest

from context_compiler.graph.budget import (
    HEADER_TOKENS,
    HINT_RESERVE,
    CostState,
    cost,
    identity_hints,
    is_closed,
    mandatory_identities,
    refs_at,
    unclosed_edges,
)
from context_compiler.graph.closure import (
    PROPAGATION,
    L0,
    L1,
    L2,
    L3,
    Level,
    closure,
    induced_delta,
)
from context_compiler.graph.compile import EXCEEDED, OK, Compiler
from context_compiler.graph.expand import (
    HARD_EDGES,
    CachingExpander,
    EnvelopeMiss,
    FrozenExpander,
)
from context_compiler.graph.pack import (
    Candidate,
    CandidateSource,
    DiscoveryContext,
    StaticCallerSource,
    build_candidates,
    idf,
    pack,
    relevance,
)
from context_compiler.graph.profiles import P0, P1, P2, P3, PROFILES
from context_compiler.graph.sidecar import SymbolMeta


# -- fixture graph builders ---------------------------------------------


def meta(
    node: int,
    t2: int = 10,
    t3: int = 100,
    r2: tuple[int, ...] = (),
    r3: tuple[int, ...] = (),
    ident: int = 8,
    prov: int = 5,
    kind: str = "function",
) -> SymbolMeta:
    return SymbolMeta(
        fqn=f"pkg.sym{node}",
        kind=kind,
        repr_L2_tokens=t2,
        repr_L3_tokens=t3,
        repr_L2_refs=r2,
        repr_L3_refs=r3,
        identity_tokens=ident,
        provenance_tokens=prov,
        evaluable=None,
    )


class StubExpander:
    """An `Expander`-shaped edge server with a round-trip counter.

    Counts one round trip per *hop* per edge type, mirroring the Sec 5.1 cost
    model, so the fixture suite can assert on the same figure the Django suite
    reports.
    """

    def __init__(self, edges):
        self.by_src: dict[int, list[tuple[str, int]]] = {}
        for src, et, dst in edges:
            self.by_src.setdefault(src, []).append((et, dst))
        self.calls: list[list[int]] = []

        class _S:
            round_trips = 0

        self.stats = _S()

    def __call__(self, frontier):
        self.calls.append(list(frontier))
        self.stats.round_trips += len(HARD_EDGES)
        out = []
        for n in frontier:
            out.extend((n, et, dst) for et, dst in self.by_src.get(n, ()))
        return out


class StubReverse:
    """Single-source reverse reads over a fixture edge list."""

    def __init__(self, edges):
        self.by_dst: dict[tuple[str, int], list[int]] = {}
        for src, et, dst in edges:
            self.by_dst.setdefault((et, dst), []).append(src)
        self.round_trips = 0

    def read(self, edge_type: str, node: int) -> list[int]:
        self.round_trips += 1
        return list(self.by_dst.get((edge_type, node), ()))


def build(edges, sidecar, degrees=None, sources=(StaticCallerSource(),)):
    expander = StubExpander(edges)
    reverse = StubReverse(edges)
    degrees = degrees if degrees is not None else _out_degrees(edges)
    return Compiler(
        sidecar=sidecar,
        expander=expander,
        reverse=reverse,
        degrees=degrees,
        sources=sources,
    )


def _out_degrees(edges) -> dict[int, int]:
    d: dict[int, int] = {}
    for src, _et, _dst in edges:
        d[src] = d.get(src, 0) + 1
    return d


# =======================================================================
# 1. Profile monotonicity
# =======================================================================


def test_profile_family_is_ordered_by_rank():
    assert [p.name for p in PROFILES] == ["P3", "P2", "P1", "P0"]
    assert [p.rank for p in PROFILES] == [3, 2, 1, 0]


def test_adjust_never_raises_a_required_level():
    """A profile may only lower what the Sec 4 table asked for."""
    for profile in PROFILES:
        for edge_type in HARD_EDGES:
            for required in (L0, L1, L2, L3):
                assert profile.adjust(edge_type, required) <= required


def test_profile_monotonicity_pointwise_on_a_rich_graph():
    """Sec 6.1: each profile's level map is pointwise <= the previous one.

    This is what makes Sec 6.2's linear scan valid. Checked on a graph that
    exercises every edge type at both hops.
    """
    edges = []
    for i, et in enumerate(HARD_EDGES):
        edges.append((1, et, 10 + i))  # hop 1, one target per edge type
        edges.append((10 + i, et, 20 + i))  # hop 2
    maps = {}
    for profile in PROFILES:
        expander = StubExpander(edges)
        maps[profile.name] = closure({1: profile.seed_level}, expander, profile).levels

    for richer, poorer in zip(PROFILES, PROFILES[1:]):
        a, b = maps[richer.name], maps[poorer.name]
        for node, level in b.items():
            assert level <= a.get(node, L0), (
                f"{poorer.name}[{node}]={level!s} exceeds {richer.name}"
                f"[{node}]={a.get(node, L0)!s}"
            )


def test_profile_level_maps_are_exactly_as_tabled():
    """Sec 6.1's table, read off a two-hop chain with a non-direct edge."""
    edges = [
        (1, "CALLS", 2),  # direct callee: P2 keeps at L2
        (1, "DECORATED_BY", 3),  # not direct: P2 drops to L1
        (2, "CALLS", 4),  # second hop
    ]
    got = {
        p.name: closure({1: p.seed_level}, StubExpander(edges), p).levels
        for p in PROFILES
    }
    assert got["P3"] == {1: L3, 2: L2, 3: L2, 4: L1}
    assert got["P2"] == {1: L3, 2: L2, 3: L1, 4: L0} or got["P2"] == {1: L3, 2: L2, 3: L1}
    assert got["P1"] == {1: L3, 2: L1, 3: L1}
    assert got["P0"] == {1: L2, 2: L1, 3: L1}


def test_cost_is_monotone_along_the_family():
    """Monotone levels give monotone cost, which is the scan's premise."""
    edges = [(1, "CALLS", 2), (1, "DECORATED_BY", 3), (2, "CALLS", 4)]
    sidecar = {n: meta(n) for n in (1, 2, 3, 4)}
    costs = [
        cost(closure({1: p.seed_level}, StubExpander(edges), p).levels, sidecar)
        for p in PROFILES
    ]
    assert costs == sorted(costs, reverse=True), costs


def test_p0_seeds_enter_at_L2():
    assert P0.seed_level == L2
    assert P3.seed_level == P2.seed_level == P1.seed_level == L3


def test_p2_keeps_only_direct_callees_and_types_at_L2():
    for et in HARD_EDGES:
        expected = L2 if et in ("CALLS", "REFERENCES_TYPE") else L1
        assert P2.adjust(et, PROPAGATION[et][L3]) == expected


# =======================================================================
# 2. cost()
# =======================================================================


def test_cost_charges_source_provenance_identities_and_header():
    """Sec 6.2's four terms, each visible in the arithmetic."""
    sidecar = {
        1: meta(1, t3=100, r3=(2, 9), prov=5),
        2: meta(2, t2=10, r2=(9,), prov=4),
        3: meta(3),  # L1: costs nothing
        9: meta(9, ident=7),  # never emitted: L1-mandatory
    }
    levels = {1: L3, 2: L2, 3: L1}
    #  src   100 (node 1 at L3) + 10 (node 2 at L2)
    #  prov    5 + 4
    #  ident   7 (node 9, referenced by both 1 and 2, charged once)
    #  header 40
    assert cost(levels, sidecar) == 100 + 10 + 5 + 4 + 7 + HEADER_TOKENS


def test_L1_lattice_members_cost_zero():
    """Sec 1.1's first tier. Adding an unreferenced L1 node changes nothing."""
    sidecar = {1: meta(1, t3=100), 5: meta(5, t2=999, t3=999, ident=50)}
    assert cost({1: L3}, sidecar) == cost({1: L3, 5: L1}, sidecar)


def test_an_emitted_node_is_not_charged_as_its_own_identity():
    sidecar = {1: meta(1, t3=100, r3=(2,)), 2: meta(2, t2=10, ident=8)}
    with_ident = cost({1: L3, 2: L1}, sidecar)
    emitted = cost({1: L3, 2: L2}, sidecar)
    assert with_ident == 100 + 5 + 8 + HEADER_TOKENS
    assert emitted == 100 + 10 + 5 + 5 + HEADER_TOKENS  # ident gone, decl + prov in


def test_a_reference_outside_the_closure_is_still_charged():
    """L1-mandatory is not a subset of the lattice (Sec 1.1)."""
    sidecar = {1: meta(1, t3=100, r3=(77,)), 77: meta(77, ident=12)}
    assert 77 not in {1: L3}
    assert cost({1: L3}, sidecar) == 100 + 5 + 12 + HEADER_TOKENS


def test_mandatory_identities_excludes_emitted_nodes():
    sidecar = {1: meta(1, t3=1, r3=(2, 3)), 2: meta(2), 3: meta(3)}
    assert mandatory_identities({1: L3, 2: L2}, sidecar) == {3}


def test_L2_and_L3_refs_are_read_from_the_right_field():
    m = meta(1, r2=(7,), r3=(8, 9))
    assert refs_at(m, L3) == (8, 9)
    assert refs_at(m, L2) == (7,)
    assert refs_at(m, L1) == ()
    assert refs_at(m, L0) == ()


# =======================================================================
# 3. CostState -- the incremental model
# =======================================================================


def _random_case(rng: random.Random, n: int = 30):
    sidecar = {
        i: meta(
            i,
            t2=rng.randint(1, 40),
            t3=rng.randint(40, 300),
            r2=tuple(rng.sample(range(n), rng.randint(0, 3))),
            r3=tuple(rng.sample(range(n), rng.randint(0, 6))),
            ident=rng.randint(4, 15),
            prov=rng.randint(3, 12),
        )
        for i in range(n)
    }
    levels = {
        i: rng.choice([L1, L1, L2, L3]) for i in rng.sample(range(n), rng.randint(1, n))
    }
    return sidecar, levels


def test_cost_state_agrees_with_cost_on_construction():
    rng = random.Random(20260817)
    for _ in range(200):
        sidecar, levels = _random_case(rng)
        assert CostState(levels, sidecar).total() == cost(levels, sidecar)


def test_cost_state_delta_agrees_with_recomputation():
    """The load-bearing equivalence: incremental cost == from-scratch cost.

    Sec 6.3's packer only ever sees `delta_cost`, so if this drifts, every
    budget decision is wrong and I4 fails silently.
    """
    rng = random.Random(4242)
    for _ in range(300):
        sidecar, levels = _random_case(rng)
        state = CostState(levels, sidecar)
        delta = {
            i: rng.choice([L1, L2, L3])
            for i in rng.sample(sorted(sidecar), rng.randint(1, 8))
        }
        merged = dict(levels)
        for node, lv in delta.items():
            if lv > merged.get(node, L0):
                merged[node] = lv
        expected = cost(merged, sidecar) - cost(levels, sidecar)
        assert state.delta_cost(delta) == expected
        assert state.apply(delta) == expected
        assert state.total() == cost(merged, sidecar)


def test_cost_state_ignores_a_level_that_would_fall():
    """Levels only ever rise (Sec 5). A lowering delta is a no-op."""
    sidecar = {1: meta(1, t3=100)}
    state = CostState({1: L3}, sidecar)
    before = state.total()
    assert state.delta_cost({1: L1}) == 0
    state.apply({1: L1})
    assert state.total() == before
    assert state.levels[1] == L3


def test_cost_state_tracks_the_dangling_set():
    sidecar = {1: meta(1, t3=5, r3=(2, 3)), 2: meta(2), 3: meta(3)}
    state = CostState({1: L3}, sidecar)
    assert state.dangling() == {2, 3}
    state.apply({2: L2})
    assert state.dangling() == {3}


# =======================================================================
# 4. Bundles and I6
# =======================================================================


def test_a_bundle_admitted_at_L3_pulls_its_L2_dependencies():
    """I6: the candidate never enters alone."""
    edges = [(9, "CALLS", 8), (8, "CALLS", 7)]
    expander = StubExpander(edges)
    delta, prov = induced_delta({1: L3}, {9: L3}, expander, P3)
    assert delta == {9: L3, 8: L2, 7: L1}
    assert prov[8][0].via == 9
    assert prov[7][0].via == 8


def test_is_closed_holds_after_a_bundle_and_fails_without_it():
    edges = [(9, "CALLS", 8), (8, "CALLS", 7)]
    cache = CachingExpander(StubExpander(edges))
    levels = dict(closure({1: L3}, cache, P3).levels)  # a genuinely closed base
    delta, _ = induced_delta(levels, {9: L3}, cache, P3)
    levels.update(delta)
    assert is_closed(levels, cache.frozen(), P3)

    bare = {1: L3, 9: L3}  # the v1.1 bug: candidate without its closure
    assert not is_closed(bare, cache.frozen(), P3)
    violations = unclosed_edges(bare, cache.frozen(), P3)
    assert (9, "CALLS", 8, L2, L0) in violations


def test_is_closed_refuses_to_certify_an_unexpanded_node():
    """An empty answer must not be mistaken for a leaf."""
    cache = CachingExpander(StubExpander([(1, "CALLS", 2)]))
    assert not is_closed({1: L3, 2: L2}, cache.frozen(), P3)


def test_frozen_expander_raises_rather_than_hitting_the_database():
    cache = CachingExpander(StubExpander([(1, "CALLS", 2)]))
    cache([1])
    frozen = cache.frozen()
    assert frozen([1]) == [(1, "CALLS", 2)]
    with pytest.raises(EnvelopeMiss):
        frozen([99])


def test_induced_delta_equals_a_from_scratch_closure():
    """Incremental bundle arithmetic is exact, not an approximation."""
    edges = [
        (1, "CALLS", 2),
        (2, "CALLS", 3),
        (9, "CALLS", 2),
        (9, "REFERENCES_TYPE", 8),
        (8, "CALLS", 3),
    ]
    for profile in PROFILES:
        base = closure({1: profile.seed_level}, StubExpander(edges), profile)
        delta, _ = induced_delta(base.levels, {9: L3}, StubExpander(edges), profile)
        merged = dict(base.levels)
        merged.update(delta)
        scratch = closure(
            {1: profile.seed_level, 9: L3}, StubExpander(edges), profile
        ).levels
        assert merged == scratch, profile.name


def test_shared_dependencies_make_the_second_bundle_cheaper():
    """Sec 6.3: admitting one candidate can make the next cheaper.

    Both candidates name the same helper in their declarations, so both drag in
    its L1-mandatory identity line. The first pays for it; the second gets it
    free, because it is already charged.
    """
    edges = [(20, "CALLS", 50), (21, "CALLS", 50)]
    sidecar = {
        1: meta(1, t3=10),
        20: meta(20, t2=10, r2=(50,)),
        21: meta(21, t2=10, r2=(50,)),
        50: meta(50, t2=500, ident=6),
    }
    cache = CachingExpander(StubExpander(edges))
    cache([20, 21])
    levels = {1: L3}
    state = CostState(levels, sidecar)

    first, _ = induced_delta(levels, {20: L2}, cache.frozen(), P3)
    cost_first = state.delta_cost(first)
    state.apply(first)
    levels.update(first)

    second, _ = induced_delta(levels, {21: L2}, cache.frozen(), P3)
    cost_second = state.delta_cost(second)

    assert first == {20: L2, 50: L1}
    assert second == {21: L2}
    assert cost_second < cost_first
    # The first pays for node 50's identity line; the second does not.
    assert cost_first == 10 + 5 + 6
    assert cost_second == 10 + 5

    # And the saving is exactly the shared dependency.
    assert cost_first - cost_second == sidecar[50].identity_tokens


# =======================================================================
# 5. Scoring
# =======================================================================


def test_idf_suppresses_a_synthetic_500_degree_hub():
    degrees = {1: 500, 2: 2}
    n = 43_420
    assert idf(1, degrees, n) < idf(2, degrees, n)
    assert idf(1, degrees, n) == pytest.approx(math.log(n / 501))


def test_idf_is_the_only_thing_separating_two_equal_candidates():
    """Sec 6.3: `idf` is what stops Model, QuerySet and Field dominating."""
    source = StaticCallerSource()
    hub, leaf = 1, 2
    degrees = {hub: 500, leaf: 1}
    scores = {
        n: relevance(source, (99,)) * idf(n, degrees, 43_420) * source.confidence
        for n in (hub, leaf)
    }
    assert scores[leaf] > scores[hub]
    assert scores[leaf] / scores[hub] > 1.5


def test_relevance_rewards_touching_several_seeds():
    source = StaticCallerSource()
    assert relevance(source, (1,)) == source.base_weight
    assert relevance(source, (1, 2)) > relevance(source, (1,))
    assert relevance(source, (1, 2, 3)) > relevance(source, (1, 2))


def test_unavailable_sources_contribute_nothing():
    """Item 9's two sources are declared but dormant."""
    from context_compiler.graph.pack import CANDIDATE_SOURCES

    dormant = [s.name for s in CANDIDATE_SOURCES if not s.available]
    assert dormant == ["covering_test", "observed_caller"]
    ctx = DiscoveryContext(reverse=StubReverse([]), edges={}, sidecar={})
    got = build_candidates([1], CANDIDATE_SOURCES, ctx, {}, 100)
    assert got == []


def test_candidates_already_emitted_are_not_proposed():
    edges = [(20, "CALLS", 1)]
    sidecar = {1: meta(1), 20: meta(20)}
    ctx = DiscoveryContext(
        reverse=StubReverse(edges), edges={}, sidecar=sidecar
    )
    assert build_candidates([1], (StaticCallerSource(),), ctx, {}, 100) != []
    assert (
        build_candidates([1], (StaticCallerSource(),), ctx, {}, 100, exclude={20})
        == []
    )


# =======================================================================
# 6. The packing loop
# =======================================================================


def test_pack_admits_the_best_value_first():
    """value = score / delta_cost, not score / own tokens (the v1.1 bug)."""
    edges = [(20, "CALLS", 50), (21, "CALLS", 51)]
    sidecar = {
        1: meta(1, t3=10),
        # Identical own cost (10 decl + 5 prov); candidate 20's declaration
        # names an expensive symbol, so its *bundle* costs far more.
        20: meta(20, t2=10, r2=(50,)),
        21: meta(21, t2=10, r2=(51,)),
        50: meta(50, ident=200),
        51: meta(51, ident=1),
    }
    cache = CachingExpander(StubExpander(edges))
    cache([20, 21])
    levels = {1: L3}
    state = CostState(levels, sidecar)
    source = StaticCallerSource()
    cands = [
        Candidate(20, source, (1,), score=1.0),
        Candidate(21, source, (1,), score=1.0),
    ]
    report = pack(1_000, levels, {}, cands, state, cache.frozen(), P3)
    assert [a.node for a in report.admitted] == [21, 20]
    assert report.admitted[0].delta_cost == 10 + 5 + 1
    assert report.admitted[1].delta_cost == 10 + 5 + 200


def test_pack_never_overruns_the_budget_by_one_token():
    """Budget exactly consumed. The bundle that would not fit stays out."""
    edges = [(20, "CALLS", 50), (21, "CALLS", 51)]
    sidecar = {
        1: meta(1, t3=10),
        20: meta(20, t2=10, prov=5),
        21: meta(21, t2=10, prov=5),
        50: meta(50, ident=0),
        51: meta(51, ident=0),
    }
    cache = CachingExpander(StubExpander(edges))
    cache([20, 21])
    levels = {1: L3}
    state = CostState(levels, sidecar)
    before = state.total()
    source = StaticCallerSource()
    cands = [Candidate(20, source, (1,), 1.0), Candidate(21, source, (1,), 0.9)]

    # Exactly one bundle (10 decl + 5 prov = 15) fits in 15 tokens.
    report = pack(15, levels, {}, cands, state, cache.frozen(), P3)
    assert report.admitted_count == 1
    assert report.spent == 15
    assert state.total() == before + 15
    assert report.remaining == 0

    # One token short: nothing is admitted at all.
    levels2, state2 = {1: L3}, CostState({1: L3}, sidecar)
    report2 = pack(14, levels2, {}, list(cands), state2, cache.frozen(), P3)
    assert report2.admitted_count == 0
    assert state2.total() == before


def test_pack_leaves_the_context_closed():
    edges = [(20, "CALLS", 50), (50, "CALLS", 60)]
    sidecar = {n: meta(n, t2=5, t3=5, ident=1, prov=1) for n in (1, 20, 50, 60)}
    cache = CachingExpander(StubExpander(edges))
    levels = dict(closure({1: L3}, cache, P3).levels)
    cache([20])  # the envelope
    state = CostState(levels, sidecar)
    pack(
        1_000,
        levels,
        {},
        [Candidate(20, StaticCallerSource(), (1,), 1.0)],
        state,
        cache.frozen(),
        P3,
    )
    assert levels == {1: L3, 20: L2, 50: L1}
    assert is_closed(levels, cache.frozen(), P3)


def test_pack_records_provenance_for_every_admitted_bundle():
    edges = [(20, "CALLS", 50)]
    sidecar = {n: meta(n, t2=5, t3=5, ident=1, prov=1) for n in (1, 20, 50)}
    cache = CachingExpander(StubExpander(edges))
    cache([20])
    levels, prov = {1: L3}, {}
    pack(
        1_000,
        levels,
        prov,
        [Candidate(20, StaticCallerSource(), (1,), 1.0)],
        CostState(levels, sidecar),
        cache.frozen(),
        P3,
    )
    assert prov[20][-1].edge == "OPTIONAL:static_caller"
    assert prov[20][-1].via == 1
    assert prov[50][0].via == 20  # pulled in by rule, not by score


def test_pack_touches_the_database_zero_times():
    """The envelope is the whole point: packing does no I/O."""
    edges = [(20, "CALLS", 50)]
    sidecar = {n: meta(n) for n in (1, 20, 50)}
    inner = StubExpander(edges)
    cache = CachingExpander(inner)
    cache([20])
    trips_after_envelope = inner.stats.round_trips
    levels = {1: L3}
    pack(
        10_000,
        levels,
        {},
        [Candidate(20, StaticCallerSource(), (1,), 1.0)],
        CostState(levels, sidecar),
        cache.frozen(),
        P3,
    )
    assert inner.stats.round_trips == trips_after_envelope


# =======================================================================
# 7. compile_context
# =======================================================================


def test_p3_fits_and_returns_status_ok():
    edges = [(1, "CALLS", 2), (2, "CALLS", 3)]
    sidecar = {1: meta(1, t3=100), 2: meta(2, t2=20), 3: meta(3)}
    ctx = build(edges, sidecar).compile_context([1], 8_000)
    assert ctx.status == OK
    assert ctx.profile is P3
    assert ctx.levels[1] == L3 and ctx.levels[2] == L2 and ctx.levels[3] == L1


def test_demotion_fires_when_p3_does_not_fit():
    """P3's L2 declarations blow the budget; P2 drops the non-direct edge."""
    edges = [(1, "CALLS", 2), (1, "DECORATED_BY", 3)]
    sidecar = {
        1: meta(1, t3=100, prov=0),
        2: meta(2, t2=50, prov=0, ident=1),
        3: meta(3, t2=5_000, prov=0, ident=1),
    }
    ctx = build(edges, sidecar).compile_context([1], 300)
    assert ctx.status == "DEMOTED:P2"
    assert ctx.profile is P2
    assert ctx.levels[3] == L1
    assert ctx.total_tokens() <= 300


def test_closure_budget_exceeded_returns_with_a_correct_deficit():
    """A first-class return value, never an exception (Sec 6.2)."""
    edges = [(1, "CALLS", 2)]
    sidecar = {1: meta(1, t2=9_000, t3=20_000, prov=0, ident=1), 2: meta(2, ident=1)}
    budget = 1_000
    compiler = build(edges, sidecar)
    ctx = compiler.compile_context([1], budget)

    assert ctx.status == EXCEEDED
    assert not ctx.ok
    assert ctx.suggestion == "narrow the task or raise the budget"

    effective = budget - int(budget * HINT_RESERVE)
    floor = cost(
        closure({1: P0.seed_level}, StubExpander(edges), P0).levels, sidecar
    )
    assert ctx.deficit == floor - effective
    assert ctx.deficit > 0


def test_both_invariants_hold_on_the_returned_context():
    edges = [(1, "CALLS", 2), (2, "CALLS", 3), (20, "CALLS", 1), (20, "CALLS", 50)]
    sidecar = {n: meta(n, t2=10, t3=60) for n in (1, 2, 3, 20, 50)}
    ctx = build(edges, sidecar).compile_context([1], 8_000)
    assert ctx.status == OK
    assert ctx.cost + ctx.hint_tokens <= ctx.budget  # I4
    assert 20 in ctx.levels  # the caller was packed
    assert ctx.levels[50] == L1  # ... with its bundle (I6)


def test_hints_never_repeat_a_charged_identity():
    """L1-mandatory is budgeted; L1-hints is the reserve. Never both."""
    sidecar = {1: meta(1, t3=10, r3=(2,)), 2: meta(2, ident=8), 3: meta(3, ident=8)}
    levels = {1: L3, 2: L1, 3: L1}
    hints = identity_hints(levels, sidecar, cap=1_000)
    assert 2 not in hints.nodes  # already charged as L1-mandatory
    assert 3 in hints.nodes


def test_hints_truncate_and_say_so():
    sidecar = {1: meta(1, t3=10)} | {n: meta(n, ident=10) for n in range(2, 12)}
    levels = {1: L3} | {n: L1 for n in range(2, 12)}
    hints = identity_hints(levels, sidecar, cap=35)
    assert hints.tokens <= 35
    assert len(hints) == 3
    assert hints.truncated
    assert hints.considered == 10


def test_hint_reserve_is_five_percent():
    assert HINT_RESERVE == 0.05
    assert int(8_000 * HINT_RESERVE) == 400


def test_round_trips_stay_within_the_gate():
    """12 closure + one reverse read per seed + 6 envelope."""
    edges = [(1, "CALLS", 2), (2, "CALLS", 3), (20, "CALLS", 1)]
    sidecar = {n: meta(n) for n in (1, 2, 3, 20)}
    compiler = build(edges, sidecar)
    ctx = compiler.compile_context([1], 8_000)
    assert ctx.stats.closure_round_trips == 12
    assert ctx.stats.discovery_round_trips == 1  # one seed
    assert ctx.stats.envelope_round_trips == 6
    assert ctx.stats.round_trips <= 24


def test_demotion_costs_no_extra_round_trips():
    """Every profile below P3 reads from the expansion cache."""
    edges = [(1, "CALLS", 2), (1, "DECORATED_BY", 3)]
    sidecar = {
        1: meta(1, t3=100, prov=0),
        2: meta(2, t2=50, prov=0, ident=1),
        3: meta(3, t2=5_000, prov=0, ident=1),
    }
    compiler = build(edges, sidecar)
    ctx = compiler.compile_context([1], 300)
    assert ctx.demoted
    assert ctx.stats.closure_round_trips == 12


def test_packing_grows_the_context_beyond_the_mandatory_floor():
    """The headline number: symbols in a compiled context vs the floor alone."""
    edges = [(1, "CALLS", 2)] + [(20 + i, "CALLS", 1) for i in range(5)]
    sidecar = {n: meta(n, t2=10, t3=40) for n in [1, 2] + [20 + i for i in range(5)]}
    ctx = build(edges, sidecar).compile_context([1], 8_000)
    assert ctx.stats.floor_symbols == 2
    assert len(ctx.levels) == 7
    assert ctx.stats.admitted == 5


def test_compile_is_deterministic():
    edges = [(1, "CALLS", 2), (20, "CALLS", 1), (21, "CALLS", 1)]
    sidecar = {n: meta(n) for n in (1, 2, 20, 21)}
    a = build(edges, sidecar).compile_context([1], 8_000)
    b = build(edges, sidecar).compile_context([1], 8_000)
    assert a.levels == b.levels
    assert a.cost == b.cost
