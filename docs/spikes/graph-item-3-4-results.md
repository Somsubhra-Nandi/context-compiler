# Items 3–4 — Ingest and Closure Fixpoint: Results

HydraDB `0.1.0`, commit `6a2fbb192f37f51a93690a2ae2d2f5e27e6e4219`, the same
build Item 0 characterised. Python 3.14.4, `neo4j` 6.2.0, Ubuntu 26.04 under
WSL2 with a 10 GB cap. Input is the frozen Django extraction artifact:
43,420 symbols and 123,907 edges from `~/out/django/`.

```
Item 3  ingest             PASS   43,420 nodes / 116,758 mandatory edges, verified by read-back
Item 3  idempotency        PASS   re-ingest over the populated graph changes no count
Item 3  dual labelling     PASS   a real kind == "test" node matches (x:Symbol) and (x:Test)
Item 4  fixture suite      PASS   33 tests, no database
Item 4  Django fixpoint    PASS   median closure 46 vs predicted 47; median cost 3,026 vs 3,308
Item 4  expand() cost      PASS   exactly 12 round trips per two-hop closure, zero property fetches
Item 4  provenance         PASS   complete on every non-seed entry across all 200 trials
```

Nothing in the spec's architecture had to change. Amendment A1's corrected
`expand()` works exactly as written. Five **new** engine constraints were found
beyond A1, all narrow and mechanical; they are recorded in §6 with the observed
behaviour that proves each one. Two of them (§6.1 null parameters, §6.2 the
32 KiB string cap) would have broken ingest on the *default* path, not just on
an optional one.

---

## 1. Reproduction

```bash
cd ~/context-compiler
source .venv/bin/activate

bash scripts/run_hydradb.sh reset          # stop, wipe the dev store, start clean

python -m context_compiler.graph.ingest \
    --symbols ~/out/django/symbols.jsonl \
    --edges   ~/out/django/edges.jsonl

python scripts/validate_closure_django.py --out /tmp/cc-django-closure.json

python -m pytest tests/graph -q                       # fast suites
CC_TRIALS=200   python -m pytest tests/graph/test_closure_django.py -q
CC_FULL_VERIFY=1 python -m pytest tests/graph/test_ingest.py -q   # ~11 min read-back
```

`reset` is new in `scripts/run_hydradb.sh`. It stops the node, waits for full
exit (the Item 0 lease/compactor race), wipes `~/.local/state/hydradb-dev`, and
starts clean. It exists because leftover spike fixtures otherwise inflate the
read-back counts an acceptance gate is trying to assert exactly.

---

## 2. Item 3 — ingest

### 2.1 Pass structure, and why it is not a choice

Six passes for nodes, one per relationship type for edges. Each pass is a
single `UNWIND` batch template. The shape is forced by the engine:

| Pass | Rows | Forced by |
|---|---:|---|
| `SET n:Symbol` + scalar properties | 43,420 | A1.2: all upserts go through `UNWIND` |
| `SET n:Test` | 21,966 | A1.2: exactly one `SET` label per batch |
| `SET n.evaluable` | 1,658 | §6.1: `null` is rejected as a parameter |
| `SET n.static_value` | 1,437 | §6.1, and §6.2: one value is over the size cap |
| `SET n.repr_*_text/_refs` (optional) | 43,420 | `--text`, off by default — see §3 |
| one `MERGE` per relationship type | 123,907 | Appendix A: one type per pattern |

Over half of Django's symbols (21,966 of 43,420) are `kind == "test"`, so the
dual-label pass A1.2 requires is not a rounding error — it is a second write
over half the graph, and it costs 9–16 s of the total.

### 2.2 Wall time and batch size

Clean-store load, then a full re-ingest over the populated graph:

