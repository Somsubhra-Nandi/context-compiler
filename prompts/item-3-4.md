# Task: Items 3–4 — Ingest and Closure Fixpoint

You are the graph engineer for **Context Compiler**, a Hack Hydra Track 2B project. You built Item 0. Another agent built Items 1–2 (extraction) and owns that code. You will not touch theirs; they will not touch yours.

**This is the critical path. Items 5, 6 and 7 all wait on Item 4.**

---

## 0. Source of truth

Read completely before anything else:

```
docs/specs/context-compiler-v1.3.md
docs/specs/amendment-a1.md            ← supersedes §5.1, read carefully
docs/specs/jsonl-contract.md          ← the input format, frozen
docs/spikes/hydradb-item-0-results.md ← your own findings
docs/spikes/scip-item-1-results.md    ← what the extractor actually produces
```

Governing sections: **§0 invariants I1–I4**, **§2.1** schema, **§2.1.3** relationship identity, **§4** propagation table, **§5** fixpoint, **Appendix A** Cypher constraints, and **Amendment A1** in full.

**Amendment A1 changed §5.1.** The spec's verbatim frontier query does not work. Implement the amended version, not the original.

Do not redesign. If running software proves a spec assumption false, document the exact observed behaviour and stop at the smallest boundary. That is how Item 0 went and it worked.

---

## 1. Scope boundary

### You own
```
src/context_compiler/graph/
tests/graph/
scripts/
docs/spikes/graph-item-3-4-results.md
```

### You must not touch
```
src/context_compiler/extract/    # other agent
tests/unit/                      # other agent
docs/specs/                      # frozen
~/hydradb                        # not yours
~/targets/                       # read-only inputs
```

### You must not implement
Budget admission or profiles (Item 5), emission (Item 6), MCP server (Item 7), seed resolution (Item 8), runtime tracing (Item 9), evaluation (Item 10). **Stop at a working `closure()`.**

---

## 2. Environment

Ubuntu 26.04 under WSL2, 10 GB RAM cap. `source .venv/bin/activate` before Python — PEP 668 refuses bare `pip`. Work only under `~/`, never `/mnt/c/`. No sudo.

Start HydraDB with `bash scripts/run_hydradb.sh start`; verify with `status`. Every shell that starts it needs `RUST_MIN_STACK=33554432` — without it the node answers `/readyz` then aborts on the first query.

### Real input data is already available

```
~/out/django/symbols.jsonl     43,420 symbols
~/out/django/edges.jsonl      123,907 edges (116,758 mandatory + 7,149 INHERITS_FROM)
```

Edge type breakdown: `CALLS` 95,288 · `IMPLEMENTS` 7,191 · `REFERENCES_TYPE` 7,150 · `DECORATED_BY` 4,317 · `OVERRIDES` 1,992 · `READS_CONSTANT` 820.

Smaller sets for fast iteration: regenerate with the extractor against `~/targets/requests` (760 symbols) or `~/targets/flask` (992 symbols). Requests is degenerate (avg out-degree 1.02) and useless for closure testing — **use Django for anything meaningful.**

---

## 3. Item 3 — Ingest

Load the JSONL pair into HydraDB. Everything below was verified in Item 0; do not rediscover it.

### Node upsert — UNWIND only, one label per SET

```cypher
UNWIND $rows AS row
  MERGE (n {id: row.v})
  SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind,
      n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3
```

Non-batched `MERGE … SET …` is **rejected outright** (`"MERGE with following clauses is not executable in Query engine"`). All upserts go through UNWIND, even single rows.

**Dual labels require two sequential passes** (Amendment A1.2). One pass sets `:Symbol` on everything; a second sets `:Test` on the `kind == "test"` subset. `SET n:Symbol, n:Test` in one batch is rejected.

### Edge upsert — one type per batch, exactly one label per endpoint

```cypher
UNWIND $rows AS row
  MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst})
  MERGE (s)-[r:CALLS {id: row.eid}]->(d)
  SET r.resolver = row.resolver, r.confidence = row.conf
```

`eid` is **not** in the JSONL. Derive it per §2.1.3:
```python
def eid(edge_type: str, src: int, dst: int) -> int:
    key = f"{edge_type}|{src}|{dst}".encode()
    return int.from_bytes(blake2b(key, digest_size=8).digest(), 'big') >> 1
```
Static edges are idempotent under re-ingest — verify this, don't assume it.

