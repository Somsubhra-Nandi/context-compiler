# Amendment A6 to Implementation Spec v1.3

**Status:** Adopted. Supersedes A5.4's "defer the root cause" decision. Raised
by `docs/specs/amendment-a5.md` (A5.2/A5.3), reconsidered before Item 8.
**Scope:** `extract/ast_occurrences.py` only, plus a full re-extraction and
re-ingest of the Django graph. No architectural change; I1-I6 unaffected. This
amendment does not touch `graph/pack.py` -- A5.1's containment filter stays,
now largely (not entirely, see A6.4) redundant for the fabricated-edge case it
was written for, and still correct defense-in-depth for any legitimately
contained-but-cheap candidate.

A5.4 deferred the root-cause fix as disproportionate to a packing amendment.
That calculus changes once Item 8 is imminent: every class and module node in
the current graph carries fabricated edges, A5's own headline numbers were
measured on that graph, and the fix only gets more expensive once Item 8 seeds
classes, Item 9 re-ingests on top of it, and Item 10 evaluates against it.
Fixing it now, once, is cheaper than fixing it later against more accumulated
state. This amendment does the fix, the re-extraction, and the re-validation,
and reports what moved -- including where it moved in a direction A5 did not
anticipate.

---

## A6.1 -- The fix: a hand-written walker that actually stops

`occurrence_nodes()` (`extract/ast_occurrences.py`) used `ast.walk(root)` with
a `continue` intended to stop at a nested `FunctionDef`/`AsyncFunctionDef`/
`ClassDef`. A5.2 established why that never worked: `ast.walk` enqueues every
descendant up front, so `continue` only skips classifying the node it was
given -- it cannot stop already-queued grandchildren from being yielded.

The fix replaces the walk with a real recursive descent that refuses to
recurse past a nested def:

```python
def _own_scope(node: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        out.append(child)
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.extend(_own_scope(child))
    return out
```

`occurrence_nodes()` now classifies a root's own bases/decorators/annotations
directly (exactly the cases the old `node is root` branch already handled
correctly) and then scans only `_own_scope(root)` for `Call` nodes. A class's
own scope is its decorators, bases and class-level statements; a module's own
scope is its imports, module-level statements and decorators; a function's
own scope is its own signature and body -- none of the three include anything
nested inside a def or class they contain, because that nested thing has its
own `Symbol` and its own scan.

**Regression tests**, `tests/unit/test_ast_occurrences.py` (8 new, all
passing): the exact A5.2 failing case (`class Foo: def method_a(self):
inner_call()` must not attribute `inner_call` to the module or to `Foo`),
plus: a class still sees its own bases/decorators/class-level calls; a method
still sees its own calls; a module still sees a genuine module-level call
sitting next to a class; and -- a case A5.2 didn't test directly -- a function
containing a nested `def` does not leak the nested function's calls either,
since the nested def has no `Symbol` of its own and misattributing its calls
to the enclosing function would just move the containment bug rather than
fix it.

**Ground-truth regression check**, `tests/unit/test_repository_verification.py`
(unchanged, both still pass): `Session.request`'s five documented real callees
are still all present, and every `repr_L2_text`/`repr_L3_text` across
`requests` and `flask` still parses as valid Python. The fix does not touch
what a function/method's own calls resolve to -- only what a
module/class/function's *scan region* is -- so real call detection is
unaffected; only over-collection is.

Full suite: 291 passed, 3 skipped (up from 283/3 in A5 -- the 8 new tests),
zero regressions.

---

## A6.2 -- Re-extraction: edge counts, and Query specifically

