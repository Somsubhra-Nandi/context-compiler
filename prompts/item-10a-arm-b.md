# Prompt: Item 10a — Baseline Arm B, with Arm A as a time-boxed stretch

**Read first:** `docs/specs/context-compiler-v1.3.md` §9.1–§9.3,
`src/context_compiler/graph/compile.py` (`Compiler.compile_context`),
`src/context_compiler/graph/budget.py` (`cost`), `src/context_compiler/emit/render.py`
(the `ContextLike` protocol), `docs/specs/amendment-a6.md` §A6.6.

**Time-box: 4 hours.** Task 0 ≈ 30 min. Arm B ≈ 2 h. Arm A is a stretch with a
hard stop — see Task 3. Stop and report rather than working around.

**Why this exists.** The project has no comparison arm. Spec §9.1 lists four;
this session builds one and possibly two. Arm B (graph, ranked, no closure) is
the load-bearing one: without it a win means "graphs help," not "closure
helps," and closure is the thesis.

---

## Task 0 — Three corrections (do first, commit separately)

**0.1 — The `SymbolMeta` contract break.** Item 8 added `start_line` and
`end_line` to `SymbolMeta` for traceback line mapping, which broke
`tests/graph/test_sidecar.py::test_field_set_matches_amendment_a1_1` — a
committed contract test asserting A1.1's exact field tuple.

Raise this as **Amendment A7**, in `docs/specs/amendment-a7.md`: what fields
were added, which item required them, why they belong in the sidecar rather
than the graph (A2's reasoning applies — scalars in the sidecar, topology in
the graph), and confirmation that adding them does not change `cost()` or any
token figure. *Then* update the test to the new tuple, citing A7. Do not edit
the assertion without the amendment; a falsified contract gets recorded, not
quietly re-fitted.

**0.2 — Reconcile "emitted" against "named" in A6.6.** Two figures for the same
context look like an error and will be read as one:

- `tests/graph/test_budget_django.py` at `CC_TRIALS=200`: floor symbols median
  **36**, final **71.5**
- A6.6: floor emitted **21**, compiled emitted **34**

They agree exactly on tokens (floor 3,718.5, final 5,602.0, utilisation 0.700),
so nothing is wrong: 36 counts all closure members including L1 identities,
21 counts L2+L3 only. A6.6's own level composition confirms it (6 L3 / 16 L2 /
15 L1 = 22 emitted, 37 total).

Add a short note to A6.6 stating both counts, naming which is *emitted* and
which is *named*, and stating which the README quotes for each row. This
distinction is the product's central idea, so present it deliberately rather
than letting two numbers collide.

**0.3 — Correct the candidate-supply figure.** A 40-trial run showed candidates
median 8.0 / admitted 8.0. At 200 trials it is **12.0 / 11.0**. Any note
claiming the packer admits *every* candidate is wrong; it admits *nearly* all.
The accurate claim — candidate supply, not budget, is the binding constraint at
~70% utilisation — still holds and is the stated justification for cutting
Item 9. Fix any place the 8/8 figure was recorded.

---

## Task 1 — Arm B: graph top-k, no closure

**The idea.** Same seeds, same graph, same budget, same `cost()`, same emitter.
Rank graph neighbours by relevance and admit until the budget fills — but
**never run the closure fixpoint**. Whatever it emits is what similarity-free
graph ranking gives you without structural closure.

**Where to build it.** `src/context_compiler/baseline/arm_b.py`. Do **not**
modify `graph/compile.py`, `graph/closure.py`, or `graph/pack.py`. Arm B is a
separate entry point reusing them read-only, so the compiler under measurement
is byte-identical to the shipped one.

**Requirements:**

1. **Seeds.** Accept `list[int]`, identical to what `mcp/seeds.py` returns.
   Emit every seed at L3, matching the compiler's treatment.
2. **Expansion.** One-hop neighbours over the same edge types the compiler's
   `PROPAGATION` table covers, ranked. Use the existing `idf` ranking that
   `impact_cone` already uses — do not invent a new scorer. **Do not iterate to
   a fixpoint and do not apply level propagation.** Admit each ranked neighbour
   at L2 (declaration) as a plain top-k retriever would.
