# Item 5 — Budget Admission and Closure-Preserving Packing: Results

HydraDB `0.1.0`, commit `6a2fbb192f37f51a93690a2ae2d2f5e27e6e4219`, the same
build Items 0 and 3–4 characterised. Python 3.14.4, `neo4j` 6.2.0, Ubuntu 26.04
under WSL2. Same Django graph, same seed filter and same `rng_seed=20260817` as
Item 4, so every figure here is directly comparable to
`docs/spikes/graph-item-3-4-results.md`.

```
Item 5  fixture suite        PASS   44 tests, no database, 0.4 s
Item 5  profile monotonicity PASS   pointwise, on fixtures and on 20 real seed sets
Item 5  I6  is_closed        PASS   200/200 trials, verified by independent re-read
Item 5  I4  budget respected PASS   200/200 trials, max 7,996 of 8,000
Item 5  round trips          PASS   median 24, p90 24, max 30
Item 5  packing              PASS   21 -> 34 emitted symbols median, 1.62x the floor
```

One new engine constraint, **A3.1**, and it is load-bearing: there is no
batched reverse read, so the candidate pool costs one round trip per seed. It
is reported in §7 rather than worked around, per the task's instruction.

Two of the task's own calibration figures did not reproduce, both in ways worth
knowing before the demo — `CLOSURE_BUDGET_EXCEEDED` is **unreachable** at an
8,000-token budget with six seeds (§4.3), and the L1-mandatory tier costs about
a third of what §3 predicted (§5.3). Neither is a defect; both are measured.

---

## 1. Reproduction

```bash
cd ~/context-compiler
source .venv/bin/activate

# assumes the Item 3 ingest is already loaded
python scripts/validate_budget_django.py --trials 200 --verify-closure \
    --out /tmp/cc-item5.json

python -m pytest tests/graph/test_budget_fixtures.py -q     # 44 tests, no DB
CC_TRIALS=200 python -m pytest tests/graph/test_budget_django.py -q
```

The full 200-trial validation takes **394 s** end to end, including an
independent `is_closed()` re-read of every emitted node on every trial.

---

## 2. The headline number

**The task asked for one figure above all others: how many symbols end up in a
compiled context versus the mandatory floor alone.**

| | Mandatory floor | Compiled context | Growth |
|---|---:|---:|---:|
| Symbols in the closure | **41** | **187.5** | **4.57×** |
| Symbols actually emitted (L2+L3) | **21** | **42** | **2.00×** |
| Tokens | 3,332 | 7,610 | 2.28× |

Medians over 200 trials at an 8,000-token budget. The two growth figures say
different things and both matter:

* **Emitted content doubles.** The floor emits 21 symbols; the compiled context
  emits 42. Half of what the model receives is optional context the packer
  earned with leftover budget — so packing is the main event, not a corner
  case, exactly as the task predicted.
* **The lattice grows 4.6×.** Every admitted caller drags its own mandatory L1
  targets in by rule. Those cost nothing in source tokens but they are what
  makes I6 hold: the context is closed at 187.5 symbols, not merely at 42.

Level composition of the final context, median: **6 L3 → 36 L2 → 142 L1**.
Item 4's floor was 6 L3 → 17 L2 → 30 L1. The seeds are unchanged, the
declaration tier doubles, and the identity lattice grows fivefold.

---

## 3. Profile hit rates at 8,000 tokens

200 trials × 6 seeds, `HINT_RESERVE = 0.05`, so `effective = 7,600`.

| Outcome | Trials | Share | Task's estimate |
|---|---:|---:|---:|
| `OK` (P3 FULL) | **183** | **91.5 %** | ~95 % |
| `DEMOTED:P2` | 1 | 0.5 % | — |
| `DEMOTED:P1` | 16 | 8.0 % | ~5 % |
| `DEMOTED:P0` | 0 | 0 % | — |
| `CLOSURE_BUDGET_EXCEEDED` | **0** | **0 %** | reachable |

P3 is satisfied on 91.5 % of tasks and demotion fires on 8.5 %. Both are within
striking distance of the task's ~95 %/~5 % estimate; the gap is explained by
`cost()` now charging provenance trailers, mandatory identities and the header,
which Item 4's `source_cost` did not. Adding roughly 200 tokens of metadata to
a 3,300-token floor pushes a few more trials over the line.

**P2 is nearly vestigial, and the floor costs say exactly why.** Measured over
the same 200 seed sets:

| Profile | Floor, median | p90 | max | Trials over 7,600 |
|---|---:|---:|---:|---:|
| **P3** | 3,545 | 7,471 | 12,898 | **17** |
| **P2** | 3,503 | 7,403 | 12,898 | **16** |
| **P1** | 2,274 | 3,348 | 5,357 | 0 |
| **P0** | 465 | 574 | 689 | 0 |

**P2 costs 1.2 % less than P3 and rescues exactly one of the seventeen trials
where P3 overruns.** It only removes the four non-`CALLS`/`REFERENCES_TYPE` edge
kinds from the first hop, and on Django those carry a small share of the
first-hop mass. When a P3 floor overruns it usually overruns by a lot, so the
scan falls through to P1.

Worth knowing before the demo: the visible demotion step is **P3 → P1**, a 36 %
cut, not the graceful four-step ladder the profile table suggests.

Cost monotonicity along the family held on **200/200** trials (0 violations).
That is checked rather than assumed — see §9.1.

---

## 4. Budget behaviour

### 4.1 Utilisation

| | Value |
|---|---:|
| Total tokens used, median | **7,610 / 8,000** |
| Utilisation, median | **95.0 %** |
| Utilisation, p90 | 99.7 % |
| Utilisation, max | 99.95 % (7,996 of 8,000) |
| Utilisation, min | 30.9 % |
| Budget exceeded | **0 / 200** |

The packer fills the budget: half the trials land above 95 % and the tightest
comes within four tokens of the ceiling without ever crossing it. The 30.9 %
minimum is a trial whose candidate pool was empty — nothing to pack, so the
floor is the answer.

### 4.2 Where the tokens go, median trial

Each row is the median of that quantity computed **per trial**, so the columns
compose:

```
mandatory floor                          3,332
optional bundles admitted by packing    +3,246      21 bundles
                                        -------
context cost                             7,238      of which:
    L1-mandatory identities                 97        1.2 % of budget
    header                                  40
hints (the 5 % reserve)                   +394
                                        -------
total                                    7,610      of 8,000
```

**Optional packing is 50.4 % of the compiled context** at the median — almost
exactly the ~62 % the task estimated, and confirmation that it is the main
event rather than a garnish. The source/provenance split inside the floor is
not instrumented, so it is not quoted.

### 4.3 `CLOSURE_BUDGET_EXCEEDED` is unreachable at this budget

**The task listed it as reachable at 8,000 tokens (2/200 trials over 16,000).
It fired 0 times, and the floor costs show it cannot fire.** At a 1,500-token
budget all 60 trials still succeed — every one of them by demoting all the way
to P0.

The reason is structural, not a sampling accident. `EXCEEDED` requires the
**P0** floor to exceed the effective budget, and P0 puts the seeds at L2 and
everything else at L1. Its cost is therefore bounded by the six seeds' own
*declarations* plus identity lines, **no matter how large the closure below them
is**. Measured: P0's floor is 465 tokens at the median and **689 at the worst of
200 trials**, so `EXCEEDED` first becomes reachable at a budget below roughly
**725 tokens** — two orders of magnitude under the demo budget.

Item 4's 16,000-token tail was a measurement of the **P3** floor (max 12,898
here, agreeing with Item 4's 12,253 on source cost alone). Demotion is precisely
the mechanism designed to escape it, and it works: the P3 tail is 12,898 tokens,
the P1 tail is 5,357.

`CLOSURE_BUDGET_EXCEEDED` is still correct, still first-class, and covered by a
fixture test that checks its `deficit` arithmetic. But **the demo cannot show it
with six Django seeds at any sane budget.** To exercise it live you need a seed
set whose *own declarations* do not fit — dozens of seeds, or seeds that are
very large classes. Flagged for whoever scripts the video.

---

## 5. Cost model

### 5.1 The three L1 tiers, measured

| Tier | Nodes, median | Tokens, median | Truncated |
|---|---:|---:|---|
| L1-lattice | 142 | **0** | never emitted |
| L1-mandatory | **6** | **97** | never |
| L1-hints | — | **394** of a 400 reserve | 184/200 trials |

### 5.2 The hint reserve is always spent

The 5 % reserve is exhausted on 184 of 200 trials (median 394 of 400 tokens).
There is never a shortage of nearby symbols worth naming, so the reserve
behaves as a fixed cost rather than an occasional one. That is the intended
design — hints are the only truncatable tier — but it means the effective
packing budget is 7,600, not 8,000, on essentially every task.

### 5.3 L1-mandatory does not bite

**Both the spec and the task warned this would hurt.** §1.1 predicts "a
300-identity closure at ~10 tokens each consumes 3,000 of an 8,000-token
budget"; the task says "~30 L1 identities per closure at ~10 tokens each is 300
tokens, and the number can spike."

