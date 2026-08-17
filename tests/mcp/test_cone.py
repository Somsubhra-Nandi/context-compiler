"""impact_cone bounds enforced against a stub reverse reader. No HydraDB."""
from __future__ import annotations

from context_compiler.graph.sidecar import SymbolMeta
from context_compiler.mcp.cone import (
    FRONTIER_CAP,
    HUB_SKIP_DEGREE,
    MAX_DEPTH_CAP,
    compute_impact_cone,
    render_cone,
)


def _meta(fqn: str, file: str = "x.py") -> SymbolMeta:
    return SymbolMeta(
        fqn=fqn,
        kind="function",
        file=file,
        repr_L2_tokens=1,
        repr_L3_tokens=1,
        repr_L2_refs=(),
        repr_L3_refs=(),
        identity_tokens=1,
        provenance_tokens=1,
        evaluable=None,
    )


class StubReverse:
    """``{(edge_type, node): [callers]}`` -- a fixed adjacency, no I/O."""

    def __init__(self, adjacency: dict[int, list[int]]):
        self.adjacency = adjacency
        self.calls: list[tuple[str, int]] = []

    def read(self, edge_type: str, node: int) -> list[int]:
        self.calls.append((edge_type, node))
        return list(self.adjacency.get(node, []))


# -- a small 3-hop chain: 0 <- 1 <- 2 <- 3 ---------------------------------


def _chain_fixture():
    sidecar = {n: _meta(f"pkg.mod.f{n}") for n in range(4)}
    reverse = StubReverse({0: [1], 1: [2], 2: [3]})
    degrees = {n: 0 for n in sidecar}
    in_degrees = {0: 1, 1: 1, 2: 1, 3: 0}
    return sidecar, reverse, degrees, in_degrees


def test_depth_one_finds_direct_caller_only():
    sidecar, reverse, degrees, in_degrees = _chain_fixture()
    result = compute_impact_cone(0, 1, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert result.depth_reached == 1
    assert result.counts_by_depth == {1: 1}
    assert {e.node for e in result.top} == {1}


def test_max_depth_is_capped_at_two_even_if_requested_higher():
    sidecar, reverse, degrees, in_degrees = _chain_fixture()
    result = compute_impact_cone(0, 50, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert result.depth_reached <= MAX_DEPTH_CAP
    # node 3 is three hops away -- must not appear even though the chain has it.
    assert 3 not in {e.node for e in result.top}


def test_stops_when_frontier_is_exhausted():
    sidecar, reverse, degrees, in_degrees = _chain_fixture()
    result = compute_impact_cone(3, 2, reverse, sidecar, degrees, in_degrees, len(sidecar))
    # node 3 has no callers in this stub graph.
    assert result.counts_by_depth.get(1, 0) == 0
    assert not result.truncated


# -- hub skip ----------------------------------------------------------


def test_hub_above_threshold_is_skipped_not_read():
    sidecar = {0: _meta("pkg.hub"), 1: _meta("pkg.caller")}
    reverse = StubReverse({0: [1]})
    degrees = {0: 0, 1: 0}
    in_degrees = {0: HUB_SKIP_DEGREE + 1}
    result = compute_impact_cone(0, 2, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert result.hubs_skipped == [0]
    assert reverse.calls == []  # never read
    assert result.counts_by_depth.get(1, 0) == 0


def test_node_at_exactly_the_threshold_is_not_a_hub():
    sidecar = {0: _meta("pkg.hub"), 1: _meta("pkg.caller")}
    reverse = StubReverse({0: [1]})
    degrees = {0: 0, 1: 0}
    in_degrees = {0: HUB_SKIP_DEGREE}
    result = compute_impact_cone(0, 2, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert result.hubs_skipped == []
    assert ("CALLS", 0) in reverse.calls


# -- frontier cap --------------------------------------------------------


def test_frontier_cap_truncates_and_reports_it():
    n = FRONTIER_CAP + 50
    sidecar = {i: _meta(f"pkg.f{i}") for i in range(n + 1)}
    # node 0's callers are 1..n -- more than the cap.
    reverse = StubReverse({0: list(range(1, n + 1))})
    degrees = {i: 0 for i in sidecar}
    in_degrees = {i: 0 for i in sidecar}
    result = compute_impact_cone(0, 2, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert result.truncated
    assert result.truncation_reason == "frontier cap"
    # the *next* hop only reads the capped frontier, not all n callers.
    assert len({c for _, c in reverse.calls if c != 0}) <= FRONTIER_CAP


# -- hard deadline --------------------------------------------------------


def test_deadline_truncates_with_a_fake_clock():
    sidecar, reverse, degrees, in_degrees = _chain_fixture()
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])

    def fake_clock():
        return next(ticks, 100.0)

    result = compute_impact_cone(
        0, 2, reverse, sidecar, degrees, in_degrees, len(sidecar),
        deadline_seconds=10.0, clock=fake_clock,
    )
    assert result.truncated
    assert result.truncation_reason == "deadline"


# -- ranking and rendering ------------------------------------------------


def test_top_is_ranked_and_capped_at_thirty():
    n = 40
    sidecar = {i: _meta(f"pkg.f{i}") for i in range(n + 1)}
    reverse = StubReverse({0: list(range(1, n + 1))})
    degrees = {i: 0 for i in sidecar}
    in_degrees = {i: 0 for i in sidecar}
    result = compute_impact_cone(0, 1, reverse, sidecar, degrees, in_degrees, len(sidecar))
    assert len(result.top) == 30


def test_render_cone_never_uses_what_breaks_wording():
    sidecar, reverse, degrees, in_degrees = _chain_fixture()
    result = compute_impact_cone(0, 2, reverse, sidecar, degrees, in_degrees, len(sidecar))
    text = render_cone(sidecar[0].fqn, result)
    assert "what breaks" not in text.lower()
    assert "potentially affected" in text.lower()


def test_render_cone_groups_by_file():
    sidecar = {
        0: _meta("pkg.a.target"),
        1: _meta("pkg.a.caller_one", file="a.py"),
        2: _meta("pkg.b.caller_two", file="b.py"),
    }
    reverse = StubReverse({0: [1, 2]})
    degrees = {n: 0 for n in sidecar}
    in_degrees = {n: 0 for n in sidecar}
    result = compute_impact_cone(0, 1, reverse, sidecar, degrees, in_degrees, len(sidecar))
    text = render_cone(sidecar[0].fqn, result)
    assert "# a.py" in text
    assert "# b.py" in text
    assert text.index("# a.py") < text.index("caller_one")
