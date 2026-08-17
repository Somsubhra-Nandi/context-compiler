#!/usr/bin/env python
"""Compile + emit one named worked example against the live Django graph.

    python scripts/worked_example.py FQN [FQN ...] --budget 8000

Used to regenerate the Item 6 worked example after Amendment A4's fixes so the
before/after token deltas are measured against a real compile, not estimated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.client import GraphClient
from context_compiler.graph.compile import Compiler
from context_compiler.graph.budget import source_tokens
from context_compiler.graph.expand import HARD_EDGES, Expander, ReverseReader
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fqns", nargs="+")
    ap.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    ap.add_argument("--edges", default="~/out/django/edges.jsonl")
    ap.add_argument("--budget", type=int, default=8000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()

    sidecar = load_sidecar(symbols)
    by_fqn = {meta.fqn: nid for nid, meta in sidecar.items()}
    seeds = []
    for fqn in args.fqns:
        if fqn not in by_fqn:
            raise SystemExit(f"not found: {fqn}")
        seeds.append(by_fqn[fqn])

    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    source = source_from_symbols(symbols)

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
            ctx = compiler.compile_context(seeds, args.budget)
            out = emit(ctx, source, sidecar)
    client.close()

    per_node = {}
    for block in out.blocks:
        per_node[block.fqn] = {
            "level": block.level.name,
            "tokens": source_tokens(sidecar[block.node], block.level),
        }

    summary = {
        "seeds": args.fqns,
        "status": ctx.status,
        "profile": ctx.profile.name if ctx.profile else None,
        "closure_symbols": len(ctx.levels),
        "emitted_symbols": len(out.order),
        "files": out.files,
        "budgeted_tokens": out.budgeted_tokens,
        "emitted_tokens": out.tokens,
        "budget": args.budget,
        "token_margin": out.token_margin,
        "mandatory_identities": len(out.mandatory_identities),
        "identity_hints": len(out.hints),
        "dedup_saved_tokens": out.dedup_saved_tokens,
        "dedup_saved_lines": out.dedup_saved_lines,
        "round_trips": ctx.stats.round_trips,
        "compile_ms": round(ctx.stats.seconds * 1000, 2),
        "seeks": out.seeks,
        "per_node": per_node,
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(out.text)
        print(f"wrote emitted text to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