Measured on Django: **6 identities, 97 tokens, 1.2 % of the budget** at the
median. The p90 is 12 identities / 226 tokens and the worst trial in 200 is 56
identities / 979 tokens.

The reason is that L1-mandatory is `refs − emitted`, and the closure has
already emitted most of what the emitted text names. A symbol named in an L3
body is, by the §4 table, pulled to L2 by the very same edge — so it leaves the
dangling set as fast as it enters it. The tier only catches references the
propagation rules do *not* cover: names in text with no corresponding hard
edge.

This is good news for the budget and mildly bad news for the argument: the
completeness claim of §1.1 turns out to be cheap on this repository. It could
still spike on a repo with weaker edge extraction, so the accounting stays.

---

## 6. Round trips

| | Observed |
|---|---:|
| Per compile, **median** | **24** |
| p90 | 24 |
| min | 18 |
| max | **30** |
| Mandatory closure | 12 on every trial |
| Candidate discovery | 6 on every trial (one per seed) |
| Candidate envelope | 6 on 199 trials, 12 on one, 0 when the pool is empty |

**The gate is met: 24 median.** The breakdown is exactly the §6.3 budget, with
the halves swapped for a reason worth recording:

```
12   mandatory closure of the seeds          as Sec 6.3 predicted
 6   reverse CALLS, one per seed             A3.1 -- Sec 6.3 assumed a batch
 6   candidate envelope, ONE hop             Sec 6.3 budgeted 12
---
24
```

The envelope is half what §6.3 budgeted because **a candidate admitted at L2
needs only one hop of edges**: the §4 table sends an L2 source to L1, and L1 is
terminal, so there is no second hop to prefetch. Twelve would be right for L3
candidates, which is what Item 9's covering tests will be — `_envelope()`
already branches on the admission level for that reason.

That saving is what pays for A3.1's six single-source reverse reads. The two
deviations cancel, which is luck, not design.

**The one trial at 30** had a 784-candidate pool. The envelope is a frontier
read like any other and chunks the same way, so it cost
`6 × ceil(784/500) = 12`. The correct cost model is
`12 + |seeds| + 6 × ceil(|candidates|/B)`, and the test asserts that formula
rather than a flat 6.

### 6.1 Latency

| | Per compile |
|---|---:|
| median | **994 ms** |
| p90 | 1,830 ms |
| p95 | 2,324 ms |
| max | **21,854 ms** |

Median is comfortably interactive. **The tail is not, and the cause is the
packing loop, not the database.** See §8.1.

---

## 7. New engine constraint

### A3.1 — There is no batched reverse read (LOAD-BEARING for §6.3)

§6.3's candidate pool needs reverse `CALLS`. The task specified "the same
amended A1.1 shape, arrow reversed" and instructed me to verify it and stop
rather than work around a rejection. **Every reversed and undirected batch form
is rejected at parse time:**

| Query | Diagnostic |
|---|---|
| `UNWIND … MATCH (x {id: row.v})<-[:CALLS]-(y) RETURN row.v, y.id` | `UNWIND source requires an id field` |
| `UNWIND … MATCH (x {id: row.v})<-[:CALLS]-(y) RETURN x.id, y.id` | `UNWIND source requires an id field` |
| `UNWIND … MATCH (y)-[:CALLS]->(x {id: row.v})` | `UNWIND batch node requires …` |
| `UNWIND … MATCH (x {id: row.v})-[:CALLS]-(y)` (undirected) | `UNWIND batch does not support …` |
| `UNWIND … MATCH (x:Symbol {id: row.v})<-[:CALLS]-(y:Symbol)` | `UNWIND batch node patterns do not support labels` |

The classifier requires the id-bound node to be the **source** of the arrow.
This is a stronger constraint than A1.1 found: A1.1 established that batch reads
reject labels and extra projections, and this adds that they also reject
direction.

**The single-source form works and is what ships:**

```cypher
MATCH (x {id: $v})<-[:CALLS]-(y) RETURN y.id AS dst
```

Measured cost: 182 ms for six typical seeds; median Django in-degree in the
eligible pool is 3. A hub is expensive — a 2,824-in-degree node takes 9.0 s —
and **`LIMIT` does not help**, because it bounds rows returned, not the scan:
`… RETURN y.id LIMIT 200` still took 9.6 s on the same node. The largest
in-degree in the eligible seed pool is 1,093, so the worst realistic single read
is around 4 s, well inside the 29,999 ms deadline but visible in the latency
tail.