Re-ran `extract_repository` over the same Django checkout and the same,
reused SCIP index (`--no-reindex` -- nothing about symbol/reference
*resolution* changed, only which resolved references get attributed to which
symbol's occurrence scan). 43,420 symbols, unchanged -- this fix does not
touch symbol discovery, only edge attribution.

**Edge counts by type, whole-corpus:**

| Type | Before | After | Delta | % |
|---|---:|---:|---:|---:|
| `CALLS` | 95,288 | 56,344 | -38,944 | **-40.9%** |
| `DECORATED_BY` | 4,317 | 4,317 | 0 | 0.0% |
| `IMPLEMENTS` | 7,191 | 7,191 | 0 | 0.0% |
| `INHERITS_FROM` | 7,149 | 7,149 | 0 | 0.0% |
| `OVERRIDES` | 1,992 | 1,992 | 0 | 0.0% |
| `READS_CONSTANT` | 820 | 820 | 0 | 0.0% |
| `REFERENCES_TYPE` | 7,150 | 7,150 | 0 | 0.0% |
| **Total** | **123,907** | **84,963** | **-38,944** | **-31.4%** |

Every non-`CALLS` type is **byte-for-byte identical**. This is exactly what
the bug's shape predicts, not a coincidence to be pleased about: `REFERENCES_TYPE`/
`DECORATED_BY` were only ever collected from a root's own bases/decorators/
annotations (already gated correctly in the old code, since that branch only
ran when `node is root`), so they were never exposed to the over-walk. Only
`CALLS` classification had no such gate, so it alone absorbed every spurious
edge from every nested scope. A fix that moved any of the other five counts
would have meant the diagnosis was wrong; it didn't.

**`django.db.models.sql.query.Query`'s outgoing `CALLS`, specifically** (the
number A5 called out): **84 -> 0**. The containing module,
`django.db.models.sql.query`: **92 -> 1** -- the one survivor is
`django.utils.regex_helper._lazy_re_compile()`, the genuine module-level call
A5.2 already identified as the sole legitimate exception. `Query.build_filter`'s
incoming `CALLS` now lists exactly its three real callers
(`Query._add_q`, `Query.build_where`, `Query.split_exclude`) -- the module and
the class are gone from that list, not filtered out of it.

---

## A6.3 -- Re-ingest and 200-trial re-validation, vs A4's baseline

`bash scripts/run_hydradb.sh reset` (prior graph backed up to
`~/out/django-pre-a6/` rather than discarded), then the standard ingest with
`--offset-index`: 232.6s load + 980.0s read-back verify, 43,420 `:Symbol` /
21,966 `:Test`, 84,963 edges read back exactly matching what was written, zero
oversize skips. The eligible seed pool (`kind in {function, method}`, not
under `tests/`, `repr_L3_tokens >= 150`) is **1,891 symbols, identical set,
before and after** -- this fix does not move enough tokens on any function or
method's own canonical text to cross that filter's threshold anywhere in the
corpus, so the 200-trial comparison below is over the exact same seed sets A4
used (same pool, same `rng_seed=20260817`), not merely a same-sized one.

`scripts/validate_budget_django.py --verify-closure` and
`scripts/validate_emit_django.py`, both 200 trials / 6 seeds / 8,000-token
budget, against A4.1's own quoted baseline:

| | A4 baseline | A6 | Delta |
|---|---:|---:|---:|
| `OK` (P3) | 177 (88.5%) | **194 (97.0%)** | **+8.5 pts** |
| `DEMOTED:P2` | 1 (0.5%) | 1 (0.5%) | 0 |
| `DEMOTED:P1` | 22 (11.0%) | **5 (2.5%)** | **-8.5 pts** |
| `DEMOTED:P0` / `EXCEEDED` | 0 / 0 | 0 / 0 | -- |
| `token_margin` min/median/p90/max | -2,319 / -707 / -356 / -85 | -2,807 / **-308.5** / -137 / -45 | median **+398** (less negative) |
| `margin_fraction` median | -10.0% | **-6.0%** | +4.0 pts |
| `token_margin > 0` (I4) | 0/200 | 0/200 | holds |
| `is_closed` (I6) | 200/200 | 200/200 | holds |

**Both invariants hold exactly as before -- nothing about this fix touches
I4 or I6's guarantee, only what the closure and packer see.** The headline
move is the profile ladder: P3 hit rate jumps from 88.5% to 97.0%, and every
point of that gain comes directly out of P1 (22 -> 5), not P2 or `EXCEEDED`
-- consistent with A3.3's finding that the visible ladder is P3 -> P1. The
mechanism is direct: fabricated `CALLS` edges out of every class/module a
seed's mandatory closure touched were inflating the *mandatory floor* itself
(not just optional packing, per A5.3), so some floors that should have fit
comfortably inside P3's budget were being measured as too large and getting
demoted. Removing the fabrication shrinks floors, and floors that no longer
overrun stay at P3.

