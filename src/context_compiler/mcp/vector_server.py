"""Vector-only MCP surface for the frozen Arm A baseline.

This process deliberately exposes only ``vector_context``.  It has no task-text
resolver, graph client, graph expansion, or compiler entry point.  The vector
index, symbol sidecar, and offset index are validated and loaded once before the
stdio loop starts.

Run with::

    CC_VECTOR_INDEX=/path/to/embeddings \
    CC_VECTOR_SYMBOLS=/path/to/symbols.jsonl \
    CC_VECTOR_OFFSETS=/path/to/offsets.json \
    python -m context_compiler.mcp.vector_server
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

from ..baseline import arm_a
from ..emit import OffsetTextSource, TextSource, emit
from ..emit.source import load_offsets
from ..graph.sidecar import SymbolMeta, load_sidecar


DEFAULT_BUDGET = 8_000


class VectorStartupError(RuntimeError):
    """Actionable startup failure raised before the MCP loop starts."""


@dataclass(frozen=True)
class VectorConfig:
    index: Path
    symbols: Path
    offsets: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "VectorConfig":
        values = os.environ if env is None else env
        required = ("CC_VECTOR_INDEX", "CC_VECTOR_SYMBOLS", "CC_VECTOR_OFFSETS")
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise VectorStartupError(
                "missing required environment variable(s): " + ", ".join(missing)
            )
        return cls(
            index=Path(values["CC_VECTOR_INDEX"]).expanduser().resolve(),
            symbols=Path(values["CC_VECTOR_SYMBOLS"]).expanduser().resolve(),
            offsets=Path(values["CC_VECTOR_OFFSETS"]).expanduser().resolve(),
        )


@dataclass(frozen=True)
class VectorServerState:
    config: VectorConfig
    sidecar: Mapping[int, SymbolMeta]
    ids_by_fqn: Mapping[str, tuple[int, ...]]
    source: TextSource
    index: arm_a.VectorIndex
    startup_ms: float = 0.0
    memory_kb: int = 0


def _require_file(path: Path, variable: str, description: str) -> None:
    if not path.is_file():
        raise VectorStartupError(f"{description} not found: {path} (set {variable})")


def load_vector_state(config: VectorConfig) -> VectorServerState:
    """Validate and load every immutable Arm A runtime dependency once."""
    started = time.perf_counter()
    if not config.index.is_dir():
        raise VectorStartupError(
            f"vector index directory not found: {config.index} (set CC_VECTOR_INDEX)"
        )
    _require_file(config.symbols, "CC_VECTOR_SYMBOLS", "symbols sidecar")
    _require_file(config.offsets, "CC_VECTOR_OFFSETS", "offset index")
    for filename in ("embeddings.npy", "ids.npy", "metadata.json"):
        _require_file(config.index / filename, "CC_VECTOR_INDEX", "vector artifact")

    try:
        sidecar = load_sidecar(config.symbols)
        source_path, offsets = load_offsets(config.offsets)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VectorStartupError(f"cannot load vector sidecars: {exc}") from exc

    if source_path.resolve() != config.symbols:
        raise VectorStartupError(
            f"offset index {config.offsets} was built against {source_path.resolve()}, "
            f"not {config.symbols}"
        )
    sidecar_ids = set(sidecar)
    offset_ids = set(offsets)
    if offset_ids != sidecar_ids:
        missing = len(sidecar_ids - offset_ids)
        extra = len(offset_ids - sidecar_ids)
        raise VectorStartupError(
            "offset index does not match symbols sidecar "
            f"({missing} missing IDs, {extra} extra IDs)"
        )

    try:
        index = arm_a.load_vector_index(config.index, config.symbols, sidecar)
    except (OSError, ValueError, RuntimeError) as exc:
        raise VectorStartupError(f"invalid Arm A vector index: {exc}") from exc

    ids_by_fqn: dict[str, list[int]] = {}
    for node, meta in sidecar.items():
        ids_by_fqn.setdefault(meta.fqn, []).append(node)

    return VectorServerState(
        config=config,
        sidecar=sidecar,
        ids_by_fqn={fqn: tuple(sorted(ids)) for fqn, ids in ids_by_fqn.items()},
        source=OffsetTextSource(config.symbols, offsets),
        index=index,
        startup_ms=(time.perf_counter() - started) * 1_000,
        memory_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )


server = MCPServer(
    "context-compiler-vector",
    instructions=(
        "Returns frozen Arm A global vector retrieval for caller-supplied exact "
        "symbol FQNs. The caller must localize code and choose symbol seeds; no "
        "task-text resolution or graph/compiler tools are available."
    ),
)

_STATE: VectorServerState | None = None


def get_state() -> VectorServerState:
    if _STATE is None:
        raise RuntimeError(
            "vector server state not initialised -- call main() before serving"
        )
    return _STATE


def set_state(state: VectorServerState) -> None:
    global _STATE
    _STATE = state


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _resolve_exact_seeds(
    seeds: list[str], ids_by_fqn: Mapping[str, tuple[int, ...]]
) -> list[int]:
    if not seeds:
        raise ValueError("`seeds` must contain at least one exact symbol FQN")
    errors: list[str] = []
    resolved: list[int] = []
    for seed in seeds:
        matches = ids_by_fqn.get(seed, ())
        if not matches:
            errors.append(f"no exact symbol FQN matches {seed!r}")
        elif len(matches) > 1:
            errors.append(
                f"exact symbol FQN {seed!r} is ambiguous ({len(matches)} matches)"
            )
        else:
            resolved.append(matches[0])
    if errors:
        raise ValueError("; ".join(errors))
    return list(dict.fromkeys(resolved))


@server.tool(structured_output=False)
def vector_context(seeds: list[str], budget: int = DEFAULT_BUDGET) -> CallToolResult:
    """Retrieve frozen Arm A context from caller-chosen exact symbol FQNs.

    ``seeds`` must contain exact fully-qualified names from the configured
    symbol sidecar. No suffix matching or natural-language query is performed.
    Arm A keeps seeds at L3, ranks every non-seed by global cosine similarity,
    and admits L2 candidates under the shared cost and exact-output guard.
    """
    state = get_state()
    if budget <= 0:
        return _error("`budget` must be a positive integer")
    try:
        ids = _resolve_exact_seeds(seeds, state.ids_by_fqn)
    except (TypeError, ValueError) as exc:
        return _error(str(exc))

    started = time.perf_counter()
    context = arm_a.run_arm_a(ids, state.sidecar, state.index, state.source, budget)
    rendered = emit(context, state.source, state.sidecar)
    latency_ms = (time.perf_counter() - started) * 1_000
    emitted_ids = rendered.order
    figures = {
        "status": context.status,
        "seeds_resolved": [state.sidecar[node].fqn for node in ids],
        "emitted_symbols": [state.sidecar[node].fqn for node in emitted_ids],
        "emitted_symbol_count": len(emitted_ids),
        "actual_tokens": rendered.tokens,
        "budgeted_tokens": rendered.budgeted_tokens,
        "budget": budget,
        "vector_retrieval_latency_ms": round(context.stats.retrieval_seconds * 1_000, 1),
        "latency_ms": round(latency_ms, 1),
    }
    return CallToolResult(
        content=[
            TextContent(type="text", text=rendered.text),
            TextContent(type="text", text=json.dumps(figures, indent=2)),
        ]
    )


def main() -> int:
    try:
        config = VectorConfig.from_env()
        state = load_vector_state(config)
    except VectorStartupError as exc:
        print(f"context-compiler-vector: {exc}", file=sys.stderr)
        return 1

    print(
        f"context-compiler-vector: {len(state.sidecar):,} symbols, "
        f"{len(state.index.ids):,} vectors, startup {state.startup_ms:.1f}ms, "
        f"rss {state.memory_kb // 1024}MB",
        file=sys.stderr,
    )
    set_state(state)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BUDGET",
    "VectorConfig",
    "VectorServerState",
    "VectorStartupError",
    "get_state",
    "load_vector_state",
    "main",
    "server",
    "set_state",
    "vector_context",
]
