"""Focused contract tests for the isolated vector-only MCP server."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from context_compiler.baseline.arm_a import VectorIndex  # noqa: E402
from context_compiler.emit import MappingTextSource, SymbolRecord  # noqa: E402
from context_compiler.extract.representations import count_tokens  # noqa: E402
from context_compiler.graph.sidecar import SymbolMeta  # noqa: E402
from context_compiler.mcp import vector_server  # noqa: E402
from context_compiler.mcp.vector_server import (  # noqa: E402
    VectorConfig,
    VectorServerState,
)


def _meta(fqn: str, l2: str, l3: str) -> SymbolMeta:
    return SymbolMeta(
        fqn=fqn,
        kind="function",
        file="pkg/mod.py",
        repr_L2_tokens=count_tokens(l2),
        repr_L3_tokens=count_tokens(l3),
        repr_L2_refs=(),
        repr_L3_refs=(),
        identity_tokens=count_tokens(f"{fqn} — pkg/mod.py:1"),
        provenance_tokens=5,
        evaluable=None,
    )


def _fixture_state() -> VectorServerState:
    texts = {
        1: ("def seed(): ...", "def seed():\n    return candidate()\n"),
        2: ("def candidate(): ...", "def candidate():\n    return 2\n"),
        3: ("def other(): ...", "def other():\n    return 3\n"),
    }
    fqns = {1: "pkg.seed", 2: "pkg.candidate", 3: "pkg.other"}
    sidecar = {
        node: _meta(fqns[node], texts[node][0], texts[node][1]) for node in texts
    }
    records = {
        node: SymbolRecord(
            id=node,
            fqn=fqns[node],
            kind="function",
            file="pkg/mod.py",
            start_line=node,
            repr_L2_text=texts[node][0],
            repr_L3_text=texts[node][1],
        )
        for node in texts
    }
    embeddings = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    ids = np.asarray([1, 2, 3], dtype=np.int64)
    index = VectorIndex(
        embeddings=embeddings,
        ids=ids,
        metadata={},
        row_by_id={1: 0, 2: 1, 3: 2},
    )
    return VectorServerState(
        config=VectorConfig(Path("/index"), Path("/symbols"), Path("/offsets")),
        sidecar=sidecar,
        ids_by_fqn={fqn: (node,) for node, fqn in fqns.items()},
        source=MappingTextSource(records),
        index=index,
    )


@pytest.fixture(autouse=True)
def _state():
    vector_server.set_state(_fixture_state())


def _figures(result):
    return json.loads(result.content[-1].text)


def test_vector_server_exposes_only_vector_context():
    tools = {tool.name: tool for tool in asyncio.run(vector_server.server.list_tools())}

    assert set(tools) == {"vector_context"}
    assert "compile_context" not in tools
    assert set(tools["vector_context"].input_schema["properties"]) == {"seeds", "budget"}
    assert "task" not in tools["vector_context"].input_schema["properties"]


def test_startup_config_requires_all_artifact_paths():
    with pytest.raises(vector_server.VectorStartupError, match="CC_VECTOR_INDEX"):
        VectorConfig.from_env({})


def test_exact_fqn_seed_resolves_and_returns_metadata():
    result = vector_server.vector_context(["pkg.seed"])

    assert not result.is_error
    assert "def seed" in result.content[0].text
    figures = _figures(result)
    assert figures["status"] == "OK"
    assert figures["seeds_resolved"] == ["pkg.seed"]
    assert figures["emitted_symbol_count"] == len(figures["emitted_symbols"])
    assert "vector_retrieval_latency_ms" in figures


def test_suffix_and_unresolved_seeds_are_useful_errors():
    suffix = vector_server.vector_context(["seed"])
    missing = vector_server.vector_context(["pkg.missing"])

    assert suffix.is_error is True
    assert "exact symbol FQN" in suffix.content[0].text
    assert missing.is_error is True
    assert "pkg.missing" in missing.content[0].text


def test_ambiguous_exact_fqn_is_rejected():
    state = _fixture_state()
    ids_by_fqn = dict(state.ids_by_fqn)
    ids_by_fqn["pkg.seed"] = (1, 2)
    vector_server.set_state(replace(state, ids_by_fqn=ids_by_fqn))

    result = vector_server.vector_context(["pkg.seed"])

    assert result.is_error is True
    assert "ambiguous" in result.content[0].text


def test_budget_is_respected():
    result = vector_server.vector_context(["pkg.seed"], budget=400)
    figures = _figures(result)

    assert figures["actual_tokens"] <= 400
    assert figures["budgeted_tokens"] <= 400


def test_rendered_vector_context_is_deterministic():
    first = vector_server.vector_context(["pkg.seed"], budget=400)
    second = vector_server.vector_context(["pkg.seed"], budget=400)

    assert first.content[0].text == second.content[0].text
    first_figures = _figures(first)
    second_figures = _figures(second)
    for key in (
        "status",
        "seeds_resolved",
        "emitted_symbols",
        "actual_tokens",
        "budgeted_tokens",
        "budget",
    ):
        assert first_figures[key] == second_figures[key]


def test_server_delegates_to_frozen_arm_a(monkeypatch):
    original = vector_server.arm_a.run_arm_a
    calls = []

    def spy(seeds, sidecar, index, source, budget):
        calls.append((seeds, sidecar, index, source, budget))
        return original(seeds, sidecar, index, source, budget)

    monkeypatch.setattr(vector_server.arm_a, "run_arm_a", spy)

    vector_server.vector_context(["pkg.seed"], budget=400)

    assert len(calls) == 1
    assert calls[0][0] == [1]
    assert calls[0][-1] == 400


def test_call_requires_no_hydradb_or_graph_client():
    state = vector_server.get_state()

    assert not hasattr(state, "client")
    assert not hasattr(state, "expander")
    assert not hasattr(state, "compiler")
    assert not vector_server.vector_context(["pkg.seed"]).is_error
