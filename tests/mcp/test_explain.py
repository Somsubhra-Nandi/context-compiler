"""explain_inclusion's derivation chain, against a hand-built Context. No HydraDB."""
from __future__ import annotations

from context_compiler.graph.closure import L1, L2, L3, Level, Reason
from context_compiler.graph.compile import OK, Context
from context_compiler.graph.sidecar import SymbolMeta
from context_compiler.mcp.explain import explain_inclusion, render_explanation

SEED, DIRECT, TRANSITIVE, PACKED, UNREACHED = 1, 2, 3, 4, 5


def _meta(fqn: str, refs=()) -> SymbolMeta:
    return SymbolMeta(
        fqn=fqn,
        kind="method",
        file="x.py",
        repr_L2_tokens=10,
        repr_L3_tokens=20,
        repr_L2_refs=refs,
        repr_L3_refs=refs,
        identity_tokens=5,
        provenance_tokens=3,
        evaluable=None,
    )


SIDECAR = {
    SEED: _meta("pkg.Seed.run"),
    DIRECT: _meta("pkg.Direct.step", refs=(UNREACHED,)),
    TRANSITIVE: _meta("pkg.Transitive.helper"),
    PACKED: _meta("pkg.Packed.extra"),
    UNREACHED: _meta("pkg.Unreached.orphan"),
}


def _context() -> Context:
    ctx = Context(status=OK, budget=8000)
    ctx.seeds = {SEED: L3}
    ctx.levels = {SEED: L3, DIRECT: L2, TRANSITIVE: L2, PACKED: L2}
    ctx.provenance = {
        DIRECT: [Reason(via=SEED, edge="CALLS", rule="CALLS(L3)->L2")],
        TRANSITIVE: [Reason(via=DIRECT, edge="CALLS", rule="CALLS(L2)->L1")],
        PACKED: [Reason(via=SEED, edge="OPTIONAL:static_caller", rule="packed(static_caller)->L2")],
    }
    return ctx


# -- seed ---------------------------------------------------------------


def test_seed_has_no_chain():
    exp = explain_inclusion(SEED, _context(), SIDECAR)
    assert exp.present
    assert exp.is_seed
    assert exp.chain == []
    assert "seed" in render_explanation(exp).lower()


# -- one mandatory hop ----------------------------------------------------


def test_direct_dependency_chain_of_one():
    exp = explain_inclusion(DIRECT, _context(), SIDECAR)
    assert exp.present
    assert not exp.is_seed
    assert len(exp.chain) == 1
    assert exp.chain[0].fqn == "pkg.Seed.run"
    text = render_explanation(exp)
    assert "<- pkg.Seed.run" in text
    assert "mandatory rule" in text


# -- transitive: walks back through DIRECT's own provenance ------------------


def test_transitive_chain_walks_back_to_the_seed():
    exp = explain_inclusion(TRANSITIVE, _context(), SIDECAR)
    assert len(exp.chain) == 2
    fqns = {step.fqn for step in exp.chain}
    assert fqns == {"pkg.Direct.step", "pkg.Seed.run"}
    # depth increases along the walk back to the seed.
    by_fqn = {step.fqn: step.depth for step in exp.chain}
    assert by_fqn["pkg.Direct.step"] < by_fqn["pkg.Seed.run"]


# -- packed / optional ----------------------------------------------------


def test_packed_node_is_distinguished_from_mandatory():
    exp = explain_inclusion(PACKED, _context(), SIDECAR)
    assert len(exp.chain) == 1
    assert exp.chain[0].edge.startswith("OPTIONAL:")
    text = render_explanation(exp)
    assert "optional packing" in text.lower()
    assert "mandatory rule" not in text  # nothing mandatory fired for this node


# -- absent, but referenced -------------------------------------------------


def test_absent_but_referenced_says_what_would_pull_it_in():
    exp = explain_inclusion(UNREACHED, _context(), SIDECAR)
    assert not exp.present
    assert exp.would_include_via == ["pkg.Direct.step"]
    text = render_explanation(exp)
    assert "not in the compiled context" in text
    assert "pkg.Direct.step" in text


# -- absent and unreferenced ------------------------------------------------


def test_absent_and_unreferenced_says_so():
    ctx = _context()
    del ctx.provenance[DIRECT]  # DIRECT no longer references UNREACHED's owner chain
    ctx.levels.pop(DIRECT)
    # Rebuild sidecar entry without the dangling ref for this specific check.
    sidecar = dict(SIDECAR)
    sidecar[DIRECT] = _meta("pkg.Direct.step")  # no refs now
    exp = explain_inclusion(UNREACHED, ctx, sidecar)
    assert not exp.present
    assert exp.would_include_via == []
    assert "pass it as an explicit seed" in render_explanation(exp).lower()
