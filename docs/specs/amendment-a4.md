# Amendment A4 to Implementation Spec v1.3

**Status:** Adopted. Raised by Item 6 (`docs/spikes/emit-item-6-results.md`) and follow-up work before Item 7.
**Scope:** §6.2 (cost model), §3.2 (MRO flattening), §6.1 (stated in passing). No architectural change. Invariants I1–I6 unaffected; I4 is the invariant this amendment repairs. All three sub-items were validated against a real HydraDB instance and the real Django extraction, not simulated.

Item 6 found `cost()` under-counting model-visible framing (I4 violated on 11 of 50 Django contexts), left three stale `static_value` properties in the graph that only a reset can clear, and flagged that MRO flattening was emitting operator-overload noise inherited from base classes. All three are closed here.

---

## A4.1 — framing term in §6.2's `cost()`

### What was wrong

`cost()` charged a flat `HEADER_TOKENS = 40` for "the context header" and nothing for the per-file group headers §7.1's grouping produces. Item 6 measured this as an 11/50 I4 violation, median +341 tokens, worst case 8,465 of an 8,000-token budget. `src/context_compiler/emit/render.py` already carried a passive, unwired diagnostic (`FRAMING_PER_EMITTED = 6`, `FRAMING_PER_FILE = 13`, `FRAMING_FIXED = 40`) fitted against those same 50 contexts, but nothing in admission read it.

### Decision

`file: str` was added to `SymbolMeta` (`src/context_compiler/graph/sidecar.py`), read straight from the `file` key `symbols.jsonl` already carries — no re-extraction needed. `graph/budget.py`'s `cost()` now charges:

```python
framing = FRAMING_PER_EMITTED * len(emitted) + FRAMING_PER_FILE * len(distinct_files) + FRAMING_SAFETY
# FRAMING_PER_EMITTED = 6, FRAMING_PER_FILE = 13, FRAMING_SAFETY = 100
```

replacing the flat `HEADER_TOKENS`. `emitted` is every node at level ≥ L2; `distinct_files` is the set of `meta.file` values among them. `CostState` — the incremental model the packer's greedy loop actually calls — was given the same accounting (`_files: Counter[str]`, only ever grows, since levels only rise): `delta_cost()` and `apply()` now include the marginal framing a bundle introduces (`FRAMING_PER_EMITTED` per newly-emitted node in the bundle, `FRAMING_PER_FILE` for each file the bundle touches for the first time). This is load-bearing, not cosmetic: if the incremental model under-tracked framing relative to the from-scratch `cost()`, the packer could over-admit and the production `assert cost(merged) + hints <= budget` in `compile.py` would raise, not just under-report.

`FRAMING_SAFETY = 100` was specified rather than re-derived, and dedup savings are deliberately **not** credited back — the source-token over-count Item 6 measured (median 591 tokens, max 2,550) and the framing under-count are left to partially offset each other, with the conservative side (no dedup credit) absorbing tail variance, exactly as instructed.

The now-redundant `framing_allowance()` diagnostic in `emit/render.py` (and `EmittedContext.framing_allowance` / `within_framing_allowance`) was removed rather than kept as a second, looser bound — once `token_margin <= 0` is a hard invariant, a separate "stays under some allowance" check adds nothing. The two `xfail(strict=True)` tests that recorded the known I4 violation (`tests/unit/test_emit.py::test_token_margin_is_not_positive[_multi_seed]`) and the Django-graph counterpart (`tests/graph/test_emit_django.py::test_token_margin_is_not_positive`) are now plain hard assertions.

### Validation — 200 Django contexts, clean post-reset graph (§A4.2)

`scripts/validate_emit_django.py`, same seed filter / `rng_seed=20260817` / 6-seeds-per-trial contract as Items 4–6, 8,000-token budget:

| | Value |
|---|---:|
| `token_margin`, min / median / p90 / max | −2,319 / **−707** / −356 / **−85** |
| `margin_fraction`, median | **−10.0%** |
| Contexts with `token_margin > 0` | **0 / 200** |
| I6 (`is_closed` on every trial) | **200 / 200** |
| Compile + emit wall time, 200 trials | 415.01s (~2.08s/trial) |

