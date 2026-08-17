# Amendment A3 to Implementation Spec v1.3

**Status:** Adopted. Raised by Item 5 (`docs/spikes/graph-item-5-results.md`).
**Scope:** §1.1, §6.1, §6.2, §6.3, Appendix A. No architectural change. Invariants I1–I6 unaffected and all verified on 200/200 Django trials.

Item 5 disproved three of the spec's own calibration figures by measurement. All three are recorded here with the observed numbers, because two of them affect how the system is described publicly and one affects how the demo is scripted.

---

## A3.1 — Batch reads are directed source-out only; reverse discovery costs one request per seed

§6.3's candidate pool needs reverse `CALLS`. **Every reversed and undirected batch form is rejected at parse time**, verified across five variants:

| Query | Diagnostic |
|---|---|
| `UNWIND … MATCH (x {id: row.v})<-[:CALLS]-(y)` | `UNWIND source requires an id field` |
| `UNWIND … MATCH (y)-[:CALLS]->(x {id: row.v})` | `UNWIND batch node requires …` |
| `UNWIND … MATCH (x {id: row.v})-[:CALLS]-(y)` (undirected) | `UNWIND batch does not support …` |

The classifier requires the id-bound node to be the arrow's **source**. This is stronger than A1.1 established: batch reads reject labels, extra projections, *and* direction.

### Decision

The single-source form ships: `MATCH (x {id: $v})<-[:CALLS]-(y) RETURN y.id AS dst`. Measured 182 ms for six typical seeds; median in-degree in the eligible pool is 3.

**Corrected cost model, replacing §6.3's flat 24:**

```
round_trips = 12                            mandatory closure
            + |seeds|                       reverse discovery (A3.1)
            + 6 × ceil(|candidates| / B)    envelope
```

Note the envelope is **6, not 12**, for L2 candidates: the §4 table sends an L2 source to L1, and L1 is terminal, so there is no second hop to prefetch. Twelve remains correct for L3 candidates, which is what Item 9's covering tests will be. Measured: median 24, p90 24, max 30.

The two deviations from §6.3 happen to cancel at six seeds. That is luck. **The model is now linear in seed count**, so §6.4's 5–8 seed recommendation is load-bearing rather than stylistic. It would not hold at 30 seeds.

### Required mitigation — hub skip

A hub is expensive: a 2,824-in-degree node takes 9.0 s, and **`LIMIT` does not help** because it bounds rows returned, not the scan (9.6 s with `LIMIT 200`).

**Skip reverse discovery for any symbol whose in-degree exceeds 500.** A symbol with hundreds of callers yields no discriminative candidates — `idf` suppresses exactly those — so the read buys nothing and costs seconds. The degree table is already loaded for `idf`; this is a lookup, not new I/O.

**Appendix A addition:** *batch reads are directed source-out only; the id-bound node must be the arrow's source. Reverse traversal requires the single-source form.*

---

## A3.2 — `CLOSURE_BUDGET_EXCEEDED` is unreachable at realistic budgets, and that is correct

It fired **0/200** at 8,000 tokens, and 0/60 at 1,500 tokens. The reason is structural, not a sampling accident: `EXCEEDED` requires the **P0** floor to exceed the budget, and P0 puts seeds at L2 and everything else at L1, so its cost is bounded by the seeds' own *declarations* regardless of how large the closure beneath them is. Measured P0 floor: 465 tokens median, **689 at the worst of 200 trials**. It first becomes reachable below roughly 725 tokens.

Item 4's 16,000-token tail was the **P3** floor (max 12,898 here, agreeing with Item 4's 12,253 on source cost alone). Demotion is exactly the mechanism that escapes it, and it works — P3 tail 12,898, P1 tail 5,357.

### Decisions

**Do not manufacture it for the demo.** Its unreachability means the system degrades gracefully instead of failing, which is a better property than a dramatic error path. The demo shows the P3 floor and demotion; the exceeded status stays correct, first-class and fixture-tested.

**Correct the `suggestion` string.** Since the binding constraint is the seeds' own declarations, `EXCEEDED` is a *too many seeds* condition, not a *task too broad* one. §6.2's `"narrow the task or raise the budget"` becomes `"reduce the seed count or raise the budget"`.

**Watch it in Item 8.** Real seed resolution may produce more or larger seeds than the six-function synthetic filter. If seed counts rise, this becomes reachable and the corrected message becomes the right one.

---

## A3.3 — The profile ladder is effectively P3 → P1

| Profile | Floor median | p90 | max | Trials over 7,600 |
|---|---:|---:|---:|---:|
| P3 | 3,545 | 7,471 | 12,898 | **17** |
| P2 | 3,503 | 7,403 | 12,898 | **16** |
| P1 | 2,274 | 3,348 | 5,357 | 0 |
| P0 | 465 | 574 | 689 | 0 |

