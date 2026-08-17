# Amendment A2 to Implementation Spec v1.3

**Status:** Adopted. Raised by Items 3–4 (`docs/spikes/graph-item-3-4-results.md`).
**Scope:** A1.1, §2.1, §3.1, §9.3, test infrastructure. No architectural change. Invariants I1–I6 unaffected.

Five new engine constraints and one architectural decision, all proven by measurement rather than inferred. Two of the constraints would have broken the *default* ingest path.

---

## A2.1 — Bulk text is served from an offset index, not from the graph

A1.1 said `repr_L2_text` / `repr_L3_text` "are fetched from the graph by single-source query at emission time." **That plan is withdrawn.** Two measurements kill it.

**164 Django symbols cannot store their text at all.** String properties cap just under 32 KiB (32,743 B accepted, 32,744 rejected, with the key counting against the budget). `repr_L3_text` exceeds it for 163 symbols — `tests.admin_views.tests` needs 347 KB. The only alternatives are truncation, which silently falsifies I4, or chunking blobs across numbered properties: real complexity in both the write and read paths, for 0.4% of symbols.

**Graph-resident text slows the closure's own hot path by 56–82%.** Measured on the label-free A1.1 batch form — the exact shape `expand()` runs — over the same 43,420 node ids: 653–762 s against lean nodes, 1,188 s against fat ones. Same topology, same edge count, only node payload differs. The engine pays for the node record whether or not the query reads its text. Edge writes ran 12–21% slower for the same reason, since `MERGE` must `MATCH` labelled endpoints.

### Decision

**The graph holds topology. The application holds bulk.**

```
symbols.jsonl  --ingest--> graph:   ids, fqn, kind, token counts, refs   (lean)
symbols.jsonl  --offset--> emission: repr_L2_text, repr_L3_text          (seek)
```

`--offset-index` writes `{id: [byte_offset, length]}` during the pass that already reads every line; `sidecar.read_repr_text()` seeks and returns the record. Emission fetches text for the ~22 symbols actually emitted, so a seek per symbol is cheaper than the round trip A1.1 already budgeted, and it has **no size ceiling**.

`--text` remains in the ingest CLI so the decision can be revisited. **Default off.** This is A1.1's sidecar principle applied one level further, now validated by a controlled experiment rather than asserted.

---

## A2.2 — Constant folding caps the folded value at 4 KB

One Django constant (`tests.validators.tests.INVALID_URLS`) folds to 66,517 bytes and **broke the default no-text ingest** — this is not a text-blob problem that `--text` avoids.

The engine limit is incidental. **The real reason to cap is that a 66 KB folded constant is bad context.** The model does not need 2,000 invalid URLs; it needs to know the constant is a list of invalid URLs. Emitting the literal wastes budget the packer could spend on a caller.

### Decision — extraction layer (§3.1)

```
folded value ≤ 4,096 bytes   → evaluable: true,  static_value: "<literal>"
folded value >  4,096 bytes  → evaluable: true,  static_value: null,
                               static_value_bytes: <int>
```

`evaluable` stays **true** — the constant *is* statically evaluable, we are declining to inline it. `repr_L2_text` shows the defining expression rather than the value, and notes the size. Never truncate: a clipped constant looks valid and is wrong.

Ingest already skips over-cap string values and reports them; that stays as defence in depth.

---

## A2.3 — All count assertions use the batched form

Whole-graph relationship counts do not survive the engine's 29,999 ms deadline at Django scale, and **labels are what cost** — the same asymmetry A1.1 found in batch reads:

| Query | Edges | Result |
|---|---:|---|
| `MATCH (a)-[:READS_CONSTANT]->(b) RETURN count(*)` | 820 | 2.29 s |
| `MATCH (a:Symbol)-[:READS_CONSTANT]->(b:Symbol) RETURN count(*)` | 820 | **deadline exceeded** |
| `MATCH (a)-[:CALLS]->(b) RETURN count(*)` | 95,288 | **deadline exceeded** |
| `MATCH (n:Symbol) RETURN count(*)` | 43,420 nodes | 17.11 s |

Unlabelled scans run ~400–500 edges/s, so the wall arrives near 13,000 edges. A labelled node count is already at 17 s of a 30 s budget.

**Decision:** counts go through the chunked, label-free A1.1 batch form, summing out-degree over all node ids. Every edge's source is an emitted symbol by the JSONL contract, so this is complete. It also exercises the same query path `expand()` uses in production rather than a separate one.

**This binds §9.3.** Any soundness check written as a whole-graph count will time out on repositories this size. Write them against the batched form from the start.

Also cosmetic, for Appendix A: `count(n)` is rejected; `count(*)` is accepted.

---

## A2.4 — Batch size is bounded by bytes as well as rows

Bolt rejects any request over 2 MiB, and **resets the connection on the way to reporting it**, so the driver surfaces a cascade of `ConnectionResetError` / `ServiceUnavailable` before the real diagnosis appears. A retry policy treating `ServiceUnavailable` as transient will loop on a defunct session instead of failing fast.

§5.1's `B` bounds row *count*; the limit is on serialised *bytes*. Scalar rows are ~200 B so `B=500` is nowhere near the ceiling, but 250 rows carrying `repr_*_text` exceed 2 MiB.

**Decision:** every batch is capped by row count **and** estimated payload, budget 1.5 MiB. A single row over budget is still sent alone — the caller cannot split it, and the engine's rejection is a clearer signal than a silent drop.

**Appendix A addition:** *batch size is bounded by serialised message bytes (2 MiB), not only by row count.*

---

## A2.5 — Null parameters are rejected; absent means null

```
invalid parameter $rows: only boolean, signed integer, finite float,
and string parameters are supported
```

A **parameter-layer** rejection, so it cannot be worked around by writing `null` differently — the row never reaches the engine. Affects 41,762 of 43,420 Django symbols (96%).

**Decision:** nullable properties each get their own `UNWIND` pass over their non-null subset. A null is expressed as an **absent property**, not a present-and-null one. §2.1's schema should be read that way. This composes with the A1.2 dual-label pass structure.

---

## A2.6 — Test isolation

Item 0's `tests/integration/test_hydradb_compat.py` writes fixtures into the same `default` graph, leaving 12 `:Symbol` and 1 `:Test` node behind. Full-suite runs are green today only because pytest happens to collect `tests/graph` first.

**Decision:** the compat suite gets its own `GRAPH_ID`. Ten-minute fix, and Item 10 will run suites in arbitrary order in CI.

---

## Not changed

- **Sidecar scaling.** 20.0 MB at 43,420 symbols extrapolates to ~92 MB at 200k, against A1.1's ~40 MB estimate. Same order; accepted.
- **`r.id` unreadable** (A1.4). Still true. `eid()` is a pure function re-derived application-side. No action; Item 9 re-checks.

---

## Summary

| Section | Change |
|---|---|
| A1.1 | Emission-time graph text fetch **withdrawn**; offset index adopted. `--text` default off. |
| §3.1 | Folded constants capped at 4 KB; over-cap keeps `evaluable: true`, `static_value: null`. |
| §9.3 | All counts via the batched label-free form. |
| §5.1 | Batches bounded by rows **and** 1.5 MiB payload. |
| §2.1 | Nullable properties are absent, not null. Written in per-field passes. |
| tests | Compat suite gets its own `GRAPH_ID`. |
| Appendix A | Add: 2 MiB message cap; 32 KiB string property cap; `count(n)` rejected, `count(*)` accepted; null parameters rejected. |