3. **Budget.** Import `cost()` unchanged, framing terms included. Same 8,000
   default. Same `HINT_RESERVE` handling — or, if you exclude the reserve,
   state that and justify it.
4. **Greedy fill.** Continue past a candidate that does not fit and admit
   smaller ones further down the ranking. Stopping at the first overflow
   understates the arm and is a strawman.
5. **Emission.** Reuse `emit()` via the `ContextLike` protocol — it takes a
   protocol, not the concrete `Context`, so a duck-typed object works without
   forking the emitter. Populate `provenance` with a real `Reason` per admitted
   node so the emitted text shows *why* each block is present. With empty
   provenance `_classify()` routes everything into the optional section, which
   would misrepresent the arm.
6. **No closure, enforced.** Assert in code that the fixpoint is never entered.

**Report, over the same 200 trials, same pool of 1,891, `rng_seed=20260817`,
6 seeds, 8,000 budget, against the compiler:**

- emitted symbols and tokens (both counts per 0.2), utilisation
- **dangling references** — symbols referenced by emitted text but not emitted
  or named. `mandatory_identities()` in `graph/budget.py` computes this and
  works on any level map. **This is the decisive metric.** Arm B will fill more
  of the budget; the claim is that it leaves references unresolvable. Report the
  distribution for both arms.
- `is_closed()` rate for Arm B. Expect well under 100%. Per §9.3 this is a
  soundness check on our own pipeline — report it for Arm B because Arm B is
  *our* graph, and do **not** score a vector arm on it.
- round trips and latency

---

## Task 2 — One side-by-side, captured to disk

Emit both arms in full for a single task, same seeds, to
`docs/spikes/baseline-arm-b-example.md`. Pick the task by a rule fixed before
looking at results — e.g. the trial whose compiler closure size is closest to
the post-A6 median of 36. State the rule.

This file is the video's source material. It needs to show the compiler
resolving its references and Arm B not.

---

## Task 3 — Arm A (vector), stretch only

Attempt **only if Arm B is complete and committed**, and with a hard stop:

- **20 minutes** for `sentence-transformers` plus a pinned code-embedding model
  under the 10 GB WSL cap. If the install or download is not working at 20
  minutes, **abandon Arm A and report it**. Do not debug it.
- Chunk = one symbol from `symbols.jsonl`, via the existing offset index. Do not
  build a sliding-window chunker; symbol-aware chunking is what makes `cost()`
  reusable and the comparison controlled.
- Cache embeddings to disk keyed by `(model_id, revision, node_id)`.
- Embedding run goes to a log. Read the log. No polling loop.
- Same budget, same `cost()`, same emitter, greedy fill, real embeddings.

If Arm A is abandoned, the README states the comparison is against graph
ranking without closure, and names vector top-k as the obvious next arm. A
limitation you understood and stated is not a weakness.

---

## Deliverables

- `docs/specs/amendment-a7.md`; `test_sidecar.py` updated citing A7
- A6.6 note on emitted vs named; 8/8 figure corrected
- `src/context_compiler/baseline/arm_b.py` + unit tests
- `docs/spikes/baseline-arm-b-results.md` — the 200-trial table, the dangling
  reference distributions, what was cut and why
- `docs/spikes/baseline-arm-b-example.md` — the side-by-side
- Report the full test count and **which suites ran**. Run
  `python -m pytest tests/unit tests/mcp tests/integration tests/graph -q`.
  The last two sessions reported ~129 passing because `tests/graph` (167 tests)
  was never collected, which hid the 0.1 contract failure.

## Constraints

- Never substitute Neo4j or mock HydraDB.
- Do not modify `compile.py`, `closure.py`, or `pack.py`.
- No background polling loops or scheduled wakeups.
- Report numbers as measured. No tuning to preserve a prior figure.
- If Arm B is not green at the 3-hour mark, commit what works and report which
  requirement is incomplete.