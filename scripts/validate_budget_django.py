#!/usr/bin/env python
"""Item 5 acceptance figures against the real Django graph.

Runs the Sec 6.2 admission scan over the same 200 x 6 seed sets Item 4 used
(same filter, same `rng_seed`), and reports every distribution the results doc
quotes: profile hit rates, token utilisation, round trips per compile,
candidate pool sizes, before/after context size from packing, and the
L1-mandatory cost distribution.

    python scripts/validate_budget_django.py --out /tmp/cc-item5.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from context_compiler.graph.budget import cost, is_closed, mandatory_identities
from context_compiler.graph.client import GraphClient
from context_compiler.graph.closure import L1, L2, L3, closure
from context_compiler.graph.compile import EXCEEDED, OK, Compiler
from context_compiler.graph.expand import HARD_EDGES, CachingExpander, Expander, ReverseReader
from context_compiler.graph.profiles import PROFILES
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar
from context_compiler.graph.validate import PREDICTION, eligible_seeds, sample_seed_sets


def stat(values) -> dict:
    vals = sorted(values)
    if not vals:
        return {}
    n = len(vals)
    return {
        "min": vals[0],
        "median": round(statistics.median(vals), 2),
        "mean": round(statistics.mean(vals), 2),
        "p90": vals[min(n - 1, int(n * 0.9))],
        "max": vals[-1],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    ap.add_argument("--edges", default="~/out/django/edges.jsonl")
    ap.add_argument("--trials", type=int, default=PREDICTION["trials"])
    ap.add_argument("--seeds", type=int, default=PREDICTION["seeds_per_trial"])
    ap.add_argument("--budget", type=int, default=8000)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--verify-closure",
        action="store_true",
        help="re-read every emitted node's edges to check I6 independently",
    )
    args = ap.parse_args()

    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()

    t0 = time.perf_counter()
    sidecar = load_sidecar(symbols)
    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    print(
        f"sidecar {len(sidecar):,} symbols, degrees {len(degrees):,} sources, "
        f"{time.perf_counter() - t0:.2f}s",
        file=sys.stderr,
    )

    pool = eligible_seeds(symbols)
    sets = sample_seed_sets(pool, args.trials, args.seeds)
    print(f"seed pool {len(pool):,}, {args.trials} trials", file=sys.stderr)

    client = GraphClient()
    client.verify()
    rows = []
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
            for i, seeds in enumerate(sets, 1):
                ctx = compiler.compile_context(seeds, args.budget)
                closed = None
                if args.verify_closure and ctx.ok:
                    fresh = CachingExpander(expander)
                    closed = is_closed(ctx.levels, fresh, ctx.profile)
                mand = mandatory_identities(ctx.levels, sidecar)
                by_level = {lv.name: 0 for lv in (L1, L2, L3)}
                for lv in ctx.levels.values():
                    by_level[lv.name] = by_level.get(lv.name, 0) + 1
                rows.append(
                    {
                        "seeds": list(seeds),
                        "status": ctx.status,
                        "profile": ctx.profile.name if ctx.profile else None,
                        "budget": ctx.budget,
                        "cost": ctx.cost,
                        "hint_tokens": ctx.hint_tokens,
                        "total_tokens": ctx.total_tokens(),
                        "utilisation": round(ctx.utilisation(), 4),
                        "deficit": ctx.deficit,
                        "floor_cost": ctx.stats.floor_cost,
                        "floor_symbols": ctx.stats.floor_symbols,
                        "floor_emitted": ctx.stats.floor_emitted,
                        "final_symbols": len(ctx.levels),
                        "final_emitted": len(ctx.emitted()),
                        "levels": by_level,
                        "candidates": ctx.stats.candidates,
                        "hubs_skipped": ctx.stats.hubs_skipped,
                        "admitted": ctx.stats.admitted,
                        "bundles_evaluated": ctx.stats.bundles_evaluated,
                        "mandatory_identities": len(mand),
                        "mandatory_identity_tokens": sum(
                            sidecar[n].identity_tokens for n in mand
                        ),
                        "hints": len(ctx.hints) if ctx.hints else 0,
                        "hints_truncated": bool(ctx.hints and ctx.hints.truncated),
                        "round_trips": ctx.stats.round_trips,
                        "closure_round_trips": ctx.stats.closure_round_trips,
                        "discovery_round_trips": ctx.stats.discovery_round_trips,
                        "envelope_round_trips": ctx.stats.envelope_round_trips,
                        "ms": round(ctx.stats.seconds * 1000, 2),
                        "is_closed": closed,
                        "bundle_sizes": [
                            a.bundle_size for a in (ctx.pack_report.admitted if ctx.pack_report else [])
                        ],
                        "bundle_costs": [
                            a.delta_cost for a in (ctx.pack_report.admitted if ctx.pack_report else [])
                        ],
                    }
                )
                if i % 20 == 0:
                    print(
                        f"  {i}/{args.trials}  {time.perf_counter() - t0:.0f}s",
                        file=sys.stderr,
                    )
    client.close()

    ok = [r for r in rows if r["status"] != EXCEEDED]
    packed = [r for r in ok if r["admitted"]]
    summary = {
        "trials": len(rows),
        "budget": args.budget,
        "seconds": round(time.perf_counter() - t0, 2),
        "status": {
            "OK": sum(1 for r in rows if r["status"] == OK),
            "demoted": sum(1 for r in rows if r["status"].startswith("DEMOTED")),
            "exceeded": sum(1 for r in rows if r["status"] == EXCEEDED),
        },
        "profile": {
            p.name: sum(1 for r in rows if r["profile"] == p.name) for p in PROFILES
        },
        "floor_symbols": stat(r["floor_symbols"] for r in ok),
        "final_symbols": stat(r["final_symbols"] for r in ok),
        "floor_emitted": stat(r["floor_emitted"] for r in ok),
        "final_emitted": stat(r["final_emitted"] for r in ok),
        "floor_cost": stat(r["floor_cost"] for r in ok),
        "total_tokens": stat(r["total_tokens"] for r in ok),
        "utilisation": stat(r["utilisation"] for r in ok),
        "candidates": stat(r["candidates"] for r in ok),
        "hubs_skipped": sum(r["hubs_skipped"] for r in rows),
        "admitted": stat(r["admitted"] for r in ok),
        "bundles_evaluated": stat(r["bundles_evaluated"] for r in ok),
        "bundle_size": stat(b for r in packed for b in r["bundle_sizes"]),
        "bundle_cost": stat(b for r in packed for b in r["bundle_costs"]),
        "mandatory_identities": stat(r["mandatory_identities"] for r in ok),
        "mandatory_identity_tokens": stat(r["mandatory_identity_tokens"] for r in ok),
        "hint_tokens": stat(r["hint_tokens"] for r in ok),
        "hints_truncated": sum(1 for r in ok if r["hints_truncated"]),
        "round_trips": stat(r["round_trips"] for r in rows),
        "ms": stat(r["ms"] for r in rows),
        "all_closed": all(r["is_closed"] is not False for r in rows),
        "all_within_budget": all(r["total_tokens"] <= r["budget"] for r in ok),
        "growth_symbols": round(
            statistics.median(r["final_symbols"] for r in ok)
            / statistics.median(r["floor_symbols"] for r in ok),
            2,
        )
        if ok
        else None,
        "growth_emitted": round(
            statistics.median(r["final_emitted"] for r in ok)
            / statistics.median(r["floor_emitted"] for r in ok),
            2,
        )
        if ok
        else None,
    }

    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "trials": rows}, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