**I4 holds on every trial with `FRAMING_SAFETY` left at the specified 100** — no increase was needed. The margin is not tight: median utilisation moved from Item 5's ~95% to roughly 90%, because two conservative choices now stack (the framing safety margin, and the un-credited dedup over-count Item 6 measured, worst case −26.2% on one context). That headroom is the cost of the guarantee, not a bug.

Profile hit rate, same 200 seed sets, compared to Item 5/6's baseline:

| Outcome | Before (Item 5/6) | After (A4.1) |
|---|---:|---:|
| `OK` (P3) | 183 (91.5%) | **177 (88.5%)** |
| `DEMOTED:P2` | 1 (0.5%) | 1 (0.5%) |
| `DEMOTED:P1` | 16 (8.0%) | **22 (11.0%)** |
| `DEMOTED:P0` | 0 | 0 |
| `CLOSURE_BUDGET_EXCEEDED` | 0 | 0 |

**The P3 hit rate dropped by exactly 3 points, and it was not tuned back.** All 6 trials that moved did so from P3 straight to P1 — none landed in P2 or P0 — consistent with A3.3's finding that the visible ladder is effectively P3 → P1. This is the honest cost of closing an I4 violation the spec's own header claim depends on (`Structural closure: complete` is a lie if the token count next to it is wrong), reported per §9.5 rather than adjusted to preserve the old figure.

---

## A4.2 — reset and re-ingest

### What this closed

Item 6 §6.1 found three Django constants (`INVALID_URLS`, `LANG_INFO`, `VALID_URLS`) whose `static_value` shrank under the A2.2 cap on re-extraction, but the engine can neither `REMOVE` nor null a property (`Neo.DatabaseError` / parse rejections on all three attempted forms), so the stale oversized values from before A2.2 landed stayed in the graph until a full reset. No code path reads `static_value` from the graph — everything goes through the sidecar — so this was stale data, not a functional defect, but it made the on-disk graph disagree with `symbols.jsonl`.

### Decision and result

