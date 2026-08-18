#!/usr/bin/env python
"""Capture the deterministic Arm B side-by-side example."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from context_compiler.baseline.arm_b import dangling_references, run_arm_b
from context_compiler.emit import emit
from context_compiler.emit.source import source_from_symbols
from context_compiler.graph.budget import is_closed, mandatory_identities
from context_compiler.graph.client import GraphClient
from context_compiler.graph.closure import closure
from context_compiler.graph.compile import Compiler
from context_compiler.graph.expand import HARD_EDGES, CachingExpander, Expander, ReverseReader
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--measurement", required=True)
    parser.add_argument("--symbols", default="~/out/django/symbols.jsonl")
    parser.add_argument("--edges", default="~/out/django/edges.jsonl")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    measurement = json.loads(Path(args.measurement).read_text())
    symbols = Path(args.symbols).expanduser()
    edges = Path(args.edges).expanduser()
    sidecar = load_sidecar(symbols)
    degrees, in_degrees = load_degree_tables(edges, tuple(HARD_EDGES))
    source = source_from_symbols(symbols)
    budget = measurement["configuration"]["budget"]
    target = 36

    client = GraphClient()
    client.verify()
    closure_rows = []
    with Expander(client, membership=sidecar) as expander:
        for index, row in enumerate(measurement["compiler_rows"]):
            result = closure({seed: 3 for seed in row["seeds"]}, expander)
            closure_rows.append((abs(len(result.levels) - target), index, len(result.levels)))
    _, selected_index, closure_size = min(closure_rows, key=lambda item: (item[0], item[1]))
    selected = measurement["compiler_rows"][selected_index]

    with Expander(client, membership=sidecar) as expander:
        with ReverseReader(client, membership=sidecar) as reverse:
            compiler = Compiler(
                sidecar=sidecar,
                expander=expander,
                reverse=reverse,
                degrees=degrees,
                in_degrees=in_degrees,
            )
            compiler_context = compiler.compile_context(selected["seeds"], budget)
            compiler_output = emit(compiler_context, source, sidecar)
            arm_b_context = run_arm_b(
                list(selected["seeds"]),
                sidecar,
                expander,
                degrees,
                reverse=reverse,
                budget=budget,
            )
            arm_b_output = emit(arm_b_context, source, sidecar)
            arm_b_closed = is_closed(
                arm_b_context.levels, CachingExpander(expander)
            )

    compiler_dangling = mandatory_identities(compiler_context.levels, sidecar)
    arm_b_dangling = dangling_references(arm_b_context, sidecar)
    def names(nodes):
        return [sidecar[node].fqn for node in sorted(nodes)]

    text = f"""# Item 10a — Arm B side-by-side example

Selection rule fixed before inspecting trial outputs: choose the trial whose
compiler closure size is closest to the post-A6 median **36**; break an exact
tie by the first trial index. Selected trial: **{selected['trial']}** (one-based),
compiler closure size **{closure_size}**.

Seeds (identical for both arms):

```text
{chr(10).join(map(str, selected['seeds']))}
```

## Comparison

| measure | compiler | Arm B |
|---|---:|---:|
| status | {compiler_context.status} | {arm_b_context.status} |
| emitted symbols | {len(compiler_output.order)} | {len(arm_b_output.order)} |
| emitted tokens | {compiler_output.tokens} | {arm_b_output.tokens} |
| budgeted tokens | {compiler_context.total_tokens()} | {arm_b_context.total_tokens()} |
| utilisation | {compiler_context.utilisation():.4f} | {arm_b_context.utilisation():.4f} |
| identity-only references | {len(compiler_dangling)} | {len(arm_b_dangling)} |
| Arm B `is_closed()` | — | {arm_b_closed} |

The compiler's mandatory closure resolves references by admitting declarations
or bodies and, where necessary, identity lines. Arm B admits only its ranked
one-hop neighbourhood at L2; it does not run the closure fixpoint or propagate
levels. Its identity-only reference set is therefore a set of names without an
emitted declaration/body, and the independent `is_closed()` check is expected
to fail. The emitter is shared, so the difference below is the retrieval arm,
not a formatter fork.

Compiler identity-only references ({len(compiler_dangling)}):

```text
{chr(10).join(names(compiler_dangling)) or '(none)'}
```

Arm B identity-only references ({len(arm_b_dangling)}):

```text
{chr(10).join(names(arm_b_dangling)) or '(none)'}
```

## Compiler output

```python
{compiler_output.text.rstrip()}
```

## Arm B output

```python
{arm_b_output.text.rstrip()}
```
"""
    Path(args.out).write_text(text)
    print(f"selected trial {selected['trial']} closure {closure_size}; wrote {args.out}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
