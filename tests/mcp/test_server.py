"""Tool schemas, structured errors, and a full compile_context round trip.

No HydraDB: the ``Compiler`` here is wired over a plain in-memory expander
function instead of ``graph.expand.Expander``, exactly as ``test_emit.py``
does for the emission layer. This exercises the *whole* stack a live MCP call
would run -- seed resolution, ``compile_context``, ``emit`` -- without a
database, which is what "tool schemas validate ... errors are structured, not
tracebacks" needs to be checked against.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from context_compiler.emit.source import MappingTextSource, SymbolRecord
from context_compiler.extract.representations import count_tokens
from context_compiler.graph.compile import Compiler
from context_compiler.graph.sidecar import SymbolMeta
from context_compiler.mcp import server
from context_compiler.mcp.cone import ReverseRead
from context_compiler.mcp.config import Config
from context_compiler.mcp.state import ServerState, StartupStats

SEED, DEP = 1, 2


def _fixture_state() -> ServerState:
    seed_l2 = "def run(self):\n    ...\n"
    seed_l3 = "def run(self):\n    return self.step()\n"
    dep_l2 = "def step(self):\n    ...\n"
    dep_l3 = "def step(self):\n    return 1\n"

    def meta(fqn, l2, l3, refs=()):
        identity = f"{fqn} — x.py:1"
        return SymbolMeta(
            fqn=fqn, kind="method", file="x.py",
            repr_L2_tokens=count_tokens(l2), repr_L3_tokens=count_tokens(l3),
            repr_L2_refs=refs, repr_L3_refs=refs,
            identity_tokens=count_tokens(identity), provenance_tokens=5,
            evaluable=None,
        )

    sidecar = {
        SEED: meta("pkg.Seed.run", seed_l2, seed_l3),
        DEP: meta("pkg.Dep.step", dep_l2, dep_l3),
    }
    records = {
        SEED: SymbolRecord(SEED, "pkg.Seed.run", "method", "x.py", 1, seed_l2, seed_l3),
        DEP: SymbolRecord(DEP, "pkg.Dep.step", "method", "x.py", 5, dep_l2, dep_l3),
    }
    adjacency = {SEED: [("CALLS", DEP)]}

    def expander(frontier):
        return [(n, et, dst) for n in frontier for et, dst in adjacency.get(n, [])]

    compiler = Compiler(sidecar=sidecar, expander=expander, reverse=None, degrees={}, in_degrees={})
    config = Config(
        symbols=Path("/fixture/symbols.jsonl"), offsets=Path("/fixture/offsets.json"),
        edges=Path("/fixture/edges.jsonl"), bolt_uri="bolt://fixture", budget=8000,
    )
    return ServerState(
        config=config,
        sidecar=sidecar,
        by_fqn={m.fqn: n for n, m in sidecar.items()},
        degrees={},
        in_degrees={},
        source=MappingTextSource(records),
        client=None,
        expander=expander,
        reverse=None,
        compiler=compiler,
        startup=StartupStats(),
    )


def setup_function(_fn):
    server.set_state(_fixture_state())


def _figures(result):
    """The trailing JSON block -- Claude Code's MCP client drops ``content``
    entirely when ``structured_content`` is also set (see the results doc), so
    tools carry the structured figures only as a second text block."""
    return json.loads(result.content[-1].text)


# -- tool schemas ---------------------------------------------------------


def test_all_three_tools_are_registered_with_expected_parameters():
    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    assert set(tools) == {"compile_context", "explain_inclusion", "impact_cone"}
    assert set(tools["compile_context"].input_schema["properties"]) == {"task", "seeds", "budget"}
    assert set(tools["explain_inclusion"].input_schema["properties"]) == {"fqn", "task", "seeds"}
    assert set(tools["impact_cone"].input_schema["properties"]) == {"fqn", "max_depth"}
    assert tools["impact_cone"].input_schema["required"] == ["fqn"]


def test_tool_description_describes_scoped_task_resolution():
    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    assert "traceback" in tools["compile_context"].description.lower()


# -- compile_context: happy path -------------------------------------------


def test_compile_context_happy_path_returns_text_and_structured_figures():
    result = server.compile_context(seeds=["pkg.Seed.run"], budget=8000)
    assert result.is_error is None or result.is_error is False
    assert len(result.content) == 2
    assert "Seed.run" in result.content[0].text or "def run" in result.content[0].text
    figures = _figures(result)
    assert figures["status"] == "OK"
    assert figures["token_margin"] <= 0
    assert figures["seeds_resolved"] == ["pkg.Seed.run"]


def test_compile_context_by_suffix_matches_compile_context_by_full_fqn():
    a = server.compile_context(seeds=["pkg.Seed.run"], budget=8000)
    b = server.compile_context(seeds=["Seed.run"], budget=8000)
    assert _figures(a)["actual_tokens"] == _figures(b)["actual_tokens"]


# -- structured errors, not tracebacks --------------------------------


def test_compile_context_missing_seed_is_a_structured_error():
    result = server.compile_context(seeds=["NoSuchSymbol"])
    assert result.is_error is True
    assert "NoSuchSymbol" in result.content[0].text


def test_compile_context_neither_task_nor_seeds_is_a_structured_error():
    result = server.compile_context()
    assert result.is_error is True


def test_explain_inclusion_without_prior_context_is_a_structured_error():
    result = server.explain_inclusion(fqn="pkg.Seed.run")
    assert result.is_error is True
    assert "compile_context" in result.content[0].text


def test_explain_inclusion_unknown_fqn_is_a_structured_error():
    server.compile_context(seeds=["pkg.Seed.run"])
    result = server.explain_inclusion(fqn="totally.unknown.symbol")
    assert result.is_error is True


def test_impact_cone_unknown_fqn_is_a_structured_error():
    result = server.impact_cone(fqn="totally.unknown.symbol")
    assert result.is_error is True


# -- explain_inclusion, once a context exists --------------------------


def test_explain_inclusion_after_compile_renders_a_chain():
    server.compile_context(seeds=["pkg.Seed.run"])
    result = server.explain_inclusion(fqn="pkg.Dep.step")
    assert result.is_error is None or result.is_error is False
    text = result.content[0].text
    assert "pkg.Seed.run" in text
    assert _figures(result)["chain"]


# -- impact_cone, against the fixture's own reverse adjacency -----------


class _Reverse:
    def read(self, edge_type: str, node: int) -> list[int]:
        return [SEED] if node == DEP and edge_type == "CALLS" else []


def test_impact_cone_happy_path_never_says_what_breaks():
    state = server.get_state()
    state.reverse = _Reverse()
    result = server.impact_cone(fqn="pkg.Dep.step")
    text = result.content[0].text
    assert "what breaks" not in text.lower()
    assert "potentially affected" in text.lower()
    assert _figures(result)["root"] == "pkg.Dep.step"