Note the asymmetry that bit us in Item 0: batched **writes** need exactly one label per endpoint; batched **reads** need zero.

### Chunking

Start at `B = 500` rows per batch. HydraDB enforces result, deadline and memory bounds. Tune empirically and **record the value you settled on and why**.

### Contingency — read this before you start

Each node carries `repr_L2_text` and `repr_L3_text`. Across 43,420 Django symbols that is on the order of 100 MB of property data over Bolt, and it may be slow or hit engine limits.

**Measure ingest time with and without the text blobs.** If including them is prohibitive, the fallback is: keep graph nodes lean (ids, fqn, kind, token counts) and serve `repr_*_text` at emission time from an on-disk offset index into `symbols.jsonl`. That is a legitimate variation of A1.1's sidecar principle — graph holds topology, application holds bulk. **Report the numbers and your recommendation; do not decide unilaterally.**

### Idempotency

Re-running ingest on an already-populated graph must not duplicate nodes or edges. Test it.

---

## 4. Item 4 — Sidecar, expand, fixpoint

### 4.1 Sidecar (Amendment A1.1)

`symbols.jsonl` is now the **runtime cost table**, not just an ingest artifact. Load it once at startup, keyed by integer id:

```python
class SymbolMeta(NamedTuple):
    fqn: str; kind: str
    repr_L2_tokens: int; repr_L3_tokens: int
    repr_L2_refs: tuple[int, ...]; repr_L3_refs: tuple[int, ...]
    identity_tokens: int; provenance_tokens: int
    evaluable: bool | None

SIDECAR: dict[int, SymbolMeta]
```

**Exclude `repr_L2_text` / `repr_L3_text`** — bulk text stays out of memory. Every cost, kind and refs lookup reads `SIDECAR`; none of them hit the graph. Report actual memory footprint for Django.

### 4.2 `expand()` — the amended form

```python
HARD_EDGES = ['REFERENCES_TYPE','CALLS','OVERRIDES',
              'IMPLEMENTS','DECORATED_BY','READS_CONSTANT']

def expand(frontier: list[int]) -> list[tuple[int, str, int]]:
    rows, out = [{"v": n} for n in frontier], []
    for et in HARD_EDGES:
        q = (f"UNWIND $rows AS row "
             f"MATCH (x {{id: row.v}})-[:{et}]->(y) "      # NO LABELS
             f"RETURN row.v AS src, y.id AS dst")          # EXACTLY two projections
        for r in session.run(q, rows=rows):
            out.append((r["src"], et, r["dst"]))
    return out
```

Labels are forbidden and exactly two projections are permitted — this is the engine's UNWIND batch classifier, verified in Item 0. Edge type comes from the loop variable, not a projection. Filter non-`Symbol` destinations via `SIDECAR` membership.

**Cost target: 6 typed queries per hop, 12 for a two-hop closure, zero property fetches.** Chunk large frontiers.

### 4.3 Propagation table (§4) — strictly decreasing, no exceptions

| Source | Edge | Target |
|---|---|---|
| L3 | `REFERENCES_TYPE` | L2 |
| L3 | `CALLS` | L2 |
| L3 | `OVERRIDES` | L2 |
| L3 | `IMPLEMENTS` | L2 |
| L3 | `DECORATED_BY` | L2 |
| L3 | `READS_CONSTANT` | L2 |
| L2 | *(any mandatory)* | L1 |
| L1 | *(any)* | L0 — terminal |

`INHERITS_FROM` is **not** here. It was consumed by MRO flattening at ingest (§3.2). Do not traverse it.

This table is derived from Python semantics, not tuned. Do not adjust it.

### 4.4 Fixpoint (§5)

```python
def closure(seeds: dict[int, Level], profile=None) -> dict[int, Level]:
    level = dict(seeds)
    frontier = [n for n, lv in seeds.items() if lv > L1]
    for _hop in range(2):                    # structural bound, not a cutoff
        if not frontier: break
        next_frontier = []
        for src, edge_type, dst in expand(frontier):
            required = PROPAGATION[edge_type][level[src]]
            if required > level.get(dst, L0):
                level[dst] = required        # levels only ever rise
                provenance[dst].append(Reason(via=src, edge=edge_type,
                                              rule=f"{edge_type}({level[src]})->{required}"))
                if required > L1:
                    next_frontier.append(dst)
        frontier = next_frontier
    return level
```

