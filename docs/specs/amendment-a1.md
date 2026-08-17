# Amendment A1 to Implementation Spec v1.3

**Status:** Adopted. Raised by Item 0 spike (`docs/spikes/hydradb-item-0-results.md`), HydraDB `6a2fbb19`.
**Scope:** §5.1, §2.1, §6.3, Appendix A. No architectural change. Invariants I1–I6 unaffected.

The Item 0 spike proved three spec assumptions false against the real engine. All three were verified by reading `src/query/opencypher.rs` and `src/query/path_procedure.rs`, not inferred. This amendment records the decisions.

---

## A1.1 — Frontier reads return topology only; scalars move to an in-memory sidecar

### What was wrong

Spec §5.1's canonical query is rejected at parse time:

```
OpenCypher query is not supported yet: UNWIND batch node patterns do not support labels
```

The `UNWIND … MATCH … RETURN` batch classifier (`unwind_edge_template`, `opencypher.rs:2921-3021`) forbids **any label** on either endpoint, and once labels are removed permits **exactly two projections** (`opencypher.rs:1176-1208`). The only accepted read shape is:

```cypher
UNWIND $rows AS row
  MATCH (x {id: row.v})-[:CALLS]->(y)
  RETURN row.v AS src, y.id AS dst
```

The full spec form works fine as a **plain single-source query**, so this is a restriction on the multi-row batch path, not on the engine.

### Decision — sidecar, not more queries

The three options in the spike report (second property fetch, `UNION` batching, N single-source round trips) all try to get scalars *out of the graph*. Don't. **The graph stores topology; the application stores scalars.**

Every scalar the closure and cost model need is produced by our own ingest pipeline. Load them at startup from the ingest artifact:

```python
# loaded once from symbols.jsonl at process start, keyed by integer node id
class SymbolMeta(NamedTuple):
    fqn: str
    kind: str                  # function | method | class | constant | module | test
    repr_L2_tokens: int
    repr_L3_tokens: int
    repr_L2_refs: tuple[int, ...]
    repr_L3_refs: tuple[int, ...]
    identity_tokens: int
    provenance_tokens: int
    evaluable: bool

SIDECAR: dict[int, SymbolMeta]
```

`expand()` returns `(src, dst)` pairs; every cost, level and refs lookup reads `SIDECAR`.

**Rationale.** This is consistent with §2.1's existing decision to own the FQN→id map application-side, and it is strictly faster than any query-based option: dict lookups instead of round trips. Memory is ~40 MB for a 200k-symbol repository. The `repr_L2_text` / `repr_L3_text` blobs deliberately stay **out** of the sidecar — they are fetched from the graph by single-source query at emission time, for the ~30 symbols actually emitted.

### Corrected §5.1

```python
HARD_EDGES = ['REFERENCES_TYPE','CALLS','OVERRIDES',
              'IMPLEMENTS','DECORATED_BY','READS_CONSTANT']

def expand(frontier: list[NodeId]) -> list[tuple[NodeId, str, NodeId]]:
    rows, out = [{"v": n} for n in frontier], []
    for et in HARD_EDGES:                       # one type per pattern
        q = (f"UNWIND $rows AS row "
             f"MATCH (x {{id: row.v}})-[:{et}]->(y) "   # NO LABELS — engine constraint
             f"RETURN row.v AS src, y.id AS dst")       # EXACTLY two projections
        for r in session.run(q, rows=rows):
            out.append((r["src"], et, r["dst"]))        # edge type known from the loop
    return out
```

Filter non-`Symbol` destinations application-side via `SIDECAR` membership — cheaper than a label predicate the batch path won't accept anyway.

### Cost model — restored, unchanged

**Six typed query templates per productive hop; 12 batched requests for an unchunked two-hop closure; zero property fetches.** Chunk to `B` rows per batch for large frontiers (start `B=500`) per the existing §5.1 note.

### Consequence for evidence reads

The batched form returns no relationship properties, so §6.3 candidate scoring — which reads `hits`, `directness`, `phase`, `trace_revision` — must use the **general single-source form**, where relationship properties project correctly. Candidate sets are small (tens, not thousands), so this is acceptable. Do not attempt to batch evidence reads.

