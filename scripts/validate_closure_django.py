#!/usr/bin/env python
"""Reproduce the predicted Django closure distribution with the real fixpoint.

Requires a running HydraDB node with the Django graph ingested::

    bash scripts/run_hydradb.sh start
    python -m context_compiler.graph.ingest \
        --symbols ~/out/django/symbols.jsonl --edges ~/out/django/edges.jsonl
    python scripts/validate_closure_django.py
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from context_compiler.graph.client import GraphClient
from context_compiler.graph.expand import Expander, expected_round_trips
from context_compiler.graph.sidecar import load_sidecar, sidecar_bytes
from context_compiler.graph.validate import (
    PREDICTION,
    compare,
    eligible_seeds,
    run_trials,
    sample_seed_sets,
    within_an_order_of_magnitude,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=Path, default=Path("~/out/django/symbols.jsonl"))
    ap.add_argument("--trials", type=int, default=PREDICTION["trials"])
    ap.add_argument("--seeds", type=int, default=PREDICTION["seeds_per_trial"])
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--rng-seed", type=int, default=20260817)
    ap.add_argument("--out", type=Path, default=None, help="write the full report as JSON")
    args = ap.parse_args(argv)

    symbols = args.symbols.expanduser()

    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    sidecar = load_sidecar(symbols)
    load_s = time.perf_counter() - t0
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    deep = sidecar_bytes(sidecar)
    print(
        f"sidecar: {len(sidecar):,} symbols  load {load_s:.2f}s  "
        f"deep {deep / 1e6:.1f} MB  process RSS delta {(rss1 - rss0) / 1024:.1f} MB"
    )

    pool = eligible_seeds(symbols)
    print(
        f"eligible seeds: {len(pool):,} "
        f"(kind in function/method, not under tests/, repr_L3_tokens >= 150)"
    )
    seed_sets = sample_seed_sets(pool, args.trials, args.seeds, args.rng_seed)

    client = GraphClient(batch_size=args.batch)
    client.verify()
    try:
        with Expander(client, membership=sidecar, batch_size=args.batch) as expander:
            t0 = time.perf_counter()
            dist = run_trials(seed_sets, expander, sidecar)
            wall = time.perf_counter() - t0
            stats = expander.stats
    finally:
        client.close()

    summary = dist.summary()
    ratios = compare(summary)

    print(f"\n{args.trials} trials x {args.seeds} seeds in {wall:.1f}s "
          f"({wall / args.trials * 1000:.0f} ms/trial)")
    print(f"expand(): {stats.round_trips:,} round trips, {stats.hops} hops, "
          f"{stats.edges_returned:,} edges, {stats.filtered_out:,} filtered, "
          f"{stats.seconds:.1f}s total")

    print("\n                     predicted     observed     ratio")
    rows = [
        ("closure size median", PREDICTION["closure_size"]["median"],
         summary["closure_size"]["median"], ratios["closure_size_median"]),
        ("closure size p90", PREDICTION["closure_size"]["p90"],
         summary["closure_size"]["p90"], ratios["closure_size_p90"]),
        ("closure size max", PREDICTION["closure_size"]["max"],
         summary["closure_size"]["max"], ""),
        ("L3+L2 tokens median", PREDICTION["tokens"]["median"],
         summary["tokens"]["median"], ratios["tokens_median"]),
        ("L3+L2 tokens p90", PREDICTION["tokens"]["p90"],
         summary["tokens"]["p90"], ratios["tokens_p90"]),
        ("L3+L2 tokens max", PREDICTION["tokens"]["max"], summary["tokens"]["max"], ""),
        (f"over 8000 tokens /{args.trials}", PREDICTION["over_8000"],
         summary["over_8000"], ""),
    ]
    for name, pred, obs, ratio in rows:
        r = f"{ratio:>9}" if ratio != "" else " " * 9
        print(f"  {name:<24} {pred:>9,} {obs:>12,}{r}")

    print(f"\nemitted (L2+L3) size median: {summary['emitted_size']['median']}")
    print(f"provenance complete on every non-seed entry: {summary['provenance_complete']}")
    print(f"round trips per trial: median {summary['round_trips']['median']}, "
          f"max {summary['round_trips']['max']} "
          f"(unchunked two-hop ideal is {expected_round_trips([1, 1], args.batch)})")
    verdict = "PASS" if within_an_order_of_magnitude(summary) else "FAIL"
    print(f"\norder-of-magnitude agreement: {verdict}")

    if args.out:
        report = {
            "prediction": PREDICTION,
            "observed": summary,
            "ratios": ratios,
            "wall_seconds": round(wall, 2),
            "expand": {
                "round_trips": stats.round_trips,
                "hops": stats.hops,
                "edges_returned": stats.edges_returned,
                "filtered_out": stats.filtered_out,
                "seconds": round(stats.seconds, 2),
            },
            "sidecar": {
                "symbols": len(sidecar),
                "load_seconds": round(load_s, 2),
                "deep_bytes": deep,
                "rss_delta_bytes": (rss1 - rss0) * 1024,
            },
            "trials": [
                {
                    "seeds": t.seeds,
                    "closure_size": t.closure_size,
                    "emitted_size": t.emitted_size,
                    "tokens": t.tokens,
                    "levels": t.levels,
                    "round_trips": t.round_trips,
                    "ms": t.ms,
                }
                for t in dist.trials
            ],
        }
        args.out.write_text(json.dumps(report, indent=2))
        print(f"wrote {args.out}")

    return 0 if within_an_order_of_magnitude(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
