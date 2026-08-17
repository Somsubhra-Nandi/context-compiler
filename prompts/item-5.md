# Task: Item 5 — Budget Admission and Closure-Preserving Packing

You built Items 0, 3 and 4. This is the last algorithmically substantial piece, and it is on the critical path.

---

## 0. Source of truth

```
docs/specs/context-compiler-v1.3.md    §1.1, §6.1, §6.2, §6.3, invariants I4 and I6
docs/specs/amendment-a1.md
docs/specs/amendment-a2.md             ← new, read it
docs/spikes/graph-item-3-4-results.md  ← your own measurements
```

Do not redesign. Document, stop, escalate.

## 1. Scope

You own `src/context_compiler/graph/`, `tests/graph/`, `scripts/`, `docs/spikes/graph-item-5-results.md`.

You must not touch `src/context_compiler/extract/`, `tests/unit/`, `docs/specs/`, `~/hydradb`, `~/targets/`, or anything Codex is building under `src/context_compiler/emit/`.

You must not implement emission (Item 6, Codex is on it now), the MCP server (Item 7), seed resolution (Item 8), runtime tracing (Item 9) or evaluation (Item 10). **Stop at a working `compile_context()` that returns a level map plus hints — no rendered text.**

## 2. Calibration from your own Item 4 data

```
median mandatory floor   3,026 tokens        P3 fits ~95% of tasks
p90                      6,832 tokens
over 8,000               10/200  (5%)        demotion fires here
over 16,000               2/200  (1%)        CLOSURE_BUDGET_EXCEEDED reachable
level composition        6.0 L3 -> 17.0 L2 -> 29.8 L1
median emitted set       22 symbols
```

**Demo budget is 8,000 tokens.** At that budget every path in §6.2 is exercised without engineering it: P3 usually fits, demotion is rare but real, the exceeded case exists. Optional packing fills roughly 62% of the average context, so it is the main event, not a corner case.

## 3. Cost model (§1.1, §6.2, I4)

`cost()` must charge **everything the model will see**:

```python
HINT_RESERVE  = 0.05
HEADER_TOKENS = 40

def cost(level_map) -> int:
    src   = sum(tok_L3(n) if lv == L3 else tok_L2(n) if lv == L2 else 0
                for n, lv in level_map.items())            # L1 costs 0 here
    prov  = sum(provenance_tokens(n) for n, lv in level_map.items() if lv >= L2)
    emitted  = {n for n, lv in level_map.items() if lv >= L2}
    dangling = union(refs_at(n, lv) for n, lv in level_map.items()) - emitted
    ident = sum(identity_tokens(n) for n in dangling)       # L1-mandatory, never truncated
    return src + prov + ident + HEADER_TOKENS
```

### The three L1 tiers — get this right, it is where v1.2 was wrong

| Tier | Definition | Cost | Truncatable |
|---|---|---|---|
| **L1-lattice** | every symbol the fixpoint assigns L1 | 0 | never emitted |
| **L1-mandatory** | subset whose FQN appears **textually** in emitted L2/L3 | **budgeted** | **never** |
| **L1-hints** | anything else worth listing | reserve | yes |

L1-mandatory is computed from `repr_L2_refs` / `repr_L3_refs` in the sidecar — set algebra, no round trips, no source reads. It is an upper bound because emission-time dedup can only shrink it.

Expect this to bite: ~30 L1 identities per closure at ~10 tokens each is 300 tokens, and the number can spike.

## 4. Profiles (§6.1)

| Profile | Seeds | 1st hop | 2nd hop |
|---|---|---|---|
| **P3 FULL** | L3 | L2 | L1 |
| **P2 COMPACT** | L3 | L2 direct callees/types, L1 otherwise | L0 |
| **P1 MINIMAL** | L3 | L1 | L0 |
| **P0 FLOOR** | L2 | L1 | L0 |

Each profile's level assignment must be **pointwise ≤** the previous. Assert this in a test — it is what makes the linear scan in §6.2 valid.

`closure()` currently accepts and ignores `profile`. Wire it up via `profile.adjust(edge_type, required_level)`. Do not change the propagation table.

## 5. Selection (§6.2)

```python
def compile_context(task_seeds, budget):
    hint_reserve = int(budget * HINT_RESERVE)
    effective    = budget - hint_reserve
    for profile in (P3, P2, P1, P0):
        c = closure(task_seeds, expand, profile)
        if cost(c) <= effective:
            merged = pack(effective - cost(c), c, profile)      # §6.3
            hints  = identity_hints(merged, cap=hint_reserve)
            assert is_closed(merged)                            # I6
            assert cost(merged) + tokens(hints) <= budget        # I4
            return Context(merged, hints, profile,
                           status="OK" if profile is P3 else f"DEMOTED:{profile}")
    return Context(status="CLOSURE_BUDGET_EXCEEDED",
                   deficit=cost(closure(task_seeds, expand, P0)) - effective,
                   suggestion="narrow the task or raise the budget")
```