| Pass | Rows | Requests | First load | Re-ingest |
|---|---:|---:|---:|---:|
| nodes `:Symbol` | 43,420 | 87 | 15.23 s | 20.75 s |
| nodes `:Test` | 21,966 | 44 | 16.15 s | 9.00 s |
| nodes `evaluable` | 1,658 | 4 | 1.47 s | 0.72 s |
| nodes `static_value` | 1,437 | 3 | 0.52 s | 0.72 s |
| edges `REFERENCES_TYPE` | 7,150 | 15 | 6.09 s | 10.15 s |
| edges `CALLS` | 95,288 | 191 | 155.84 s | 128.33 s |
| edges `OVERRIDES` | 1,992 | 4 | 4.64 s | 3.06 s |
| edges `IMPLEMENTS` | 7,191 | 15 | 15.47 s | 10.60 s |
| edges `DECORATED_BY` | 4,317 | 9 | 10.26 s | 6.01 s |
| edges `READS_CONSTANT` | 820 | 2 | 1.92 s | 1.23 s |
| edges `INHERITS_FROM` | 7,149 | 15 | 18.30 s | 9.71 s |
| **total** | | **389** | **247.14 s** | **201.61 s** |

Peak client RSS 133 MB. Edge writes dominate: `CALLS` alone is 63 % of the wall
time and 78 % of the edges.

**Batch size: settled on `B = 500` rows *and* a 1.5 MiB payload budget.**

A row-count sweep at 5,000 symbols found the count barely matters — 8.22 s at
`B=250`, 8.29 s at `B=500`, 8.12 s at `B=1000`. Request count falls 4× across
that range and wall time moves under 2 %, so this engine's batch path is
throughput-bound on rows, not round-trip-bound. `B=500` is kept because it is
the documented default, it matches the frontier-chunking constant in §5.1, and
nothing measured argues for moving it. Raising it buys nothing and enlarges the
blast radius of a failed batch.

The second half of that setting is not optional and is not in the spec: Bolt
rejects any request over 2 MiB (§6.4), and a row count cannot bound bytes. Every
batch is therefore capped by both. For scalar rows the byte budget never binds
(500 Django rows are ~100 KB); for the `--text` pass it binds constantly.

### 2.3 Idempotency — verified, not assumed

The table above *is* the idempotency test: the second run is a full re-ingest of
all 43,420 symbols and 123,907 edges over the already-populated graph. Every
read-back count is unchanged, so `MERGE (n {id: …})` and
`MERGE (s)-[r:TYPE {id: eid}]->(d)` both matched rather than created.

`eid()` is a pure function of `(type, src, dst)` per §2.1.3, so edge identity is
reproducible without ever reading it back — which matters, because A1.4 found
`r.id` is not projectable. `tests/graph/test_ingest.py` pins the exact digest,
checks separation across type/src/dst, checks revision scoping for I5's runtime
edges, and re-derives ids in a fresh subprocess to rule out per-process salting.

`test_reingesting_a_slice_creates_no_duplicates` re-runs the real upsert queries
over real Django rows against the live graph and asserts both the node count and
the per-type out-degree of a probe node are unchanged.

### 2.4 Read-back counts

| | Expected | Read back |
|---|---:|---:|
| `:Symbol` | 43,420 | **43,420** |
| `:Test` | 21,966 | **21,966** |
| `REFERENCES_TYPE` | 7,150 | **7,150** |
| `CALLS` | 95,288 | **95,288** |
| `OVERRIDES` | 1,992 | **1,992** |
| `IMPLEMENTS` | 7,191 | **7,191** |
| `DECORATED_BY` | 4,317 | **4,317** |
| `READS_CONSTANT` | 820 | **820** |
| mandatory total | 116,758 | **116,758** |
| `INHERITS_FROM` | 7,149 | **7,149** |

Every per-type count matches `edges.jsonl` exactly. How these were counted is
itself a finding — see §6.3.

### 2.5 Dual labelling

`tests/graph/test_ingest.py::test_dual_label_on_a_real_test_symbol` takes the
first `kind == "test"` record out of `symbols.jsonl` and matches it as both
`(x:Symbol)` and `(x:Test)`, checking the `fqn` agrees in both. The
complementary test asserts a `kind == "function"` symbol does **not** carry
`:Test`, and a third asserts the `:Test` population equals the `kind == "test"`
population exactly (21,966). A1.2's two-pass workaround holds at scale.