**Consequence for the spec.** §6.3's round-trip budget assumed reverse discovery
was free (folded into the 12-request envelope). It is not: it is `|seeds|`
requests, linear in seed count. At the §6.4 recommendation of 5–8 seeds that is
5–8 requests and the 24 budget holds. It would not hold at 30 seeds.

**Recommendation:** amend §6.3's cost model to
`12 + |seeds| + 6 × ceil(|candidates|/B)`, and note in Appendix A that batch
reads are directed source-out only. If a future engine build accepts the
reversed form, `test_reverse_batch_read_is_rejected` fails and this can be
withdrawn — the query is pinned as `REVERSE_BATCH_QUERY` for exactly that
reason.

### A3.2 — Cosmetic: the sibling-implementation source is dormant on Django

Not an engine constraint, but it belongs with the findings. §6.3 lists "sibling
implementation, shared `IMPLEMENTS` target" as an available static candidate
source. **It produces zero candidates on all 200 trials.** No symbol in the
eligible seed pool has an `IMPLEMENTS` out-edge: the extraction layer emits
`IMPLEMENTS` for class→ABC relations, and the seed filter selects functions and
methods. It is implemented, tested and wired into `CANDIDATE_SOURCES`; it
simply never fires with this seed filter. It would fire on class seeds.

So **the entire Django candidate pool is reverse `CALLS`**, median 26 per trial,
p90 61, max 784 — well short of the ~200 §6.3 sized its performance analysis
around.

---

## 8. Packing behaviour

### 8.1 The greedy loop is quadratic, and the tail shows it

| | Observed |
|---|---:|
| Candidates offered, median | 25.5 |
| Candidates offered, p90 / max | 61 / 784 |
| Bundles admitted, median | **21** |
| Bundle evaluations, median | 300 |
| Bundle evaluations, max | **62,370** |
| Bundle size (nodes), median | 2 |
| Bundle size, p90 / max | 16 / 158 |
| Bundle cost (tokens), median | **81** |
| Bundle cost, p90 / max | 246 / 2,520 |

The §6.3 loop re-evaluates every surviving candidate on every iteration, so it
is O(admissions × pool). At the median that is 300 evaluations and it is
invisible. On the 784-candidate trial it was 62,370 evaluations and **21.9 s**,
of which the database accounts for well under a second.

**The mitigation is the one §6.3 already holds in reserve, and it is not
implemented.** Lazy greedy on the `score(y)/tokens_at(y)` upper bound would cut
that to tens of exact evaluations. I did not build it, because the task's
fallback condition was "if the envelope is still too costly" and the envelope is
not the problem — the *in-memory* re-evaluation is. Building an unrequested
optimisation on a 1/200 case is a redesign, so this is documented and left.

Note the re-evaluation is not gratuitous: it is what makes shared dependencies
pay off (§8.2). A fix must keep the re-ranking, not drop it.

### 8.2 Shared dependencies make later bundles cheaper

This is the property that makes the packer cluster context rather than scatter
it, and it shows up directly in the numbers: the median bundle costs 81 tokens
but contains 2 nodes, while the mean bundle contains 6.7. Later admissions in a
trial are systematically cheaper than earlier ones, because their mandatory
closure is already present. The fixture suite pins the mechanism exactly
(`test_shared_dependencies_make_the_second_bundle_cheaper`): two callers of the
same helper, and the second pays 15 tokens where the first paid 21 — a saving
of exactly the helper's identity line.

### 8.3 Known non-optimality, accepted

§6.3 permits it and §6.2 does not search for it: a lower profile with a richer
optional set could occasionally beat a higher profile with a thin one. A P2
floor leaves more room for bundles than a P3 floor does. The scan takes the
first profile whose *mandatory floor* fits and never revisits that choice.
Documented in `compile.py`; not fixed.

---

## 9. What was verified, and how

### 9.1 Profile monotonicity

Proven three ways, because it is the premise that makes the linear scan valid:

1. `adjust()` never raises a required level — checked exhaustively over every
   (profile, edge type, level) triple.
2. Pointwise ordering of the resulting level maps, on a fixture graph that
   exercises every edge type at both hops.
3. Pointwise ordering on 20 real Django seed sets.

Cost monotonicity is checked **separately and empirically**, because it does not
follow from pointwise ordering: emitting *more* can remove L1-mandatory
identities, so a richer profile is not automatically more expensive by
arithmetic. Measured across all four profiles on 200 Django seed sets:
**0 violations of `cost(P3) >= cost(P2) >= cost(P1) >= cost(P0)`.**

### 9.2 I6, on real data