`CLOSURE_BUDGET_EXCEEDED` is a **first-class return value**, not an exception. It is also a product feature: *"your task's mandatory floor is 22k tokens; it is too broad for one shot."* No top-k system can say that.

Both asserts run in production, not just in tests. The whole point of I6 is that the header stops lying.

## 6. Bundle packing (§6.3, invariant I6) — the core of this item

**Nothing is emitted at L2 or L3 without its induced mandatory closure.**

### Candidate pool — static only, for now

| Candidate | Level | Source | Available? |
|---|---|---|---|
| Static caller of a seed | L2 | reverse `CALLS` | **yes** |
| Sibling implementation | L2 | shared `IMPLEMENTS` target | **yes** |
| Covering test | L3 | `COVERS` | **Item 9 — not yet** |
| Runtime-observed caller | L2 | `OBSERVED_CALLS` | **Item 9 — not yet** |

Build the pool behind a `candidate_sources` list so Item 9 plugs in without restructuring. Reverse `CALLS` needs a reverse batch read — same amended A1.1 shape, arrow reversed. Verify the engine accepts `MATCH (x {id: row.v})<-[:CALLS]-(y)`; if it rejects reversed patterns, report and stop rather than working around it.

### Bundles, and making them affordable

```python
def bundle(current, cand, lvl, profile):
    expanded   = closure(current | {cand: lvl}, expand, profile)
    delta      = {n: l for n, l in expanded.items() if l > current.get(n, L0)}
    delta_cost = cost(expanded) - cost(current)
    return delta, delta_cost

value(cand) = score(cand) / delta_cost(cand | current)
```

Naive recomputation is ~200 candidates × 12 round trips = 2,400 requests per compile. **Unusable.** Do this instead:

**Precompute the candidate envelope.** Fetch the mandatory neighbourhoods of *all* candidates in one extra pass — 12 more batched requests — then every bundle computation is in-memory set algebra with zero further round trips. Target total: **24 batched requests per compile**.

Fallback if the envelope is still too costly: lazy greedy. Note `delta_cost(y|S)` is non-increasing in `S`, so a stale value is a *lower* bound and inverts standard CELF pruning. Use `score(y)/tokens_at(y)` as the heap priority — since `tokens_at(y) ≤ delta_cost(y|S)` always, it is an admissible **upper** bound. Compute the exact bundle only at the top of the heap, then re-heap.

### Scoring

```
score(y) = relevance(y) × idf(y) × confidence(y)
idf(y)   = log(N / (1 + degree(y)))
```

`idf` is what stops `Model`, `QuerySet` and `Field` dominating — Django's max out-degree is 175. Representation decay limits their *token* damage, not their *score*, and those are separate problems.

> **Do not blur:** closure inclusion is decided by semantic rules; ranking by scores. A high score never forces inclusion; a mandatory rule never consults a score. I6 does not violate this — a bundle's mandatory members are included by *rule*, triggered by a candidate that scoring merely proposed.

Because bundles share dependencies, admitting one candidate makes the next cheaper. That is a feature: the packer clusters context around a coherent region of the graph rather than scattering it.

**Known non-optimality, accepted:** a lower profile with a richer optional set could occasionally beat a higher profile with a thin one. §6.2 does not search that space. Document; do not fix.

## 7. Tests

**Fixtures, no database** (`tests/graph/test_budget_fixtures.py`):
- profile monotonicity: every profile pointwise ≤ the previous
- `cost()` charges source, provenance, mandatory identities, header — and zero for L1-lattice
- a bundle admitted at L3 pulls its L2 dependencies; `is_closed()` holds after
- shared dependencies make the second bundle cheaper than the first
- budget exactly consumed: no overrun by one token
- `CLOSURE_BUDGET_EXCEEDED` returns with a correct `deficit`, does not raise
- `idf` suppresses a synthetic 500-degree hub

**Django, live** (`tests/graph/test_budget_django.py`), 200 trials × 6 seeds, same seed filter and `rng_seed` as Item 4:
- P3 satisfied on roughly 95% of trials — report the actual figure
- demotion fires on roughly 5%
- `is_closed(merged)` **100%**, no exceptions
- `cost(merged) + hints ≤ budget` **100%**
- round trips per compile ≤ 24 median
- report the distribution of final context size and token utilisation

## 8. Results doc

`docs/spikes/graph-item-5-results.md`: profile hit rates at 8,000 tokens; token utilisation (how much of the budget actually gets used); round trips per compile; candidate pool sizes; before/after context size from packing; the L1-mandatory cost distribution; and any new engine constraint.

**One number matters most for the writeup:** how many symbols end up in a compiled context versus the mandatory floor alone. That is the packing story.

## 9. Time box and gate

Fixtures green: 1.5 h. Django validation: 1 h. Overrun → report and stop.

Gate: both asserts hold on all 200 trials; profile monotonicity proven; round trips ≤ 24; every discrepancy documented. Commit after fixtures and again after Django. Do not start Item 6 or 7.
