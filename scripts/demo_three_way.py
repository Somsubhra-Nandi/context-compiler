#!/usr/bin/env python
"""Traceback-seeded Arm A/Arm B/Arm C comparison with one budget and emitter."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from context_compiler.baseline.arm_a import load_vector_index, run_arm_a
from context_compiler.baseline.arm_b import run_arm_b
from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.client import GraphClient
from context_compiler.graph.compile import Compiler
from context_compiler.graph.expand import HARD_EDGES, Expander, ReverseReader
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar
from context_compiler.mcp.seeds import resolve_task


# Byte-identical traceback to scripts/demo_side_by_side.py.
TRACEBACK = """\
Traceback (most recent call last):
  File "django/db/models/query.py", line 1682, in filter
  File "django/db/models/query.py", line 1699, in _filter_or_exclude
  File "django/db/models/sql/query.py", line 1510, in build_filter
"""


def level_composition(rendered) -> dict[str, int]:
    counts = {"L3": 0, "L2": 0}
    for block in rendered.blocks:
        counts[block.level.name] += 1
    return counts


def arm_row(context, rendered, sidecar, elapsed_ms: float) -> dict:
    identities = set(rendered.mandatory_identities)
    hints = set(rendered.hints)
    return {
        "status": context.status,
        "profile": context.profile.name if context.profile else None,
        "emitted_symbols": len(rendered.order),
        "emitted_tokens": rendered.tokens,
        "budgeted_tokens": context.total_tokens(),
        "utilisation": round(context.utilisation(), 4),
        "level_composition": level_composition(rendered),
        "mandatory_identities": len(identities),
        "mandatory_identity_fqns": sorted(sidecar[node].fqn for node in identities),
        "identity_hints": len(hints),
        "named_not_emitted": len(identities | hints),
        "latency_ms": round(elapsed_ms, 2),
        "emitted_fqns": sorted(sidecar[node].fqn for node in rendered.order),
        "text": rendered.text,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    parser.add_argument("--edges", default="~/out/django/edges.jsonl")
    parser.add_argument("--index", default="~/out/django/embeddings")
    parser.add_argument("--budget", type=int, default=8_000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()
    sidecar = load_sidecar(symbols)
    source = source_from_symbols(symbols)
    index = load_vector_index(args.index, symbols, sidecar)
    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    diagnostics: list[str] = []
    seeds = resolve_task(TRACEBACK, sidecar, diagnostics=diagnostics)
    if len(seeds) < 3:
        raise SystemExit(
            f"traceback resolved only {len(seeds)} seed(s), fewer than three; "
            f"diagnostics={diagnostics}"
        )
    resolved = [{"id": node, "fqn": sidecar[node].fqn} for node in seeds]

    started = time.perf_counter()
    arm_a = run_arm_a(list(seeds), sidecar, index, source, budget=args.budget)
    arm_a_ms = (time.perf_counter() - started) * 1_000
    arm_a_rendered = emit(arm_a, source, sidecar)

    client = GraphClient()
    client.verify()
    with Expander(client, membership=sidecar) as expander:
        with ReverseReader(client, membership=sidecar) as reverse:
            compiler = Compiler(
                sidecar=sidecar,
                expander=expander,
                reverse=reverse,
                degrees=degrees,
                in_degrees=in_degrees,
            )
            started = time.perf_counter()
            arm_b = run_arm_b(
                list(seeds), sidecar, expander, degrees, reverse=reverse, budget=args.budget
            )
            arm_b_ms = (time.perf_counter() - started) * 1_000
            arm_b_rendered = emit(arm_b, source, sidecar)

            started = time.perf_counter()
            arm_c = compiler.compile_context(list(seeds), args.budget)
            arm_c_ms = (time.perf_counter() - started) * 1_000
            arm_c_rendered = emit(arm_c, source, sidecar)
    client.close()

    result = {
        "traceback": TRACEBACK,
        "diagnostics": diagnostics,
        "seeds": resolved,
        "identical_seed_ids": list(seeds),
        "budget": args.budget,
        "arm_a_vector": arm_row(arm_a, arm_a_rendered, sidecar, arm_a_ms),
        "arm_b_graph_top_k": arm_row(arm_b, arm_b_rendered, sidecar, arm_b_ms),
        "arm_c_context_compiler": arm_row(arm_c, arm_c_rendered, sidecar, arm_c_ms),
    }
    destination = Path(args.out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