---

## 3. The text-blob contingency — numbers and recommendation

**This is the decision the task asked me to measure and not take unilaterally.**
Ingest supports both modes: `--text` writes `repr_L2_text`, `repr_L3_text` and
the two refs lists into node properties; the default leaves them out.

### 3.1 Measurements

Django's repr text totals **89.5 MB** across 43,420 symbols (mean 2.06 KB,
median 670 B, `repr_L3_text` max 347 KB).

Both modes were run end to end at full Django scale on a freshly wiped store.
The no-text mode was run twice; run-to-run variance on this VM is roughly 15 %,
so its figures are given as a range and the deltas as ranges too.

| | No text (default) | With text (`--text`) | Delta |
|---|---:|---:|---:|
| **Ingest wall** | **247–282 s** | **352.60 s** | **+25 % to +43 %** |
| — text pass itself | — | 58.4 s (43,420 rows) | |
| — edge writes | 212–231 s | 257.8 s | +12 % to +21 % |
| **Read-back, 609 batch reads** | **653–762 s** | **1,187.69 s** | **+56 % to +82 %** |
| Client peak RSS | 133–143 MB | 256 MB | +79 % |
| Counts correct | yes | yes | |
| Symbols whose text cannot be stored | — | **164** | |

The read-back gap is well outside the noise; the ingest gap is only partly so,
and the honest reading is that ingest cost is a secondary consideration here.

### 3.2 What actually blocks it

Ingest time alone would be affordable — the text pass is under a minute, and the
rest of the gap is close to run-to-run noise. Three other things are not.