**Bonus, not asked for but the same measurement A3.6 made, so reported for
continuity:** `mandatory_identities` (the L1 tier `cost()` actually charges
tokens for) median dropped from A3.6's 6 to **4.0** (p90 12 -> 9; tokens
median 97 -> **63.0**, p90 226 -> 156). The **max is identical**, 56 identities
/ 979 tokens, in both -- the 200 seed sets are the same sets in the same
order (same pool, same `rng_seed`), so trial-for-trial correspondence is
meaningful, and the worst trial's seed set evidently never touched a
contaminated class/module either before or after. Not every trial was
affected by this bug; the ones that touched a populated class or module were.

**Utilisation moved in the direction A5.1 did not predict for its own single
example.** A5.1 found that for the `QuerySet.filter`/`build_filter` pair
specifically, the freed budget went unspent because that pair's candidate
pool had nothing else to admit. Across the full 200-trial distribution, the
opposite is true in aggregate: median utilisation *improves* (margin_fraction
-10.0% -> -6.0%), because most seed sets' freed floor budget *does* have
real candidates waiting to be packed into it. A5.1's single-pair finding was
a real but non-representative corner case (a small candidate pool), not the
general pattern; both are true, for different seed sets, and this document
does not paper over the difference.

---

## A6.4 -- The regenerated §8.1 example, and the identity-hints count that motivated this whole amendment

`scripts/worked_example.py "django.db.models.query.QuerySet.filter"
"django.db.models.sql.query.Query.build_filter" --budget 8000`, against the
freshly re-ingested graph:

| | Original (buggy, §8.1) | A5 (packing fix only) | A6 (root-cause fix) |
|---|---:|---:|---:|
| optional context admitted | 3 (2,182t) | 1 (13t) | 1 (13t) |
| closure size (`len(ctx.levels)`) | 120 | 65 | **47** |
| emitted symbols | 24 | 22 | 22 |
| budgeted tokens | 6,932 / 8,000 | 4,710 / 8,000 | 4,715 / 8,000 |
| emitted tokens | 6,130 | 4,364 | 4,368 |
| mandatory identities | 3 | 2 | 2 |
| identity hints | 24 | 23 | **22** |
| **identity hints that are artifacts** | n/a (whole optional block was) | **13 / 23 (57%)** | **0 / 22 (0%)** |

The closure shrinks a further 18 nodes (65 -> 47) beyond A5.1's fix, because
`WhereNode` and `ColPairs` -- both legitimately admitted at L2 in the
*mandatory* floor, per A5.3 -- no longer carry the fabricated `CALLS` edges
that were pulling in roughly three dozen unrelated symbols on hop 2 of the
plain two-hop closure. A5.1's packing-side filter could never have reached
this, because the corruption was never in what the packer proposed; it was in
what the mandatory closure's own second hop read.

**Every one of the 22 identity hints was traced individually against the
re-run closure's provenance** (not sampled -- all 22): all trace back through
a real method-level `CALLS` chain that `build_filter` genuinely reaches --
`Query._add_q` really constructs a `JoinPromoter` and calls its two methods;
`Query.split_exclude` really constructs `OuterRef`/`Exists`/`ResolvedOuterRef`
and calls `bump_prefix`/`clear_ordering`/`add_filter`/`trim_start`;
`Query.get_initial_alias` really calls `join`/`ref_alias`;
`Query.solve_lookup_type` really references `LOOKUP_SEP`/`refs_expression`/`Ref`;
`Query.check_related_objects` really calls `check_query_object_type`/
`check_rel_lookup_compatibility`. Zero of the 22 are containment artefacts.
The two `mandatory_identities` (`Expression`, `Node`) are equally genuine --
real base-class references off `ColPairs` and `WhereNode` respectively, not
the base-class-of-the-fabricated-`Query`-candidate artefact A5.1 found (that
specific one, `BaseExpression`, is gone because the fabricated `Query`
candidate it came from is gone).

This is the answer to A5.3's open question, now with the root cause removed
rather than papered over: identity hints in this example are no longer a
mix of real dependencies and containment noise. They are only real
dependencies. Nothing here suppresses or reclassifies a hint -- every hint
shown is one `graph.closure` actually derived from a real `CALLS`/
`REFERENCES_TYPE` edge, checked against `ClosureResult.provenance` directly.

