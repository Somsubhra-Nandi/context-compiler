# Item 10a — Baseline Arm B results

## Registration and arm definition

This comparison uses the same 200 deterministic six-seed trials as A6.6:
seed pool **1,891**, `rng_seed=20260817`, sidecar **43,420** symbols, and an
8,000-token budget. Both arms use the same live HydraDB graph, `cost()`, and
`emit()` implementation.

Arm B is an undirected one-hop graph top-k retriever. It unions forward and
reverse neighbours over the six `PROPAGATION` edge types, ranks unique
neighbours by the existing `idf()` scorer used by `impact_cone`, and admits
each selected neighbour at L2. It never runs `closure()`, `induced_delta()`,
or profile level propagation. The greedy scan continues after an item does
not fit, so a later smaller declaration can still be admitted.

Arm B excludes the compiler's 5% `HINT_RESERVE`. A plain top-k retriever has
no separate identity-hint tier, so the full budget is available to retrieved
blocks. Mandatory identity references are still charged by `cost()` and
rendered by the shared emitter. This convention is fixed in the arm and is
not a tuning response to the trial outputs.

## Results

Values are distributions over all 200 trials. “Emitted tokens” is the actual
rendered text; “budgeted tokens” is the arm's charged total. The reference
compiler's median remains the A6.6 figure: 34 emitted symbols and 5,602
budgeted tokens (70.0% utilisation).

| metric | arm | min | median | p90 | p99 | max |
|---|---|---:|---:|---:|---:|---:|
| emitted symbols | compiler | 10 | 34 | 51 | 72 | 73 |
|  | Arm B | 18 | **35** | 55 | 72 | 73 |
| emitted tokens | compiler | 2,410 | 5,043 | 7,295 | 7,738 | 7,760 |
|  | Arm B | 2,289 | **4,640** | 7,115 | 7,701 | 7,752 |
| budgeted tokens | compiler | 2,497 | 5,602 | 7,911 | 7,990 | 7,996 |
|  | Arm B | 2,449 | **5,325.5** | 7,977 | 7,998 | 7,999 |
| utilisation | compiler | 0.3121 | 0.7000 | 0.9889 | 0.9988 | 0.9995 |
|  | Arm B | 0.3061 | **0.6700** | 0.9971 | 0.9998 | 0.9999 |
| identity-only references | compiler | 0 | 4 | 9 | 31 | 56 |
|  | Arm B | 0 | **4** | 9 | 14 | 20 |
| identity-only reference tokens | compiler | 0 | 63 | 156 | 593 | 979 |
|  | Arm B | 0 | **66** | 156 | 241 | 359 |
| graph round trips | compiler | 18 | 24 | 24 | 24 | 24 |
|  | Arm B | 42 | **42** | 42 | 42 | 42 |
| compile latency (ms) | compiler | 205.84 | 413.53 | 748.60 | 1,581.92 | 1,684.32 |
|  | Arm B | 156.55 | **259.92** | 328.07 | 442.55 | 1,658.01 |

Arm B admitted 29 candidates at the median (p90 55, max 67); the compiler's
candidate supply was **12.0 median / 11.0 admitted**, with candidate supply—not
the budget—the binding constraint around the 70% utilisation point. Arm B's
larger undirected one-hop supply produced one more emitted symbol at the
median and packed the upper tail more tightly, but its declarations are
cheaper than the compiler's closure-induced context, so its median budgeted
utilisation was 67.0%, not higher than the compiler's 70.0%. That is the
measured result, including the fact that the prompt's expectation of a higher
median fill did not hold for this graph.

### Dangling-reference metric

The reported “identity-only references” are the set returned by
`mandatory_identities(levels, sidecar)`: names referenced by emitted text that
do not have an emitted L2/L3 block. The shared emitter renders these names as
mandatory identity lines, so literal FQN lookup after emission has no unknown
sidecar names; the metric intentionally measures the weaker state—an identity
without a declaration or body. Arm B's decisive soundness signal is instead
its independent `is_closed()` result: **0/200 true, 200/200 false (0.0%)**.
The one-hop arm therefore exposes an incomplete graph context even when its
identity lines make the names visible.

## What was cut

Arm B cuts the compiler's structural closure fixpoint and all level
propagation. It also cuts profile demotion and closure-preserving optional
bundles: a ranked neighbour is admitted as a bare L2 block. The only
comparison-specific budget choice is the documented removal of the 5% hint
reserve; source, provenance, mandatory identities, framing, and rendering
remain shared through the existing cost/emitter paths.

The price is structural soundness. Arm B is faster in median compile CPU/wall
latency and emits slightly more blocks, but it spends 42 graph reads on its
undirected one-hop neighbourhood and fails the closure check on every trial.
The compiler spends a median 24 reads to build and pack a smaller, structurally
closed result.

Raw per-trial measurements are in `/tmp/baseline-arm-b-200.json` from
`scripts/validate_baseline_arm_b.py`. The captured full-output example is
[`baseline-arm-b-example.md`](baseline-arm-b-example.md).

## Verification

The mandated command was run against the real HydraDB:

```text
python -m pytest tests/unit tests/mcp tests/integration tests/graph -q
315 passed, 3 skipped, 2 failed
```

The two failures are external graph-state consistency checks, not Arm B
assertions: HydraDB contains 43,432 `Symbol` / 21,967 `Test` nodes while the
sidecar contract expects 43,420 / 21,966. No mock or substitute graph was used.
The new Arm B suite itself passes: **5 passed**.

## Arm A stretch outcome

Arm A was attempted only after Arm B was complete and committed. Installing
`sentence-transformers==3.4.1` completed, but the pinned code model
`microsoft/codebert-base` revision
`3b0952feddeffad0063f274080e3c23d75e7eb39` did not finish loading/downloading
within the bounded stretch window. The embedding log ends at the library's
“creating a new one with mean pooling” step; the partial Hugging Face cache was
953 MB and the symbol embedding cache contained zero vectors. Arm A was
abandoned without debugging around the timeout and has no reported metrics.
The controlled comparison is therefore graph ranking without closure; vector
top-k is the obvious next arm.