---

## A1.2 — Dual labels require two sequential upsert batches

### What was wrong

Spec §2.1 states HydraDB supports `SET n:Symbol, n:Test`. In one UNWIND vertex-upsert batch it is rejected:

```
OpenCypher query is not supported yet: UNWIND vertex upsert requires exactly one SET label
```

(`opencypher.rs:1488`, `:1549`.)

### Decision — accept, adjust ingest

The spike **verified** (not assumed) that two sequential single-label batches against the same `id` produce a node matched by both `(x:Symbol)` and `(x:Test)`:

```cypher
UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind
UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Test
```

I6's dual-labelling requirement stands. Ingest emits label-`SET` as two passes: one for all `:Symbol` nodes, a second for the `:Test` subset. Mechanical, not architectural.

### Also adopted

Non-batched `MERGE (n {id: …}) SET …` is rejected outright (`"MERGE with following clauses is not executable in Query engine"`). **All vertex upserts go through the UNWIND batch path, even single rows.** Appendix A's framing of `UNWIND` as the workhorse is literal, not stylistic.

Note the asymmetry, and do not let it surprise you later: batched **writes** require exactly *one* label per endpoint; batched **reads** require *zero*.

---

## A1.3 — `MSpaths` selects on `fqn`; `SPpaths`/`SSpaths` take integer ids

### What was wrong

The spec never said which property `MSpaths` selects on. It cannot be `id`.

- `algo.SPpaths` / `algo.SSpaths` take a direct integer `sourceNode` / `targetNode`. Label/property selectors are rejected: `"{option} is only supported by algo.MSpaths"`.
- `algo.MSpaths`'s `sourceValues` / `targetValues` must be **lists of strings**. The INT `id` property fails with `"sourceValues must be a list of strings"`.

### Decision

§6.3's `MSpaths` usage selects on **`fqn`** (`sourceLabel: 'Symbol'`, `sourceProperty: 'fqn'`), which is string-typed and present on every node. The application already holds the id↔fqn map in both directions, so converting a candidate id set to fqn strings is free.

`SPpaths` / `SSpaths` continue to take integer ids directly, consistent with §2.1.

---

## A1.4 — `r.id` is not projectable

`MATCH (x:Symbol {id:…})-[r:CALLS]->(y:Symbol) RETURN r.id` fails with `"unbound variable r"` in every form tried, though `r.resolver`, `r.confidence` and other relationship properties project fine.

**Non-blocking.** §2.1.3 defines `eid()` as a pure function of `(type, src, dst[, revision])`, so edge identity is always re-derivable application-side and never needs reading back. Confirm before Item 9, but no design change.

---

## Amended CI guardrails

Add to `tests/integration/test_hydradb_compat.py`:

1. Pin the **corrected** `expand()` query shape as a passing test.
2. Keep the existing `pytest.raises` on the verbatim v1.3 form — if a HydraDB upgrade starts accepting it, that test fails and flags the amendment for review. (The spike already did this; it was the right call.)
3. Assert the two-pass dual-label write produces a node matching both labels.
4. Assert `MSpaths` with an INT selector property is rejected, so nobody reintroduces it.

---

## Summary of changes

| Spec section | Change |
|---|---|
| §5.1 | Query rewritten: no labels, two projections. Scalars from in-memory sidecar. Cost model unchanged (12 requests / two-hop). |
| §2.1 | Add `SIDECAR` as a first-class component loaded from `symbols.jsonl`. `repr_*_text` stays in the graph. |
| §2.1 (ingest) | Dual labels via two sequential single-label batches. All upserts via UNWIND, even single rows. |
| §6.3 | `MSpaths` selects on `fqn`. Evidence reads use single-source form, not batched. |
| §2.1.3 | `r.id` not readable; `eid()` re-derived application-side. No change. |
| Appendix A | Add: batch reads forbid labels and allow exactly two projections; batch writes require exactly one label per endpoint; vertex upsert requires exactly one `SET` label. |