---

## Decisions

**Shipped.** The fix, its regression tests, the re-extraction, the re-ingest,
and this validation are all complete and green: 291/291 non-skipped tests
pass, I4 and I6 hold at 200/200, and the P3 hit-rate improvement is a genuine
consequence of removing fabricated mandatory-floor inflation, not a tuning
choice.

**A5.1's containment filter stays in `graph/pack.py`.** It is now largely
redundant for the specific fabrication this amendment removes -- a
module/class no longer has a spurious `CALLS` edge to its own seed to
propose in the first place -- but it remains correct, cheap defense-in-depth
against any future case where a container legitimately calls something at
its own scope that happens to also be a seed's own container elsewhere in a
multi-seed compile, and it costs nothing to keep.

**The old graph is preserved, not deleted**, at `~/out/django-pre-a6/`
(symbols.jsonl, edges.jsonl, offsets.json), in case any later item needs to
reproduce a pre-A6 number.

**Item 8/9/10 can now proceed without this liability.** A5.4 flagged this
fix as required "before Item 8 ships class-level seeds" specifically because
a class seeded directly enters at L3, and `CALLS(L3)->L2` would have forced
every one of a fabricated container's ~84 phantom targets into a full L2
declaration -- an order of magnitude worse than anything visible in A5's
document. That ceiling is gone: `Query`'s outgoing `CALLS` is 0, and the same
holds for every class/module in the corpus that this amendment's whole-corpus
`CALLS` count confirms was affected uniformly, not just the one example both
this document and A5 happened to use.

### Summary

| Section | Change |
|---|---:|
| A6.1 | `occurrence_nodes()` rewritten with a hand-written `_own_scope()` recursion that refuses to descend into nested `FunctionDef`/`AsyncFunctionDef`/`ClassDef` bodies. 8 new regression tests, including the exact A5.2 failing case and a nested-inner-function case A5.2 didn't cover. Full suite 291/291 (+8 over A5), 0 regressions. |
| A6.2 | Re-extraction: `CALLS` 95,288 -> 56,344 (-40.9%); every other edge type byte-identical, confirming the fix's scope matches the diagnosis exactly. `Query`'s outgoing `CALLS`: 84 -> 0. Module's: 92 -> 1 (one genuine module-scope call survives). |
| A6.3 | Re-ingest + 200-trial re-validation vs A4's baseline, same seed pool and seed sets: P3 88.5% -> 97.0% (all of the gain from P1, 11.0% -> 2.5%); `token_margin` median -707 -> -308.5 (utilisation improves in aggregate, the opposite of A5.1's single-example finding, both true for different seed sets); I4 and I6 hold at 200/200 on both. |
| A6.4 | §8.1 re-regenerated: closure size 120 -> 65 (A5) -> 47 (A6); identity hints that are artefacts: n/a -> 13/23 (A5) -> **0/22 (A6)**, every one individually traced to a real `CALLS`/`REFERENCES_TYPE` edge. |
| Decision | A5.1's packing-side filter is kept as defense-in-depth. Old graph preserved at `~/out/django-pre-a6/`. Item 8's class-level seed risk (A5.3/A5.4) is closed, not just documented. |

## A6.5 -- Pre-registration for back-propagation metrics

The v1.3 §9 simulation predicted median closure 47, p90 83, 10/200 over
8,000 tokens; the pre-A6 implementation produced 46, 86, 10. That validation
ran against a graph since shown to carry 38,944 fabricated `CALLS` edges.
Prediction for the corrected graph, registered before measurement:
**closure sizes shrink**, because fabricated container edges are removed; the
effect is **largest where a class or module was admitted at L2**, since those
carried the bulk of the fabrication and re-expanded on hop 2. Median and p90
should both fall; the over-budget count should fall or hold.

The outcome will be reported using this pre-registered classification: it
**holds** if the measured changes match the prediction; it **shifts with a
mechanism** if the changes move in the predicted direction but one headline
comparison differs; and it **breaks** if the measurements move against the
prediction, in which case the original result will be scoped to the pre-A6
graph.
