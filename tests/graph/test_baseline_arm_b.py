"""Arm B comparison-arm tests; all use an in-memory graph oracle."""
from __future__ import annotations

from context_compiler.baseline.arm_b import dangling_references, run_arm_b
from context_compiler.emit import MappingTextSource, SymbolRecord, emit
from context_compiler.graph.budget import is_closed
from context_compiler.graph.closure import L2, L3
from context_compiler.graph.expand import HARD_EDGES, CachingExpander
from context_compiler.graph.sidecar import SymbolMeta


class StubExpander:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

        class Stats:
            round_trips = 0

        self.stats = Stats()

    def __call__(self, frontier):
        self.calls.append(list(frontier))
        self.stats.round_trips += len(HARD_EDGES)
        return [row for row in self.rows if row[0] in frontier]


class StubReverse:
    def __init__(self, rows):
        self.rows = list(rows)

    def read(self, edge_type, node):
        return [src for src, et, dst in self.rows if et == edge_type and dst == node]


def meta(node, *, t2=10, t3=100, r2=(), r3=(), fqn=None):
    return SymbolMeta(
        fqn=fqn or f"pkg.s{node}",
        kind="function",
        file="pkg/mod.py",
        repr_L2_tokens=t2,
        repr_L3_tokens=t3,
        repr_L2_refs=tuple(r2),
        repr_L3_refs=tuple(r3),
        identity_tokens=4,
        provenance_tokens=2,
        evaluable=None,
    )


def records(sidecar):
    return {
        node: SymbolRecord(
            id=node,
            fqn=meta.fqn,
            kind=meta.kind,
            file=meta.file,
            start_line=node,
            repr_L2_text=f"def s{node}(): ...",
            repr_L3_text=f"def s{node}():\n    return {node}\n",
        )
        for node, meta in sidecar.items()
    }


def test_arm_b_reads_one_hop_and_keeps_seed_bodies():
    sidecar = {
        1: meta(1, r3=(2, 3)),
        2: meta(2, r2=(4,)),
        3: meta(3),
        4: meta(4),
        5: meta(5),
    }
    expander = StubExpander(
        [(1, "CALLS", 2), (1, "REFERENCES_TYPE", 3), (2, "CALLS", 4), (1, "INHERITS_FROM", 5)]
    )
    ctx = run_arm_b([1], sidecar, expander, degrees={1: 2, 2: 1, 3: 0, 4: 0, 5: 0})

    assert expander.calls == [[1]]
    assert ctx.levels[1] == L3
    assert ctx.levels[2] == L2 and ctx.levels[3] == L2
    assert 4 not in ctx.levels and 5 not in ctx.levels
    assert ctx.provenance[2][0].edge == "CALLS"
    assert ctx.stats.round_trips == len(HARD_EDGES)


def test_arm_b_greedy_fill_continues_after_overflow():
    sidecar = {
        1: meta(1, t3=100),
        2: meta(2, t2=1_000),
        3: meta(3, t2=10),
    }
    expander = StubExpander([(1, "CALLS", 2), (1, "CALLS", 3)])
    ctx = run_arm_b([1], sidecar, expander, degrees={2: 0, 3: 0}, budget=1_200)

    assert ctx.levels[3] == L2
    assert ctx.levels.get(2) != L2
    assert ctx.stats.skipped_too_large == 1


def test_arm_b_reports_identity_only_references_and_is_not_closed():
    sidecar = {
        1: meta(1, t3=20, r3=(2,)),
        2: meta(2, t2=10, r2=(3,)),
        3: meta(3),
    }
    expander = StubExpander([(1, "CALLS", 2), (2, "CALLS", 3)])
    ctx = run_arm_b([1], sidecar, expander, degrees={1: 1, 2: 1, 3: 0})

    assert dangling_references(ctx, sidecar) == {3}
    assert not is_closed(ctx.levels, CachingExpander(StubExpander(expander.rows)))


def test_arm_b_context_is_accepted_by_emitter():
    sidecar = {1: meta(1, t3=20), 2: meta(2, t2=10)}
    expander = StubExpander([(1, "CALLS", 2)])
    ctx = run_arm_b([1], sidecar, expander, degrees={1: 1, 2: 0})
    out = emit(ctx, MappingTextSource(records(sidecar)), sidecar)

    assert out.order == [1, 2]
    assert "GRAPH TOP-K, NO CLOSURE" in out.text


def test_arm_b_can_include_reverse_one_hop_neighbours():
    sidecar = {node: meta(node) for node in (1, 2, 3)}
    expander = StubExpander([])
    reverse = StubReverse([(2, "CALLS", 1)])
    ctx = run_arm_b([1], sidecar, expander, degrees={2: 0}, reverse=reverse)

    assert ctx.levels[2] == L2
