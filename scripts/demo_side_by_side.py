#!/usr/bin/env python
"""Item 10a demo: traceback-seeded compiler vs Arm B, same seeds/budget/emitter.

    python scripts/demo_side_by_side.py --out docs/spikes/data/demo-side-by-side.json

Seeds are resolved through Item 8's ``resolve_task`` on a real traceback, not
hand-picked FQNs, so both arms take an identical, independently-reproducible
seed list.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from context_compiler.baseline.arm_b import run_arm_b
from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.budget import is_closed
from context_compiler.graph.client import GraphClient
from context_compiler.graph.compile import Compiler
from context_compiler.graph.expand import (
    HARD_EDGES,
    CachingExpander,
    Expander,
    ReverseReader,
)
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar
from context_compiler.mcp.seeds import resolve_task

TRACEBACK = """\
Traceback (most recent call last):
  File "django/db/models/query.py", line 1682, in filter
  File "django/db/models/query.py", line 1699, in _filter_or_exclude
  File "django/db/models/sql/query.py", line 1510, in build_filter
"""

HOP2_TARGETS = [
    "django.db.models.sql.where.WhereNode",
    "django.db.models.expressions.ColPairs",
    "django.db.models.sql.query.JoinPromoter",
    "django.db.models.expressions.OuterRef",
    "django.db.models.expressions.Exists",
    "django.db.models.expressions.ResolvedOuterRef",
]


def level_composition(rendered_blocks):
    counts = {"L3": 0, "L2": 0}
    for block in rendered_blocks:
        counts[block.level.name] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    parser.add_argument("--edges", default="~/out/django/edges.jsonl")
    parser.add_argument("--budget", type=int, default=8_000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()
    sidecar = load_sidecar(symbols)
    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    source = source_from_symbols(symbols)

    diagnostics: list[str] = []
    seeds_a = resolve_task(TRACEBACK, sidecar, diagnostics=diagnostics)
    seeds_b = resolve_task(TRACEBACK, sidecar, diagnostics=[])
    assert set(seeds_a) == set(seeds_b), "both arms must receive the identical seed set"
    seeds = seeds_a

    if len(seeds) < 3:
        raise SystemExit(
            f"traceback resolved only {len(seeds)} seed(s), fewer than three -- "
            f"stopping per the demo's stop condition. diagnostics={diagnostics}"
        )

    resolved = [{"id": s, "fqn": sidecar[s].fqn} for s in seeds]

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
            t0 = time.perf_counter()
            ctx = compiler.compile_context(list(seeds), args.budget)
            compiler_ms = (time.perf_counter() - t0) * 1000
            rendered = emit(ctx, source, sidecar)
            compiler_dangling = set(rendered.mandatory_identities)
            compiler_hints = set(rendered.hints)
            compiler_closed = is_closed(ctx.levels, CachingExpander(expander))

            t0 = time.perf_counter()
            arm_b_ctx = run_arm_b(
                list(seeds), sidecar, expander, degrees, reverse=reverse, budget=args.budget
            )
            arm_b_ms = (time.perf_counter() - t0) * 1000
            arm_b_rendered = emit(arm_b_ctx, source, sidecar)
            arm_b_dangling = set(arm_b_rendered.mandatory_identities)
            arm_b_hints = set(arm_b_rendered.hints)
            arm_b_closed = is_closed(arm_b_ctx.levels, CachingExpander(expander))
    client.close()

    compiler_emitted = {sidecar[n].fqn for n in rendered.order}
    arm_b_emitted = {sidecar[n].fqn for n in arm_b_rendered.order}
    compiler_named_not_emitted = compiler_dangling | compiler_hints
    arm_b_named_not_emitted = arm_b_dangling | arm_b_hints
    compiler_named = compiler_emitted | {sidecar[n].fqn for n in compiler_named_not_emitted}
    arm_b_named = arm_b_emitted | {sidecar[n].fqn for n in arm_b_named_not_emitted}

    hop2 = {}
    for fqn in HOP2_TARGETS:
        hop2[fqn] = {
            "compiler_emitted": fqn in compiler_emitted,
            "compiler_named": fqn in compiler_named,
            "arm_b_emitted": fqn in arm_b_emitted,
            "arm_b_named": fqn in arm_b_named,
        }

    result = {
        "traceback": TRACEBACK,
        "diagnostics": diagnostics,
        "seeds": resolved,
        "budget": args.budget,
        "compiler": {
            "status": ctx.status,
            "profile": ctx.profile.name if ctx.profile else None,
            "emitted_symbols": len(rendered.order),
            "emitted_tokens": rendered.tokens,
            "budgeted_tokens": ctx.total_tokens(),
            "utilisation": round(ctx.utilisation(), 4),
            "level_composition": level_composition(rendered.blocks),
            "mandatory_identities": len(compiler_dangling),
            "mandatory_identity_fqns": sorted(sidecar[n].fqn for n in compiler_dangling),
            "identity_hints": len(compiler_hints),
            "identity_hint_fqns": sorted(sidecar[n].fqn for n in compiler_hints),
            "named_not_emitted": len(compiler_named_not_emitted),
            "is_closed": compiler_closed,
            "round_trips": ctx.stats.round_trips,
            "compile_latency_ms": round(compiler_ms, 2),
            "emitted_fqns": sorted(compiler_emitted),
            "text": rendered.text,
        },
        "arm_b": {
            "status": arm_b_ctx.status,
            "profile": arm_b_ctx.profile.name if arm_b_ctx.profile else None,
            "emitted_symbols": len(arm_b_rendered.order),
            "emitted_tokens": arm_b_rendered.tokens,
            "budgeted_tokens": arm_b_ctx.total_tokens(),
            "utilisation": round(arm_b_ctx.utilisation(), 4),
            "level_composition": level_composition(arm_b_rendered.blocks),
            "mandatory_identities": len(arm_b_dangling),
            "mandatory_identity_fqns": sorted(sidecar[n].fqn for n in arm_b_dangling),
            "identity_hints": len(arm_b_hints),
            "identity_hint_fqns": sorted(sidecar[n].fqn for n in arm_b_hints),
            "named_not_emitted": len(arm_b_named_not_emitted),
            "is_closed": arm_b_closed,
            "round_trips": arm_b_ctx.stats.round_trips,
            "compile_latency_ms": round(arm_b_ms, 2),
            "emitted_fqns": sorted(arm_b_emitted),
            "text": arm_b_rendered.text,
        },
        "set_diff": {
            "emitted_only_compiler": sorted(compiler_emitted - arm_b_emitted),
            "emitted_only_arm_b": sorted(arm_b_emitted - compiler_emitted),
            "named_only_compiler": sorted(compiler_named - arm_b_named),
            "named_only_arm_b": sorted(arm_b_named - compiler_named),
        },
        "hop2_from_build_filter": hop2,
    }
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
