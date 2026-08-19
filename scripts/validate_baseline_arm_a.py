#!/usr/bin/env python
"""Run the frozen 200-trial Arm A evaluation without graph access.

The seed pool and sampler are imported from the same module used by
``validate_baseline_arm_b.py``.  By default, this script also verifies every
sampled seed list against the canonical Arm B raw rows before starting.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from context_compiler.baseline.arm_a import EXCEEDED, load_vector_index, run_arm_a
from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.budget import mandatory_identities
from context_compiler.graph.sidecar import load_sidecar
from context_compiler.graph.validate import eligible_seeds, sample_seed_sets


def stat(values) -> dict:
    values = sorted(values)
    if not values:
        return {}
    n = len(values)
    return {
        "min": values[0],
        "median": round(statistics.median(values), 4),
        "p90": values[min(n - 1, int(n * 0.90))],
        "p99": values[min(n - 1, int(n * 0.99))],
        "max": values[-1],
        "mean": round(statistics.mean(values), 4),
    }


def verify_reference_seeds(path: Path, seed_sets: list[list[int]]) -> None:
    reference = json.loads(path.read_text())
    rows = reference.get("arm_b_rows")
    if not isinstance(rows, list):
        raise ValueError(f"{path} has no arm_b_rows seed reference")
    expected = [row["seeds"] for row in rows]
    if seed_sets != expected:
        for index, (actual, prior) in enumerate(zip(seed_sets, expected), 1):
            if actual != prior:
                raise ValueError(
                    f"sampled seed mismatch at trial {index}: {actual} != {prior}"
                )
        raise ValueError(
            f"sampled/reference trial counts differ: {len(seed_sets)} != {len(expected)}"
        )


def summary(rows: list[dict]) -> dict:
    ok = [row for row in rows if row["status"] != EXCEEDED]
    return {
        "status": {
            status: sum(row["status"] == status for row in rows)
            for status in sorted({row["status"] for row in rows})
        },
        "emitted_symbols": stat(row["emitted_symbols"] for row in ok),
        "emitted_tokens": stat(row["emitted_tokens"] for row in ok),
        "budgeted_tokens": stat(row["budgeted_tokens"] for row in ok),
        "utilisation": stat(row["utilisation"] for row in ok),
        "candidate_count": stat(row["candidates"] for row in rows),
        "admitted_count": stat(row["admitted"] for row in ok),
        "trimmed_for_emission": stat(row["trimmed_for_emission"] for row in ok),
        "identity_only_references": stat(row["identity_only_references"] for row in ok),
        "identity_only_reference_tokens": stat(
            row["identity_only_reference_tokens"] for row in ok
        ),
        "query_construction_latency_ms": stat(
            row["query_construction_latency_ms"] for row in rows
        ),
        "ranking_latency_ms": stat(row["ranking_latency_ms"] for row in rows),
        "vector_retrieval_latency_ms": stat(
            row["vector_retrieval_latency_ms"] for row in rows
        ),
        "admission_latency_ms": stat(row["admission_latency_ms"] for row in rows),
        "emission_guard_latency_ms": stat(
            row["emission_guard_latency_ms"] for row in rows
        ),
        "total_arm_latency_ms": stat(row["total_arm_latency_ms"] for row in rows),
        "budget_overruns": sum(row["emitted_tokens"] > row["budget"] for row in ok),
        "cost_overruns": sum(row["budgeted_tokens"] > row["budget"] for row in ok),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    parser.add_argument("--index", default="~/out/django/embeddings")
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--budget", type=int, default=8_000)
    parser.add_argument("--rng-seed", type=int, default=20260817)
    parser.add_argument(
        "--reference-seeds",
        default="docs/spikes/data/baseline-arm-b-200.json",
        help="canonical Arm B raw rows used to assert ID-for-ID sampling equality",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    symbols = Path(args.symbols).expanduser()
    sidecar = load_sidecar(symbols)
    source = source_from_symbols(symbols)
    pool = eligible_seeds(symbols)
    seed_sets = sample_seed_sets(pool, args.trials, args.seeds, args.rng_seed)
    reference_path = Path(args.reference_seeds).expanduser()
    verify_reference_seeds(reference_path, seed_sets)
    index = load_vector_index(args.index, symbols, sidecar)
    print(
        f"sidecar {len(sidecar):,}; eligible pool {len(pool):,}; "
        f"trials {len(seed_sets)}; budget {args.budget:,}; seed rows verified",
        file=sys.stderr,
    )

    rows = []
    started = time.perf_counter()
    for trial, seeds in enumerate(seed_sets, 1):
        context = run_arm_a(list(seeds), sidecar, index, source, budget=args.budget)
        rendered = emit(context, source, sidecar)
        identities = mandatory_identities(context.levels, sidecar)
        rows.append(
            {
                "trial": trial,
                "seeds": seeds,
                "status": context.status,
                "profile": context.profile.name,
                "budget": args.budget,
                "emitted_symbols": len(rendered.order),
                "emitted_tokens": rendered.tokens,
                "budgeted_tokens": context.total_tokens(),
                "utilisation": round(context.utilisation(), 4),
                "candidates": context.stats.candidates,
                "admitted": context.stats.admitted,
                "skipped_too_large": context.stats.skipped_too_large,
                "trimmed_for_emission": context.stats.trimmed_for_emission,
                "identity_only_references": len(identities),
                "identity_only_reference_tokens": sum(
                    sidecar[node].identity_tokens for node in identities
                ),
                "query_construction_latency_ms": round(
                    context.stats.query_seconds * 1_000, 3
                ),
                "ranking_latency_ms": round(context.stats.ranking_seconds * 1_000, 3),
                "vector_retrieval_latency_ms": round(
                    context.stats.retrieval_seconds * 1_000, 3
                ),
                "admission_latency_ms": round(
                    context.stats.admission_seconds * 1_000, 3
                ),
                "emission_guard_latency_ms": round(
                    context.stats.emission_guard_seconds * 1_000, 3
                ),
                "total_arm_latency_ms": round(context.stats.seconds * 1_000, 3),
            }
        )
        if trial % 20 == 0:
            print(f"  {trial}/{len(seed_sets)}", file=sys.stderr)

    result = {
        "configuration": {
            "trials": len(seed_sets),
            "seed_count": args.seeds,
            "pool": len(pool),
            "rng_seed": args.rng_seed,
            "budget": args.budget,
            "sidecar_symbols": len(sidecar),
            "reference_seeds": str(reference_path),
            "seed_sets_match_reference": True,
            "representation": "repr_L2_text; one symbol per vector",
            "query_definition": "L2-normalized arithmetic mean of normalized seed vectors",
            "ranking": "descending cosine similarity, then ascending node ID",
            "graph_calls": False,
            "closure_scored": False,
        },
        "index_build": dict(index.metadata),
        "benchmark_elapsed_seconds": round(time.perf_counter() - started, 3),
        "arm_a": summary(rows),
        "arm_a_rows": rows,
    }
    destination = Path(args.out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"configuration": result["configuration"], "arm_a": result["arm_a"]}, indent=2))
    print(f"wrote {destination}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