**164 Django symbols cannot store their text at all.** A string property is
capped just under 32 KiB (§6.2). `repr_L3_text` exceeds it for 163 symbols and
`repr_L2_text` for one — `tests.admin_views.tests` needs 347 KB. There is no
partial-credit option: the engine rejects the write, so the only ways to put
these in the graph are truncating them (which silently falsifies I4's "token
counts describe the canonical emitted representation") or chunking each blob
across numbered properties — real complexity in the write path *and* the read
path, for 0.4 % of symbols. Ingest currently routes these rows to narrower
templates so the fields that *do* fit are still written, and reports the rest.

**Graph-resident text slows the closure's own hot path by 56–82 %.** This is the
decisive number. The read-back above uses the label-free A1.1 batch form — the
exact query shape `expand()` runs in production — over the same 43,420 node ids
in both modes. It takes 653 s and 762 s against lean nodes across two runs, and
1,187.69 s against fat ones. Text the closure never reads still makes the
closure's reads substantially slower, because the engine pays for the node
record either way.

**And it makes labelled queries miss the deadline earlier.** At 5,000 symbols,
`MATCH (a:Symbol)-[:CALLS]->(b:Symbol) RETURN count(*)` returns 4,588 in the
no-text graph and exceeds the 29,999 ms deadline in the text graph — same
topology, same edge count, only the node payload differs. The same effect shows
up in writes: edge `MERGE` has to `MATCH` labelled endpoints, and edge writes
run 21 % slower against fat nodes.

### 3.3 Recommendation

**Keep the graph lean; serve `repr_*_text` from an on-disk offset index into
`symbols.jsonl` at emission time.** `--text` stays in the CLI so the decision
can be revisited, but the default is off, and the Django graph currently loaded
was ingested without text.

This is the A1.1 sidecar principle applied one level further — the graph holds
topology, the application holds bulk — and it costs almost nothing to build:
`--offset-index` writes `{id: [byte_offset, length]}` during the pass that
already reads every line, and `sidecar.read_repr_text()` seeks and returns the
record. §6.3's emission tier fetches text for the ~30 symbols actually emitted,
so a seek per emitted symbol is cheaper than the graph round trip A1.1 already
budgeted for, and it has no size ceiling.

The one thing this gives up is A1.1's stated plan to fetch `repr_*_text` from
the graph by single-source query at emission. That plan does not survive the
164 oversized symbols regardless of which storage is chosen, so the offset index
replaces it rather than competing with it. **Flagged for the spec owner:** A1.1's
sentence "they are fetched from the graph by single-source query at emission
time" needs amending either way.

---

## 4. Item 4 — sidecar, expand, fixpoint

### 4.1 Sidecar footprint

| | Django |
|---|---:|
| Symbols | 43,420 |
| Load time | **0.61 s** |
| Deep size (shared strings counted once) | **20.0 MB** |
| Process RSS delta | **23.2 MB** |
| Text deliberately excluded | 89.5 MB |

A1.1 estimated ~40 MB for a 200k-symbol repository. Django at 43,420 symbols
costs 20.0 MB, so the real figure is roughly 2.4× that per-symbol estimate —
same order, worth knowing before a 200k repo. The dominant terms are the `fqn`
strings and the `repr_L3_refs` tuples (105,923 ints across the corpus). `kind`
is interned, so its 43,420 references cost six strings.

The point stands regardless: the scalar table is **less than a quarter** of the
text it replaces, and every cost, kind and refs lookup in the closure is a dict
hit rather than a round trip.

### 4.2 `expand()` round-trip cost

Amendment A1.1's corrected query works verbatim. `tests/graph/test_closure_django.py`
pins the shape (no labels on either endpoint, exactly two projections) and keeps
the Item 0 rejection test for the verbatim v1.3 form, so an engine upgrade that
starts accepting it surfaces as a failure.

Measured over 200 six-seed trials against the full Django graph:

| | Observed |
|---|---:|
| Round trips, single-source one hop | **6** (= one per hard edge type) |
| Round trips, two-hop closure | **12** — median *and* max across 200 trials |
| Total round trips, 200 trials | 2,400 (400 hops) |
| Latency per round trip | **26–41 ms** |
| Latency per two-hop closure | **311–497 ms** mean; 303 ms median, 432 ms p90, 760 ms max on the faster run |
| Edges returned | 11,096 |
| Property fetches | **0** |
| Destinations filtered as non-`Symbol` | **0** |

Latency is given as a range across two runs separated by a full graph rebuild.
The *round-trip counts and the closure results were byte-identical* across both
— 2,400 round trips, 11,096 edges, the same distribution to the decimal — so the
spread is VM timing noise, not behaviour.

**The §5.1 cost model holds exactly.** Twelve batched requests for an unchunked
two-hop closure, and the chunking formula `6 × ceil(|frontier|/B) × hops` is
pinned by a test that expands a 1,200-node frontier at `B=500` and asserts 18
round trips.

The zero filtered destinations is worth stating: every `dst` returned by every
expansion was present in the sidecar, which independently confirms the JSONL
contract's referential-integrity guarantee that every endpoint resolves to an
emitted symbol.

### 4.3 Propagation table

Implemented exactly as §4 specifies, with no adjustments. `INHERITS_FROM` is
absent — it is ingested for display and ranking but never appears in
`HARD_EDGES`, never gets a query template, and never propagates. Three fixture
tests guard this: one asserts every table row strictly decreases, one asserts
the table's key set equals `HARD_EDGES` exactly, and one asserts an unknown edge
type reaching `closure()` (an evidence relation, say) neither raises nor
propagates.

### 4.4 Fixpoint

`closure(seeds, expand, profile=None)` returns a `ClosureResult` carrying the
level map, provenance, hop count and edges examined. `profile` is accepted and
ignored with a `TODO(item-5)`; a fixture test asserts passing one changes
nothing, so Item 4 cannot silently start doing Item 5's job.

Provenance is recorded on every level *rise*, so a node raised twice carries
both reasons in order. Across all 200 Django trials, every non-seed entry has at
least one `Reason(via, edge, rule)` whose `via` is itself in the closure and
whose `edge` is a hard edge — the acceptance gate's item 6, checked on real data
rather than a fixture.

---

## 5. Django cross-validation vs the prediction

200 trials × 6 seeds, seeds drawn from the documented filter (`kind ∈ {function,
method}`, `file` not under `tests/`, `repr_L3_tokens >= 150`), which selects
1,891 of Django's 43,420 symbols. Sampling is deterministic (`rng_seed=20260817`).

| Metric | Predicted | Observed | Ratio |
|---|---:|---:|---:|
| closure size median | 47 | **46.0** | 0.98 |
| closure size p90 | 83 | **86** | 1.04 |
| closure size max | 150 | **180** | — |
| L3+L2 tokens median | 3,308 | **3,026** | 0.92 |
| L3+L2 tokens p90 | 6,797 | **6,832** | 1.01 |
| L3+L2 tokens max | 20,272 | **12,253** | — |
| over 8,000 tokens | 10/200 | **10/200** | exact |

**PASS, and by a much wider margin than the order-of-magnitude gate asks for.**
Every headline figure agrees within 8 %, and the tail count is exact.

The two deviations are both in the direction the task predicted. The simulation
ignored level-merging on converging paths, and merging cuts both ways: it makes
the largest closures *bigger* in node count (max 180 vs 150) because a node
reached by two paths is kept at the higher level and re-expanded, while it makes
the most expensive closures *cheaper* in tokens (max 12,253 vs 20,272) because
a node counted twice by the simulation is counted once here. The medians barely
move because merging is rare near the middle of the distribution.

Level composition, mean per trial: **6.0 L3** (the seeds) → **17.0 L2** →
**29.8 L1**. Median emitted set (L2+L3) is 22 symbols. That shape is the thesis
in one line: six seeds pull in about seventeen declarations and name about
thirty more, for roughly 3,000 tokens.

---

## 6. Discrepancies from spec v1.3 and Amendment A1

Five new findings. None required an architectural change; all are recorded with
the observed behaviour that proves them, per the Item 0 precedent.

### 6.1 `null` parameter values are rejected outright (LOAD-BEARING for ingest)

Spec §2.1 declares `evaluable: BOOLEAN` and `static_value: STRING, null if not
evaluable`, and the JSONL contract emits both as `null` for non-constants. Any
batch carrying one is rejected before execution:

```
invalid parameter $rows: only boolean, signed integer, finite float,
and string parameters are supported
```

This is a **parameter-layer** rejection, not a property-layer one, so it cannot
be worked around by writing `null` differently — the row never reaches the
engine. It affects 41,762 of 43,420 Django symbols (96 %).

**Resolution:** `evaluable` and `static_value` each get their own `UNWIND` pass
over their non-null subset, so a null is expressed as an absent property. This
is the same shape A1.2 already forced for `:Test`, and it composes with it. No
spec change needed, but §2.1's node schema should note that a null-valued
property is absent rather than present-and-null.

### 6.2 String properties are capped just under 32 KiB

An over-cap value fails with an unhelpful `internal query execution error`
(`Neo.DatabaseError.General.UnknownError`), not a size diagnostic. Bisected
precisely: with a 3-character property key, **32,743 bytes is accepted and
32,744 is rejected**. The budget covers the key and framing too, so a longer
key lowers the ceiling; ingest uses a conservative 32,000.

The limit is **per string value**, not per row or per request — 100 rows × 8 KB
(800 KB), 500 rows × 2 KB (1 MB), and two 32 KB properties on one node all
succeed.

Django exceeds it in three places:

| Field | Symbols over cap | Largest |
|---|---:|---|
| `repr_L3_text` | 163 | 347,096 B (`tests.admin_views.tests`) |
| `repr_L2_text` | 1 | 66,533 B (`tests.validators.tests.INVALID_URLS`) |
| `static_value` | 1 | 66,517 B (same symbol) |

**The `static_value` case broke the default no-text ingest**, which is worth
saying plainly because it is not obvious: this is not a text-blob problem that
`--text` avoids. One Django constant folds to a 66 KB string, and §3.1's
constant folding will produce more of these on any repo with a large literal
table.

**Resolution:** oversized values are **skipped and reported**, never truncated
and never written as an empty string. A clipped constant is a worse artifact
than an absent one — it looks valid and is wrong — and I4's upper-bound claim
depends on stored text being exactly what was costed. The value stays reachable
through the `symbols.jsonl` offset index. Identity fields (`fqn`, `file`,
`body_hash`) instead raise, because a node nothing can resolve is not a
degradation worth accepting silently; Django's longest `fqn` is 147 B, so this
path is defensive.

### 6.3 Whole-graph relationship counts do not survive the deadline (LOAD-BEARING for verification)

The engine terminates queries at 29,999 ms. At Django scale a whole-graph
relationship count does not finish, and **labels are what make it expensive** —
the same asymmetry A1.1 found in the batch-read path:

| Query | Edges | Result |
|---|---:|---|
| `MATCH (a)-[:READS_CONSTANT]->(b) RETURN count(*)` | 820 | 820 in **2.29 s** |
| `MATCH (a:Symbol)-[:READS_CONSTANT]->(b:Symbol) RETURN count(*)` | 820 | **deadline exceeded** |
| `MATCH (a)-[:OVERRIDES]->(b) RETURN count(*)` | 1,992 | 1,992 in **4.19 s** |
| `MATCH (a)-[:CALLS]->(b) RETURN count(*)` | 95,288 | **deadline exceeded** |
| `MATCH (n:Symbol) RETURN count(*)` | 43,420 nodes | 43,420 in **17.11 s** |

Unlabelled relationship scans run at roughly 400–500 edges/s, so the wall
arrives near 13,000 edges. Adding endpoint labels to an 820-edge count is enough
to blow a 30 s budget on its own. Node counts with a label are fine but already
at 17 s of the 30 s budget at this size.

**Resolution:** `verify()` sums out-degree through the chunked, label-free A1.1
batch form over every node id. Every edge's source is an emitted symbol by the
JSONL contract, so this is a complete count, each request stays far inside the
deadline, and it exercises the same query path `expand()` uses in production
rather than a separate one. It costs 609 requests and 653 s for the full Django
graph, so the test that runs it is gated behind `CC_FULL_VERIFY=1`.

**Not a blocker for the product** — no hot path counts a whole relationship
type. It matters for CI assertions and for §9.3's soundness checks, which should
be written against the batched form from the start.

### 6.4 Bolt caps a request message at 2 MiB, and row-count chunking cannot respect it

```
Neo.ClientError.Request.Invalid: message size exceeds limit of 2097152 bytes
```

The server also **resets the connection** on the way to reporting this, so the
driver surfaces a cascade of `ConnectionResetError` / `ServiceUnavailable`
before the real diagnosis appears. A retry policy that treats
`ServiceUnavailable` as transient will loop on a defunct session rather than
fail fast.

§5.1 says to chunk to `B` rows per batch, which is necessary but not
sufficient: `B` bounds the row *count*, and the limit is on serialised *bytes*.
Django's scalar rows are ~200 B, so `B=500` is 100 KB and nowhere near the
ceiling — but 250 rows carrying `repr_*_text` from the test-heavy tail of the
corpus exceed 2 MiB and are rejected.

**Resolution:** `client.chunk_rows()` bounds every batch by row count *and* by
estimated payload, with a 1.5 MiB budget leaving headroom for query text and
Bolt framing. A single row larger than the budget is still sent alone, because
the caller cannot split it and the engine's rejection is a clearer signal than
a silent drop. Six unit tests in `tests/graph/test_client.py` pin the policy.

**Suggested Appendix A addition:** "batch size is bounded by serialised message
bytes (2 MiB), not only by row count."

### 6.5 Cosmetic: `count(n)` is rejected, `count(*)` is not

`MATCH (n:Symbol) RETURN count(n) AS c` fails with `property values support
integer, float, boolean, and string literals`; `count(*)` with the same pattern
works. Appendix A lists `count` as available without distinguishing the two
forms. One line, no impact.

---

## 7. Test inventory

```
tests/graph/test_closure_fixtures.py   33 passed   no database, 0.3 s
tests/graph/test_sidecar.py             8 passed   no database
tests/graph/test_client.py              7 passed   no database
tests/graph/test_ingest.py             17 passed   HydraDB, 11 min with CC_FULL_VERIFY=1
                                                   (14 in 60 s without it)
tests/graph/test_closure_django.py     20 passed   HydraDB, 98 s   (CC_TRIALS=200)
```

**Test-ordering hazard, found while running the whole repo suite.**
`tests/integration/test_hydradb_compat.py` (Item 0) writes its fixtures into the
same `default` graph, adding 12 `:Symbol` and 1 `:Test` node. Running the full
suite therefore leaves the graph at 43,432 / 21,967, and a later
`test_all_symbols_landed` fails on a count that is not actually wrong. pytest's
collection order happens to run `tests/graph` first, so `python -m pytest` is
green today — but that is luck, not design.

Not fixed here: `tests/integration/` is Item 0's file and shared with the
amendment's guardrail additions, so changing its fixture strategy is not mine to
decide unilaterally. **Recommendation for the spec owner:** give the compat
suite its own `GRAPH_ID`, or have it clean up its own ids, before Item 10 starts
running suites in arbitrary order in CI. The workaround today is
`scripts/run_hydradb.sh reset` followed by a fresh ingest before asserting
counts.

The fixture suite covers the cases the task named — linear chain, diamond
maximum, cycle termination, multi-seed merging, L1 terminality, provenance
completeness — plus edge-order independence on the diamond, self-loops, a
6-clique, seeds at L2 and L1, and the guarantee that a seed's level is never
lowered by a rule. It stubs `expand()`, so it pins propagation semantics
independently of the engine, the ingest layer and the sidecar. It was green
before any real data was touched.

---

## 8. Files

```
src/context_compiler/graph/__init__.py       new
src/context_compiler/graph/client.py         new   Bolt session, chunking, retry with bisection
src/context_compiler/graph/ingest.py         new   JSONL -> HydraDB, CLI, eid()
src/context_compiler/graph/sidecar.py        new   A1.1 scalar table + byte-offset index
src/context_compiler/graph/expand.py         new   amended A1.1 frontier read
src/context_compiler/graph/closure.py        new   §4 table + §5 fixpoint + provenance
src/context_compiler/graph/validate.py       new   cross-validation against the prediction
tests/graph/{__init__,test_closure_fixtures,test_sidecar,test_client,
             test_ingest,test_closure_django}.py
scripts/validate_closure_django.py           new
scripts/bench_ingest.sh                      new
scripts/run_hydradb.sh                       modified — added `reset`
docs/spikes/graph-item-3-4-results.md        this file
```

Nothing under `src/context_compiler/extract/`, `tests/unit/`, `docs/specs/`,
`~/hydradb` or `~/targets/` was modified.

---

## 9. Unresolved issues

1. **A1.1's emission-time text plan needs amending** (§3.3). Fetching
   `repr_*_text` from the graph by single-source query cannot work for the 164
   Django symbols whose text exceeds the property cap, whichever storage mode is
   chosen. The offset index is implemented and recommended; the spec owner
   should confirm before Item 6 builds against it.
2. **Constant folding can emit oversized `static_value`** (§6.2). One Django
   constant folds to 66 KB. §3.1 does not bound the folded value's size, and the
   extraction layer is not mine to change. Currently handled by skipping the
   property; a cap at fold time would be cleaner and is worth raising with the
   extraction owner.
3. **CI count assertions must use the batched form** (§6.3). Any §9.3 soundness
   check written as a whole-graph count will time out on repositories this size.
4. **Sidecar scaling** (§4.1). 20.0 MB at 43,420 symbols extrapolates to ~92 MB
   at 200k, against A1.1's ~40 MB estimate. Fine on this VM, worth knowing.
5. `r.id` remains unreadable (A1.4). Confirmed still true here; unblocking is
   not needed because `eid()` is re-derived application-side, but Item 9 should
   re-check before relying on it.
6. **Item 0's compat suite shares the `default` graph with the Django data**
   (§7). Harmless under today's collection order, a latent CI failure under any
   other. Needs a decision from the spec owner, since the file is not mine.
