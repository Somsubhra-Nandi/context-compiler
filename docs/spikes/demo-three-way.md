# Three-way retrieval evidence: vector, graph top-k, and structural compiler

This comparison holds the Django snapshot, traceback, resolved seeds, shared
emitter, and 8,000-token ceiling constant while changing the retrieval
objective:

- **Arm A — Vector:** global semantic-similarity ranking over one embedding per
  symbol, followed by greedy admission; no graph or structural closure.
- **Arm B — Graph top-k:** one-hop graph ranking followed by greedy admission;
  no structural closure.
- **Arm C — Context Compiler:** graph expansion, profile propagation, and
  closure-bundle packing with explicit dependency preservation.

The generated three-way output is checked in as
[`data/demo-three-way.json`](data/demo-three-way.json). The deterministic set
and rank analysis is in
[`data/demo-three-way-analysis.json`](data/demo-three-way-analysis.json). The
existing canonical Arm B/C evidence remains unchanged.

## Methodology

The byte-identical traceback used by the prior two-way demo resolved these
three seeds, in this order:

1. `django.db.models.sql.query.Query.build_filter`
2. `django.db.models.query.QuerySet._filter_or_exclude`
3. `django.db.models.query.QuerySet.filter`

The seed IDs were identical across all three arms. Each arm used the same
8,000-token budget and shared emitter. Arm A ranked every non-seed symbol by
cosine similarity to the L2-normalized arithmetic mean of the normalized seed
vectors, with ascending node ID as the deterministic tie-break. Arm B and Arm
C are the previously defined graph top-k and structural compiler arms.

## Canonical 8k result

| Measure | Arm A — Vector | Arm B — Graph top-k | Arm C — Context Compiler |
|---|---:|---:|---:|
| Emitted symbols | 58 | 27 | 26 |
| L3 / L2 | 3 / 55 | 3 / 24 | 3 / 23 |
| Actual emitted tokens | 7,308 | 4,271 | 4,674 |
| Budgeted tokens | 7,996 | 4,716 | 5,048 |
| Utilization | 99.95% | 58.95% | 63.10% |
| Mandatory identity-only refs | 17 | 2 | 2 |
| Identity hints | 0 | 0 | 22 |

Arm A nearly fills the ceiling. Context Compiler stops substantially below it
once its structural slice has been selected. This illustrates that budget is a
ceiling, not a target; lower utilization alone is not evidence of higher
quality.

## Arm A model and index

| Property | Frozen value |
|---|---|
| Model | `jinaai/jina-embeddings-v2-base-code` |
| Model revision | `516f4baf13dec4ddddda8631e019b5737c8bc250` |
| Delegated remote-code revision | `3baf9e3ac750e76e8edd3019170176884695fb94` |
| Representation | one `repr_L2_text` per symbol |
| Index | 43,420 vectors, 768 dimensions, `float32`, L2-normalized |
| Generation | batch size 16; max sequence length 2,048; truncation enabled |

The recorded GPU build timings were 0.342 s for the smoke check, 134.610 s for
embedding inference, and 143 s end to end. These are one-time index-construction
measurements, not per-query retrieval latency.

The generated index remains outside Git: `embeddings.npy` (133,386,368 bytes),
`ids.npy` (347,488 bytes), and `metadata.json` (1,997 bytes). No embedding
artifact is checked in.

## Arm A 200-trial summary

The frozen run used 200 trials, 6 seeds per trial, `rng_seed=20260817`, an
8,000-token budget, and the same 1,891-symbol candidate seed pool as canonical
Arm B. Arm A's seed lists matched Arm B ID-for-ID. All 200 trials returned
`OK`; actual and budgeted overruns were both 0.

| Measure | Median | Mean |
|---|---:|---:|
| Emitted symbols | 67 | 66.395 |
| Actual emitted tokens | 7,425.5 | 7,433.5 |
| Budgeted tokens | 7,995 | 7,993.49 |
| Utilization | 0.9994 | 0.9992 |
| Candidates | 43,414 | 43,414 |
| Admitted candidates | 61 | 60.395 |
| Identity-only refs | 24 | 24.425 |
| Identity tokens | 449 | 452.745 |

Median per-query timings were:

| Stage | Median |
|---|---:|
| Query construction | 0.213 ms |
| Ranking | 42.907 ms |
| Vector retrieval (query + ranking) | 43.267 ms |
| Greedy admission | 1,278.801 ms |
| Exact-output guard | 14.007 ms |
| Total Arm A | 1,338.907 ms |

The 143 s one-time build is deliberately reported separately from these
per-query measurements.

## Compiler-hint overlap diagnostic

Arm C exposes 22 structural identity hints. They are compiler-produced
context, not independently labeled ground truth. The following
**compiler-hint overlap** is therefore descriptive and must not be interpreted
as accuracy, ground-truth recall, or a correctness score.

| Arm | Reached | Emitted | Identity-only | Missed |
|---|---:|---:|---:|---:|
| Arm A — Vector | 2/22 (9.1%) | 2 | 0 | 20 |
| Arm B — Graph top-k | 0/22 (0%) | 0 | 0 | 22 |

Arm A emitted the two reached hints:

- `django.db.models.sql.query.Query.add_filter`
- `django.db.models.sql.query.Query.add_q`

It reached no additional compiler hints through identity-only references.
Arm C's coverage of its own 22-hint set is 22/22 by construction, not an
independent accuracy benchmark.

As a separate secondary comparison, Arm C's complete named set contains 24
identities: those 22 hints plus
`django.db.models.expressions.Expression` and `django.utils.tree.Node`.

| Arm | Reached | Emitted | Identity-only | Missed |
|---|---:|---:|---:|---:|
| Arm A — Vector | 3/24 (12.5%) | 2 | 1 | 21 |
| Arm B — Graph top-k | 2/24 (8.3%) | 0 | 2 | 22 |

This 24-identity comparison is distinct from the 22-hint diagnostic.

## Selected vector ranks

Ranks are one-based among all 43,414 non-seed candidates for the canonical
three-seed query.

| Compiler hint | Vector rank | Arm A admission |
|---|---:|---|
| `Query.add_filter` | 4 | admitted |
| `Query.add_q` | 14 | admitted |
| `Q` | 94 | not admitted |
| `Exists` | 105 | not admitted |
| `QuerySet._clone` | 117 | not admitted |
| `Query.trim_start` | 147 | not admitted |
| `JoinPromoter` | 159 | not admitted |
| `OuterRef` | 838 | not admitted |
| `ResolvedOuterRef` | 967 | not admitted |
| `Ref` | 1,150 | not admitted |
| `Query.names_to_path` | 1,272 | not admitted |
| `LOOKUP_SEP` | 40,772 | not admitted |

`django.db.models.sql.query.Query.trim_start`, independently validated by the
existing B1 case study as the upstream regression location, ranked 147th
globally by vector similarity but was not admitted into Arm A's 8,000-token
context. Context Compiler surfaced it as an identity hint. The B1 compiler
arm's one-line `Query.trim_start()` repair passed 505 tests with 0 failures.
This is an observation about this query's retrieval and packing behavior; it
does not show that vectors cannot find `trim_start` or that vector search is
universally inferior.

## Exact-output guard

In 2 of 200 Arm A trials, the shared pre-emission `cost()` estimate slightly
underpredicted the final emitted token count. The initially rendered totals
were 8,019 tokens in trial 144 and 8,093 in trial 173. Arm A therefore applies
an exact-output guard that checks actual emitted tokens and deterministically
removes the lowest-ranked admitted vector candidates until the hard 8,000-token
limit is satisfied. It removed one candidate in trial 144 and three in trial
173.

This affected 2/200 trials and did not alter the embedding model or revision,
query definition, vector scores or ranking, seed sets, shared `cost()`, Arm B,
or Context Compiler. The final run had zero actual and zero budgeted overruns.

## Interpretation and limitations

Semantic similarity, graph proximity, and structural completeness are
different retrieval objectives. This comparison characterizes those three
objectives on one frozen Python/Django corpus; it does not establish
statistical significance or a universal ordering between retrieval methods.
The compiler hints are not ground-truth labels, budget utilization is not a
quality score, and structural closure does not guarantee a correct agent
answer. Evidence remains primarily Python/Django and depends on extractor,
symbol-resolution, and graph quality.
