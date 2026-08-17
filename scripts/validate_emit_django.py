#!/usr/bin/env python
"""Amendment A4.1 acceptance figures against the real Django graph.

Compiles AND emits 200 (default) seed-set trials -- the same seed filter,
`rng_seed` and 6-seeds-per-trial contract Items 4/5/6 used -- and reports the
`token_margin` distribution (I4: must be `<= 0` on every trial after the A4.1
framing fix) alongside the profile hit rates, so the new numbers are directly
comparable to Item 5/6's 91.5% / 0.5% / 8.0% / 0% / 0% baseline.

    python scripts/validate_emit_django.py --out /tmp/cc-a4.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.budget import is_closed
from context_compiler.graph.client import GraphClient
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
    ap.add_argument("--trials", type=int, default=200)
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
    source = source_from_symbols(symbols)
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
                out = emit(ctx, source, sidecar)
                rows.append(
                    {
                        "seeds": list(seeds),
                        "status": ctx.status,
                        "profile": ctx.profile.name if ctx.profile else None,
                        "budgeted_tokens": out.budgeted_tokens,
                        "emitted_tokens": out.tokens,
                        "token_margin": out.token_margin,
                        "margin_fraction": round(out.margin_fraction, 4),
                        "files": out.files,
                        "order": len(out.order),
                        "is_closed": closed,
                    }
                )
                if i % 20 == 0:
                    print(
                        f"  {i}/{args.trials}  {time.perf_counter() - t0:.0f}s",
                        file=sys.stderr,
                    )
    client.close()

    ok = [r for r in rows if r["status"] != EXCEEDED]
    positive = [r for r in ok if r["token_margin"] > 0]
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
        "token_margin": stat(r["token_margin"] for r in ok),
        "margin_fraction": stat(r["margin_fraction"] for r in ok),
        "positive_margin_count": len(positive),
        "positive_margin_fraction": round(len(positive) / len(ok), 4) if ok else None,
        "all_closed": all(r["is_closed"] is not False for r in rows),
    }

    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "trials": rows}, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