P2 costs 1.2% less than P3 and rescues exactly one of seventeen P3 overruns. Chosen 1/200 times. It only removes the four non-`CALLS`/`REFERENCES_TYPE` edge kinds from the first hop, and on Django those carry little first-hop mass. When a P3 floor overruns it usually overruns by a lot, so the scan falls through to P1.

**Decision: accept and describe honestly.** The visible demotion step is **P3 → P1, a 36% cut**, not a graceful four-step ladder. Re-cutting P2's edge set is a spec change that 1/200 does not justify. Keep P2 in the family — it is free, it preserves monotonicity, and it may matter on a repo with a different edge mix.

Observed hit rates at 8,000 tokens: **P3 91.5%** (spec estimated ~95%), P2 0.5%, P1 8.0%, P0 0%. The gap from 95% is explained: `cost()` now charges provenance trailers, mandatory identities and the header, which Item 4's `source_cost` did not.

---

## A3.4 — Candidate pool is capped; the greedy loop must not be quadratic in pool size

§6.3's loop re-evaluates every surviving candidate each iteration, so it is O(admissions × pool). At the median that is 300 evaluations and invisible. On one trial with a 784-candidate pool it was **62,370 evaluations and 21.9 seconds**, of which the database accounted for well under one second.

Item 7 exposes `compile_context` over MCP. A 22-second worst case is not shippable.

### Decision — cap the pool, do not implement CELF

**Rank candidates by the `score(y) / tokens_at(y)` upper bound and keep the top 150.** Median admissions is 21, so 150 is roughly 7× headroom and nothing below the cut would realistically be admitted. Combined with A3.1's hub skip, the observed max pool drops from 784 to about 150, bounding the loop at O(admissions × 150).

This is preferred to §6.3's lazy-greedy fallback because it is ~20 lines against ~100, and because the fallback's stated trigger was envelope cost — which is not the problem. The in-memory re-evaluation is.

**Do not drop the re-evaluation.** It is what makes shared dependencies pay off: the median bundle costs 81 tokens while containing 2 nodes, and later admissions are systematically cheaper because their mandatory closure is already present. Capping the pool preserves that; replacing greedy with a static ordering would destroy it.

Target after the fix: p99 compile latency under 3 s. Median is already 994 ms.

---

## A3.5 — Sibling-implementation candidates are dormant under the synthetic seed filter

Zero candidates on all 200 trials. The extraction layer emits `IMPLEMENTS` for class→ABC relations; the Item 4/5 seed filter selects functions and methods, which have no `IMPLEMENTS` out-edge. Implemented, tested, wired into `CANDIDATE_SOURCES`, never fires.

**Decision: accept, no change.** It fires on class seeds, which Item 8's real seed resolution will produce. Worth stating in the writeup that the Django candidate pool is effectively single-source (reverse `CALLS`, median 26 per trial), so §6.3's ranking claims are exercised on one source only.

---

## A3.6 — §1.1's L1-mandatory cost warning is wrong by a factor of thirty

§1.1 predicts "a 300-identity closure at ~10 tokens each consumes 3,000 of an 8,000-token budget." **Measured on Django: 6 identities, 97 tokens, 1.2% of budget** at the median. p90 is 12 identities / 226 tokens; the worst of 200 trials is 56 / 979.

The reason is worth understanding, because it is the propagation table working as designed: L1-mandatory is `refs − emitted`, and a symbol named in an L3 body is pulled to L2 **by the very edge that names it**. It leaves the dangling set as fast as it enters. The tier only catches references that no hard edge covers.

### Decisions

**Never quote §1.1's prediction as a measured cost** — in the README, the video, or anywhere else. Cite 97 tokens and the mechanism.

**Keep the accounting.** It is cheap here because extraction is good; it could spike on a repo with weaker edge coverage, and the guarantee is what makes `Structural closure: complete` true rather than approximate.

---

## Summary

| Section | Change |
|---|---|
| §6.3 | Cost model → `12 + \|seeds\| + 6·ceil(\|cands\|/B)`. Envelope is 6 for L2 candidates, 12 for L3. |
| §6.3 | Skip reverse discovery above 500 in-degree. |
| §6.3 | Cap the candidate pool at top-150 by `score/tokens`. Keep per-iteration re-ranking. |
| §6.2 | `EXCEEDED` suggestion → "reduce the seed count or raise the budget". |
| §6.1 | P2 retained but described as rarely taken; the real ladder is P3 → P1. |
| §1.1 | 3,000-token warning replaced by the measured 97 tokens. |
| Appendix A | Add: batch reads are directed source-out only; `LIMIT` bounds rows, not scan. |

### Measured figures for the writeup

```
mandatory floor          21 emitted symbols / 3,332 tokens
compiled context         42 emitted symbols / 7,610 tokens     2.00x emitted, 4.57x lattice
optional packing         50.4% of the compiled context
utilisation              95.0% median, 99.95% max, 0/200 over budget
round trips              24 median, 30 max
I6 closure               200/200, verified by independent re-read
I4 budget                200/200, max 7,996 of 8,000
compile latency          994 ms median (pre-A3.4)
```