`bash scripts/run_hydradb.sh reset` followed by the standard ingest command, run twice in this session (once before the A4.1 validation, once after A4.3's re-extraction):

| | First run (pre-A4.1 validation) | Second run (post-A4.3 re-extraction) |
|---|---:|---:|
| Ingest wall time | 264.74s | 246.80s |
| `:Symbol` / `:Test` | 43,420 / 21,966 | 43,420 / 21,966 |
| `static_value` rows written | 1,435, **0 oversize skips** | 1,435, 0 oversize skips |
| Edge counts | identical to Item 3–4/6 baseline (95,288 `CALLS`, 123,907 total) | identical |

1,435 `static_value` rows with zero skips confirms the three previously-oversized properties are gone — a reset was in fact required, a plain re-ingest over the populated graph would not have cleared them, matching Item 6's diagnosis exactly. The A4.1 validation above ran against this clean state.

---

## A4.3 — MRO flattening emits too much

### What was wrong

In the Item 6 worked example, `ColPairs` cost 1,362 tokens and `WhereNode` cost 555 — together 21.5% of an 8,000-token context — and the bulk of both was dunder and internal methods inherited from `Combinable` and `tree.Node` (`__rxor__`, `bitleftshift`, `__rmod__`, `__eq__`, `__hash__`, `__copy__`, …). None of it describes what `build_filter` does; it is Python's operator-protocol plumbing riding along because `flatten_members()` (`src/context_compiler/extract/mro.py`) kept every member of every base in the MRO, deduped only by name collision.

### Decision

`flatten_members()` now drops a member during MRO flattening when **both** conditions hold: it is inherited (`owner != cls`) **and** its name is a dunder or a single-underscore private (`name.startswith("_")`). A class's own members are never filtered, regardless of name — `ColPairs.__init__`, `__len__`, `__iter__`, `__repr__` all survive because `owner == cls`. Non-underscore aliases Django itself defines for operator methods (`bitand`, `bitor`, `bitxor`, `bitleftshift`, `bitrightshift`) are also kept when inherited, since they don't start with `_` — the filter is on naming convention, not on being an operator method.

Four new fixture tests in `tests/unit/test_mro.py` pin the four cases (inherited dunder dropped, inherited private dropped, inherited public kept, a class's own dunder/private kept even though the identical name would be filtered if inherited); the pre-existing three tests (`test_diamond`, `test_three_deep_chain`, `test_unresolvable_base_is_partial`) needed no changes.

### Re-extraction and re-ingest

Re-running extraction over Django with the fix in place (`--no-reindex`, reusing the existing SCIP index — MRO flattening is pure post-processing over already-resolved bases/members, not a change to what SCIP/AST extract) produced **identical** symbol and edge counts to the pre-fix extraction (43,420 symbols, 123,907 edges, same per-type breakdown): the fix changes which lines render inside `repr_L2_text` for classes with inherited members, nothing about the extraction graph itself. Re-ingest (reset + full load) completed in 246.80s with the same read-back counts as A4.2.

### Worked example, regenerated

Same six seeds, same 8,000-token budget, against the graph with both A4.1 and A4.3 live (verbatim output: `docs/spikes/amendment-a4-example.md`):

| | Before (Item 6) | After (A4) |
|---|---:|---:|
| `ColPairs` — members / tokens | 69 / **1,362** | 39 / **684** (**−678, −49.8%**) |
| `WhereNode` — members / tokens | 37 / **555** | 27 / **376** (**−179, −32.3%**) |
| Combined | 1,917 tokens | 1,060 tokens (**−857, −44.7% of their own footprint**) |
| Closure size | 92 symbols | 116 symbols |
| Emitted symbols | 46 | 50 |
| Emitted tokens | 7,724 | 7,134 (**−590**) |
| Budgeted tokens | 7,954 | 7,951 |
| `token_margin` | −230 | −817 |
| Mandatory identities / hints | 3 / 23 | 3 / 25 |
| Dedup saved | 604t / 57 lines | 713t / 69 lines |

**The 857-token saving on `ColPairs` + `WhereNode` is a clean, isolated measurement of A4.3** — MRO flattening touches only which member lines are rendered for a class, never `cost()` or the packer, so those two per-symbol deltas are uncontaminated by A4.1. The aggregate row (closure/emitted/margin) is **not** a clean A4.3-only measurement, because both amendments are live in the same rebuild and a third rebuild with only one fix applied was not worth the ~250s ingest + re-extraction cost to produce: the freed budget from A4.3 let the packer admit 4 more optional bundles (46→50 emitted, 92→116 closure) rather than banking all 857 tokens as slack, while A4.1's framing term independently pushed the margin more negative across the board (see the −707 median in the 200-trial run above). Both effects move in directions consistent with their individual mechanisms; they were not disentangled further because doing so wasn't asked for and the per-symbol numbers already isolate the claim A4.3 makes.

---

## Summary

| Section | Change |
|---|---|
| §6.2 | `cost()`'s flat `HEADER_TOKENS = 40` replaced by `6·emitted + 13·distinct_files + 100` (A4.1). `CostState` tracks the same term incrementally. |
| §2.1 (sidecar) | `SymbolMeta` gains `file`, loaded from `symbols.jsonl`'s existing `file` field. |
| §6.1 | Three stale `static_value` properties (Item 6 §6.1) cleared by a full reset + re-ingest; no code path read them, so this was cosmetic-but-real (A4.2). |
| §3.2 | `flatten_members()` drops inherited (`owner != cls`) dunder/private members; a class's own members are never filtered (A4.3). |
| Tests | Two `xfail(strict=True)` I4 tests (`test_emit.py` ×2 concept, `test_emit_django.py` ×1) are now hard assertions. Four new `test_mro.py` cases pin the A4.3 filter. `test_budget_fixtures.py`'s exact-value assertions were recomputed against the new framing formula; one new test (`test_cost_charges_a_framing_term_per_distinct_file`) isolates the per-file term. |

### Measured figures for the record

```
A4.1  FRAMING_SAFETY               100 (unchanged from spec; sufficient — 0/200 violations)
A4.1  token_margin, 200 trials     median -707, max -85, 0/200 positive
A4.1  P3 hit rate                  88.5% (was 91.5%), P1 11.0% (was 8.0%), P2/P0/EXCEEDED unchanged
A4.1  I6 closure                   200/200
A4.2  reset+ingest wall time       264.74s / 246.80s (two runs this session)
A4.2  static_value rows            1,435, 0 oversize skips (clean)
A4.3  ColPairs                     1,362 -> 684 tokens (-49.8%), 69 -> 39 members
A4.3  WhereNode                    555 -> 376 tokens (-32.3%), 37 -> 27 members
A4.3  worked example, emitted      7,724 -> 7,134 tokens (-590, confounded with A4.1)
```
