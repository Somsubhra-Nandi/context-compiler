#!/usr/bin/env python
"""Compare the shipped compiler with Item 10a Arm B on the Django graph.

The two arms receive the same deterministic seed sets and share the same live
graph, sidecar, budget and emitter. Arm B's closure check is deliberately an
independent post-run soundness check; it is not part of Arm B admission.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from context_compiler.baseline.arm_b import dangling_references, run_arm_b
from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.budget import is_closed, mandatory_identities
from context_compiler.graph.client import GraphClient
from context_compiler.graph.compile import EXCEEDED, Compiler
from context_compiler.graph.expand import (
    HARD_EDGES,
    CachingExpander,
    Expander,
    ReverseReader,
)
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar
from context_compiler.graph.validate import eligible_seeds, sample_seed_sets


def stat(values) -> dict:
    values = sorted(values)
    if not values:
        return {}
    n = len(values)
    return {
        "min": values[0],
        "median": round(statistics.median(values), 2),
        "p90": values[min(n - 1, int(n * 0.90))],
        "p99": values[min(n - 1, int(n * 0.99))],
        "max": values[-1],
    }


def status_counts(rows, key="status"):
    out: dict[str, int] = {}
    for row in rows:
        value = row[key]
        out[value] = out.get(value, 0) + 1
    return out


def arm_summary(rows, *, baseline: bool) -> dict:
    ok = [row for row in rows if row["status"] != EXCEEDED]
    return {
        "status": status_counts(rows),
        "emitted_symbols": stat(row["emitted_symbols"] for row in ok),
        "emitted_tokens": stat(row["emitted_tokens"] for row in ok),
        "budgeted_tokens": stat(row["budgeted_tokens"] for row in ok),
        "utilisation": stat(row["utilisation"] for row in ok),
        "dangling_identity_only_references": stat(
            row["dangling_references"] for row in ok
        ),
        "dangling_identity_tokens": stat(
            row["dangling_identity_tokens"] for row in ok
        ),
        "round_trips": stat(row["round_trips"] for row in rows),
        "compile_latency_ms": stat(row["compile_latency_ms"] for row in rows),
        "is_closed": (
            {
                "true": sum(row["is_closed"] is True for row in rows),
                "false": sum(row["is_closed"] is False for row in rows),
                "rate": round(sum(row["is_closed"] is True for row in rows) / len(rows), 4),
            }
            if baseline
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    parser.add_argument("--edges", default="~/out/django/edges.jsonl")
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--budget", type=int, default=8_000)
    parser.add_argument("--rng-seed", type=int, default=20260817)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()
    sidecar = load_sidecar(symbols)
    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    source = source_from_symbols(symbols)
    pool = eligible_seeds(symbols)
    seed_sets = sample_seed_sets(pool, args.trials, args.seeds, args.rng_seed)
    print(
        f"sidecar {len(sidecar):,}; eligible pool {len(pool):,}; "
        f"trials {len(seed_sets)}; budget {args.budget:,}",
        file=sys.stderr,
    )

    client = GraphClient()
    client.verify()
    compiler_rows = []
    arm_b_rows = []
    t0 = time.perf_counter()
    with Expander(client, membership=sidecar) as expander:
        with ReverseReader(client, membership=sidecar) as reverse:
            compiler = Compiler(
                sidecar=sidecar,
                expander=expander,
                reverse=reverse,
                degrees=degrees,
                in_degrees=in_degrees,
            )
            for i, seeds in enumerate(seed_sets, 1):
                ctx = compiler.compile_context(seeds, args.budget)
                rendered = emit(ctx, source, sidecar)
                dangling = mandatory_identities(ctx.levels, sidecar)
                compiler_rows.append(
                    {
                        "trial": i,
                        "seeds": seeds,
                        "status": ctx.status,
                        "profile": ctx.profile.name if ctx.profile else None,
                        "emitted_symbols": len(rendered.order),
                        "emitted_tokens": rendered.tokens,
                        "budgeted_tokens": ctx.total_tokens(),
                        "utilisation": round(ctx.utilisation(), 4),
                        "dangling_references": len(dangling),
                        "dangling_identity_tokens": sum(
                            sidecar[node].identity_tokens for node in dangling
                        ),
                        "round_trips": ctx.stats.round_trips,
                        "compile_latency_ms": round(ctx.stats.seconds * 1000, 2),
                        "candidates": ctx.stats.candidates,
                        "admitted": ctx.stats.admitted,
                    }
                )

                arm_b = run_arm_b(
                    list(seeds),
                    sidecar,
                    expander,
                    degrees,
                    reverse=reverse,
                    budget=args.budget,
                )
                arm_b_rendered = emit(arm_b, source, sidecar)
                arm_b_dangling = dangling_references(arm_b, sidecar)

                # This is a separate soundness check. It reads the required
                # outgoing edges for the produced level map and never changes
                # Arm B's result or admission path.
                verify_expander = CachingExpander(expander)
                verify_before = expander.stats.round_trips
                closed = is_closed(arm_b.levels, verify_expander)
                verify_trips = expander.stats.round_trips - verify_before
                arm_b_rows.append(
                    {
                        "trial": i,
                        "seeds": seeds,
                        "status": arm_b.status,
                        "profile": arm_b.profile.name,
                        "emitted_symbols": len(arm_b_rendered.order),
                        "emitted_tokens": arm_b_rendered.tokens,
                        "budgeted_tokens": arm_b.total_tokens(),
                        "utilisation": round(arm_b.utilisation(), 4),
                        "dangling_references": len(arm_b_dangling),
                        "dangling_identity_tokens": sum(
                            sidecar[node].identity_tokens for node in arm_b_dangling
                        ),
                        "round_trips": arm_b.stats.round_trips,
                        "verification_round_trips": verify_trips,
                        "compile_latency_ms": round(arm_b.stats.seconds * 1000, 2),
                        "candidates": arm_b.stats.candidates,
                        "admitted": arm_b.stats.admitted,
                        "is_closed": closed,
                    }
                )
                if i % 20 == 0:
                    print(f"  {i}/{len(seed_sets)}", file=sys.stderr)
    client.close()

    result = {
        "configuration": {
            "trials": len(seed_sets),
            "seed_count": args.seeds,
            "pool": len(pool),
            "rng_seed": args.rng_seed,
            "budget": args.budget,
            "sidecar_symbols": len(sidecar),
            "hard_edges": list(HARD_EDGES),
            "arm_b_closure": False,
        },
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "compiler": arm_summary(compiler_rows, baseline=False),
        "arm_b": arm_summary(arm_b_rows, baseline=True),
        "compiler_rows": compiler_rows,
        "arm_b_rows": arm_b_rows,
    }
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("configuration", "compiler", "arm_b")}, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