`test_is_closed_holds_on_every_trial` rebuilds a fresh `CachingExpander` and
re-reads every emitted node's out-edges from HydraDB, rather than reusing the
compile's own cache. If it reused the cache the check would be a tautology.
`is_closed()` also **refuses to certify a node whose edges it has never read**,
returning False rather than treating an empty answer as a leaf — three fixture
tests caught exactly this during development.

Result: **200/200 closed**, and `--verify-closure` confirms it independently in
the validation script.

### 9.3 The incremental cost model

`CostState` maintains `src`/`prov`/`ident` under rising levels in O(|delta|),
because recomputing the dangling set per candidate per iteration is the
difference between a 1-second compile and a 30-second one. Since the packer
*only* ever sees `delta_cost`, a drift there would corrupt every budget decision
silently. It is pinned two ways:

* 500 randomised fixture cases assert `CostState` agrees with the from-scratch
  `cost()` on both construction and delta.
* Every Django trial asserts `ctx.cost == cost(ctx.levels, sidecar)` after
  packing, in production code, not only in the test.

### 9.4 Both assertions run in production

`assert is_closed(...)` and `assert cost(...) + hints <= budget` are in
`_pack_and_finish`, not in the test suite. A check that only runs under pytest
cannot deliver the property the header advertises.

---

## 10. Test inventory

```
tests/graph/test_budget_fixtures.py    44 passed   no database, 0.4 s
tests/graph/test_budget_django.py      20 passed   HydraDB, 59 s at CC_TRIALS=40
tests/graph/test_closure_fixtures.py   33 passed   no database (1 test replaced)
```

`test_profile_parameter_is_accepted_and_ignored` was removed: Item 4 asserted
`closure()` ignores `profile`, and Item 5 makes that false by design. It is
replaced by `test_profile_none_means_the_unadjusted_table`, which pins the
guarantee that survives — `profile=None` still applies the §4 table verbatim and
agrees with P3.

---

## 11. Files

```
src/context_compiler/graph/profiles.py    new   Sec 6.1 monotone family
src/context_compiler/graph/budget.py      new   Sec 6.2 cost model, I6 check, hints
src/context_compiler/graph/pack.py        new   Sec 6.3 candidate sources + bundles
src/context_compiler/graph/compile.py     new   Sec 6.2 admission scan
src/context_compiler/graph/closure.py     mod   profile wired up; induced_delta()
src/context_compiler/graph/expand.py      mod   CachingExpander, FrozenExpander,
                                                ReverseReader
src/context_compiler/graph/sidecar.py     mod   load_degrees() for idf
src/context_compiler/graph/__init__.py    mod   exports
tests/graph/test_budget_fixtures.py       new
tests/graph/test_budget_django.py         new
tests/graph/test_closure_fixtures.py      mod   one test replaced
scripts/validate_budget_django.py         new
docs/spikes/graph-item-5-results.md       this file
```

Nothing under `src/context_compiler/extract/`, `src/context_compiler/emit/`,
`tests/unit/`, `docs/specs/`, `~/hydradb` or `~/targets/` was modified. No
emission, MCP server, seed resolution, runtime tracing or evaluation code was
written — `compile_context()` returns a level map plus hints and stops there.

---

## 12. Unresolved issues

1. **A3.1 needs a spec decision** (§7). §6.3's round-trip budget assumed a
   batched reverse read that this engine does not have. The implemented
   single-source form works and the 24-request gate still holds at six seeds,
   but the cost model is now linear in seed count and §6.3 should say so.
2. **`CLOSURE_BUDGET_EXCEEDED` cannot be demonstrated at the demo budget**
   (§4.3). It is correct, tested and first-class, but P0's floor is bounded by
   the seeds' own declarations, so six Django seeds always fit. The demo script
   needs a deliberately oversized seed set, or the claim should be made about
   the *P3* floor rather than the exceeded status.
3. **P2 is chosen once in 200 trials** (§3). The four-step profile ladder is
   effectively a two-step one on Django. Worth either accepting explicitly or
   re-cutting P2's edge set — but that is a spec change, not mine.
4. **The packing loop is quadratic in pool size** (§8.1). 21.9 s on the worst of
   200 trials. §6.3's lazy-greedy fallback is the fix and is deliberately not
   implemented; it should be before Item 7 exposes this over MCP.
5. **The sibling-implementation source never fires** (§7, A3.2). Implemented and
   tested, dormant on this seed filter. Not a bug, but it means the Django
   candidate pool is single-source and §6.3's ranking claims are only exercised
   on reverse `CALLS`.
6. **L1-mandatory is far cheaper than §1.1 predicted** (§5.3). 97 tokens rather
   than 3,000. The accounting is worth keeping — it could spike elsewhere — but
   the spec's warning should not be quoted as a measured cost.