Provenance is **not optional** — every non-seed entry records `(via, edge, rule)`. §7.4 and `explain_inclusion` depend on it, and it is the demo.

Do not implement profiles. Accept the parameter, ignore it, leave a TODO for Item 5.

---

## 5. Required tests

### `tests/graph/test_closure_fixtures.py` — no HydraDB, fast
Hand-built graphs with an exact expected level map:
- linear chain A→B→C→D: A at L3 gives B=L2, C=L1, D absent
- diamond: converging paths take the **maximum** level, not the first assigned
- cycle A→B→A: terminates, no infinite loop
- multi-seed: two L3 seeds with overlapping neighbourhoods merge correctly
- L1 is terminal: nothing propagates from it
- provenance recorded for every non-seed entry

Stub `expand()` so these run without a database. **This suite must pass before you touch real data.**

### `tests/graph/test_ingest.py` — HydraDB required
- 43,420 nodes and 116,758 mandatory edges land and read back
- dual-label: a `kind == "test"` node matches both `(x:Symbol)` and `(x:Test)`
- re-ingest is idempotent — no duplicates
- `eid` derivation is stable across runs

### `tests/graph/test_closure_django.py` — HydraDB required
Cross-validation against a known-good simulation. Sampling 200 sets of **6 seeds** from library functions (`kind ∈ {function, method}`, not under `tests/`, `repr_L3_tokens >= 150`) produced:

```
closure size    median 47    p90 83    max 150
L3+L2 tokens    median 3308  p90 6797  max 20272
over 8000 tokens: 10/200
```

Reproduce this with the real fixpoint and the same seed selection. **Median closure size should land near 47 and median cost near 3,308.** Order-of-magnitude agreement is the pass condition; exact match is not expected since the simulation ignored level-merging on converging paths.

**If your numbers are wildly different, something is wrong — investigate before declaring Item 4 done.** This is the single most valuable check in the task: an independent prediction of the answer, made before the code existed.

---

## 6. Deliverables

```
src/context_compiler/graph/__init__.py
src/context_compiler/graph/client.py       # Bolt session, chunking, retry
src/context_compiler/graph/ingest.py       # JSONL -> HydraDB, CLI
src/context_compiler/graph/sidecar.py      # in-memory scalar table
src/context_compiler/graph/expand.py       # amended A1.1 frontier read
src/context_compiler/graph/closure.py      # propagation table + fixpoint
tests/graph/…
docs/spikes/graph-item-3-4-results.md
```

CLI:
```bash
python -m context_compiler.graph.ingest --symbols ~/out/django/symbols.jsonl \
                                        --edges   ~/out/django/edges.jsonl
```

Results doc must record: ingest wall time and chosen batch size; with/without text-blob comparison and your recommendation; sidecar memory footprint; `expand()` round-trip count and latency per hop; the Django closure distribution versus the predicted table above; and any spec or amendment discrepancy.

---

## 7. Time boxes

- Item 3 ingest working end to end: **3 hours**. Overrun → report and stop.
- Fixture tests green: **1 hour** after ingest.
- Django cross-validation: **1 hour** after that.

Report at each boundary, not only at the end. Commit after Item 3 passes and again after Item 4 — do not leave hours of work uncommitted.

---

## 8. Acceptance gate

Not complete because code was written. Before finishing:

1. HydraDB running; full Django graph ingested and counts verified by read-back
2. Re-ingest proven idempotent
3. Dual-label proven on a real `kind == "test"` node
4. All fixture tests green
5. Real fixpoint reproduces the predicted Django distribution within an order of magnitude
6. Provenance present on every non-seed closure entry
7. `expand()` round-trip count measured and reported
8. Every discrepancy from spec v1.3 or Amendment A1 documented

Do not proceed to Item 5.

---

## 9. Final report

Implementation summary · commands to reproduce · test output · files changed · performance table (ingest, sidecar, expand, closure) · Django distribution vs prediction · unresolved issues · recommended commit message.
