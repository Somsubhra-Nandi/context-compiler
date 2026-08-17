"""Item 7: the MCP product surface, stdio transport (spec Sec 8).

Three tools, all read-only consumers of ``graph.compile`` and ``emit.render``:

    compile_context(task | seeds, budget)   the product
    explain_inclusion(fqn, task | seeds)    full derivation chain, not budget-bound
    impact_cone(fqn, max_depth)             bounded reverse-CALLS closure

Everything that decides -- the fixpoint, the profile scan, the packer, the cost
model -- lives in ``graph/`` and is untouched here. This module resolves seeds
(``PLACEHOLDER(item-8)``, see ``seeds.py``), calls ``compile_context``/``emit``,
and renders. State (sidecar, offset index, the one Bolt connection) is loaded
once in ``main()`` before the stdio loop starts; see ``state.py``.

    python -m context_compiler.mcp.server

Env vars: ``CC_SYMBOLS``, ``CC_OFFSETS``, ``CC_EDGES``, ``CC_BOLT_URI``,
``CC_BUDGET`` -- see ``config.py`` for defaults.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

from ..emit import emit
from .cone import compute_impact_cone, render_cone
from .config import Config
from .explain import explain_inclusion as _explain_inclusion
from .explain import render_explanation
from .seeds import SeedResolutionError, resolve_seed, resolve_seeds, resolve_task
from .state import ServerState, StartupError, load_state

server = MCPServer(
    "context-compiler",
    instructions=(
        "Compiles a token-budgeted, structurally-closed context for a Django "
        "codebase task: given a task description or explicit symbol names, "
        "returns exactly the declarations, bodies and dependency identities "
        "needed to answer it, under a fixed token budget. Prefer compile_context "
        "over grepping the repo yourself when the question is about how a "
        "specific function, method or class behaves and what it depends on."
    ),
)

_STATE: ServerState | None = None


def get_state() -> ServerState:
    if _STATE is None:
        raise RuntimeError("server state not initialised -- call main() before serving")
    return _STATE


def set_state(state: ServerState) -> None:
    global _STATE
    _STATE = state


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _resolve_ids(task: str | None, seeds: list[str] | None, state: ServerState) -> list[int]:
    if seeds:
        return resolve_seeds(seeds, state.by_fqn)
    if task:
        return resolve_task(task, state.sidecar)
    raise SeedResolutionError("either `task` or `seeds` must be given")


@server.tool(structured_output=False)
def compile_context(
    task: str | None = None, seeds: list[str] | None = None, budget: int | None = None
) -> CallToolResult:
    """Compile a token-budgeted context for a task and return it, ready to read.

    Give either `seeds` (explicit fully-qualified or unambiguous-suffix symbol
    names, e.g. `django.db.models.query.QuerySet.filter` or just
    `QuerySet.filter`) or a `task` description. `seeds` is far more reliable --
    task-based resolution is a PLACEHOLDER(item-8): a plain token-overlap match
    against symbol names, not a real search. Pass explicit `seeds` whenever you
    already know which symbol(s) the question is about.

    Returns the compiled context as text (declarations, bodies, provenance and
    an identity index, already grouped and budgeted) plus a trailing JSON block
    with the structured figures: status, profile, symbol/closure counts,
    budgeted vs. actual tokens, round trips and latency. `status` is either
    `OK`, `DEMOTED:<profile>` (budget too tight for full detail, richness was
    traded down), or `CLOSURE_BUDGET_EXCEEDED` (even the minimum mandatory
    closure of the seeds does not fit -- narrow the seed set or raise `budget`).
    """
    state = get_state()
    try:
        ids = _resolve_ids(task, seeds, state)
    except SeedResolutionError as exc:
        return _error(str(exc))

    b = budget or state.config.budget
    t0 = time.perf_counter()
    ctx = state.compiler.compile_context(ids, b)
    out = emit(ctx, state.source, state.sidecar)
    latency_ms = (time.perf_counter() - t0) * 1000

    state.last_context = ctx
    state.last_seeds = ids

    structured = {
        "status": ctx.status,
        "profile": ctx.profile.name if ctx.profile else None,
        "emitted_symbols": len(out.order),
        "closure_size": len(ctx.levels),
        "budget": b,
        "budgeted_tokens": out.budgeted_tokens,
        "actual_tokens": out.tokens,
        "token_margin": out.token_margin,
        "round_trips": ctx.stats.round_trips,
        "latency_ms": round(latency_ms, 1),
        "seeds_resolved": [state.sidecar[n].fqn for n in ids if n in state.sidecar],
    }
    return CallToolResult(
        content=[
            TextContent(type="text", text=out.text),
            TextContent(type="text", text=json.dumps(structured, indent=2)),
        ],
    )


@server.tool(structured_output=False)
def explain_inclusion(
    fqn: str, task: str | None = None, seeds: list[str] | None = None
) -> CallToolResult:
    """Explain why one symbol is (or is not) in a compiled context.

    Renders the full derivation chain back to a seed -- every rule that fired,
    not just the one-line trailer `compile_context`'s output shows next to each
    symbol. Not budget-bound: this can be verbose where the compiled context
    cannot afford to be.

    By default this explains inclusion in the most recently compiled context
    from this session. Pass `task` or `seeds` to compile a fresh context first
    (same placeholder resolver as `compile_context`) and explain against that
    instead. If neither a prior context nor `task`/`seeds` is available, this
    returns an error asking you to call `compile_context` first.
    """
    state = get_state()
    try:
        node = resolve_seed(fqn, state.by_fqn)
    except SeedResolutionError as exc:
        return _error(str(exc))

    if task or seeds:
        try:
            ids = _resolve_ids(task, seeds, state)
        except SeedResolutionError as exc:
            return _error(str(exc))
        ctx = state.compiler.compile_context(ids, state.config.budget)
        state.last_context = ctx
        state.last_seeds = ids
    else:
        ctx = state.last_context
        if ctx is None:
            return _error(
                "no compiled context available yet -- call compile_context first, "
                "or pass `task`/`seeds` to this call to compile one"
            )

    explanation = _explain_inclusion(node, ctx, state.sidecar)
    return CallToolResult(
        content=[
            TextContent(type="text", text=render_explanation(explanation)),
            TextContent(type="text", text=json.dumps(asdict(explanation), indent=2)),
        ],
    )


@server.tool(structured_output=False)
def impact_cone(fqn: str, max_depth: int = 2) -> CallToolResult:
    """Reverse-CALLS closure: what could be *potentially* affected if `fqn` changes.

    This is an over-approximation of reachability, not a claim about behavior --
    it reports "potentially affected", never "what breaks". `max_depth` is
    capped at 2 hops. Bounded for engine reasons (docs/spikes/graph-item-5-results.md
    A3.1: there is no batched reverse read on this database): callers with more
    than 500 in-degree are skipped rather than read (reported, not silently
    dropped), at most 200 nodes are expanded per hop, and the call gives up
    after 10 seconds and returns whatever it has with `truncated: true`.

    Returns counts by depth plus the top 30 callers by relevance, grouped by
    file -- never a raw dump of every affected symbol.
    """
    state = get_state()
    try:
        node = resolve_seed(fqn, state.by_fqn)
    except SeedResolutionError as exc:
        return _error(str(exc))

    result = compute_impact_cone(
        node,
        max_depth,
        state.reverse,
        state.sidecar,
        state.degrees,
        state.in_degrees,
        len(state.sidecar),
    )
    root_fqn = state.sidecar[node].fqn
    structured = {
        "root": root_fqn,
        "depth_reached": result.depth_reached,
        "counts_by_depth": result.counts_by_depth,
        "truncated": result.truncated,
        "truncation_reason": result.truncation_reason,
        "hubs_skipped": [
            state.sidecar[n].fqn for n in result.hubs_skipped if n in state.sidecar
        ],
        "top": [asdict(e) for e in result.top],
        "seconds": round(result.seconds, 3),
    }
    return CallToolResult(
        content=[
            TextContent(type="text", text=render_cone(root_fqn, result)),
            TextContent(type="text", text=json.dumps(structured, indent=2)),
        ],
    )


def main() -> int:
    config = Config.from_env()
    try:
        state = load_state(config)
    except StartupError as exc:
        print(f"context-compiler: {exc}", file=sys.stderr)
        return 1

    print(
        f"context-compiler: {state.startup.n_symbols:,} symbols, "
        f"sidecar {state.startup.sidecar_ms}ms, bolt {state.startup.bolt_ms}ms, "
        f"rss {state.startup.memory_kb // 1024}MB",
        file=sys.stderr,
    )
    set_state(state)
    try:
        server.run(transport="stdio")
    finally:
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
