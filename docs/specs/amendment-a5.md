# Amendment A5 to Implementation Spec v1.3

**Status:** Adopted. Raised by `docs/spikes/mcp-item-7-results.md` §8.1, on the
live Django graph (commit `259c5cb`, Amendment A4 landed).
**Scope:** §6.3 candidate sources only. No architectural change. I1-I6
unaffected on the mandatory side; this amendment tightens I6's packing side
and documents, without fixing, a defect upstream of it.

§8.1's own transcript understated its problem: 2,169 of the 6,932 emitted
tokens (31%) were the containing module and class of a seed, admitted as
`OPTIONAL:static_caller` -- a containment relationship, not a call. Chasing
*why* that edge exists at all led to a second, more serious finding: the same
malformed edges are read in the *mandatory* direction too, inside the plain
two-hop closure, with no packing involved.

---

## A5.1 -- A candidate is not a caller if it contains the seed

### The bug

`StaticCallerSource.find()` (`graph/pack.py`) proposes every reverse-`CALLS`
neighbour of a seed as an optional candidate, with no check on what kind of
symbol it is. When the extraction layer emits a `CALLS` edge from a seed's own
enclosing module or class (A5.2 explains why it does), the packer had no way
to tell that apart from a real caller, so it happily proposed -- and, being
cheap relative to score, admitted -- the seed's own containing scope as
"context on who calls this."

### The fix

`build_candidates()` now drops any candidate whose FQN is a strict dotted
prefix of a seed's FQN before scoring:

```python
def _contains(container_fqn: str, member_fqn: str) -> bool:
    return member_fqn.startswith(container_fqn + ".")
```

Python FQNs nest hierarchically (`module.Class.method`), so this check is
exact containment, not a heuristic: a module or class whose FQN prefixes a
seed's FQN is that seed's enclosing scope by construction, never something the
seed's edges can legitimately name as a caller. It generalizes across every
`CandidateSource` (not just `static_caller`), so `sibling_implementation` and
Item 9's `covering_test`/`observed_caller` get the same guard once they go
live, at zero extra cost -- the check is a string comparison against seeds
already resolved for this compile, done once per candidate.

### Measured effect, one seed pair

`worked_example.py "django.db.models.query.QuerySet.filter"
"django.db.models.sql.query.Query.build_filter" --budget 8000`, live against
the same Django graph and HydraDB instance §8.1 used, before and after:

| | before | after | delta |
|---|---:|---:|---:|
| status / profile | OK / P3 | OK / P3 | -- |
| optional context admitted | 3 | 1 | -2 |
| optional context tokens | 2,182 | 13 | -2,169 |
| emitted symbols | 24 | 22 | -2 |
| closure size (`len(ctx.levels)`) | 120 | 65 | -55 (-46%) |
| budgeted tokens | 6,932 / 8,000 | 4,710 / 8,000 | -2,222 |
| emitted tokens | 6,130 | 4,364 | -1,766 (-29%) |
| utilisation | 86.6% | 58.9% | -27.7 pts |
| mandatory identities | 3 | 2 | -1 |
| identity hints | 24 | 23 | -1 (composition changed, see A5.3) |
| round trips | 20 | 20 | 0 |
| compile latency | 770.6 ms | 327.2 ms | -2.4x |

The two admitted-then-excluded candidates were exactly `django.db.models.sql.query`
(the module, 297t) and `Query` (the class, 1,872t) -- confirming §8.1's own
diagnosis. `Query.build_where` (13t), a real `static_caller` of `build_filter`
(`build_where` calls it directly), still gets admitted in both runs: it is not
a containment edge and the fix does not touch it.

**What fills the freed 2,222 tokens: nothing.** For this seed pair the only
candidate source that fires at all is `static_caller`
(`sibling_implementation` is dormant on function/method seeds per A3.5;
`covering_test`/`observed_caller` are Item 9, not yet available), and once the
module and class are excluded, `Query.build_where` was already the only other
member of that pool in both runs. There is nothing else to spend the freed
budget on, so the compile does not get "differently full" -- it gets smaller
and less padded, and utilisation drops accordingly. A task with a richer
candidate pool would spend the freed budget on real candidates instead;
nothing in this fix prevents that.

**Broader check, 40 Django trials** (`tests/graph/test_budget_django.py`'s
existing fixture, unmodified, before/after this change):

| | before | after |
|---|---:|---:|
| final symbols (median) | 165.5 | 112.0 |
| final tokens (median) | 7,788 | 7,567.5 |
| utilisation (median) | 0.974 | 0.946 |
| candidates (median) | 19.0 | 15.0 |
| admitted (median) | 15.0 | 11.5 |

Consistent with the single-example numbers: the median trial loses about 4
candidates and 3.5 admissions to the containment filter, and the median final
context shrinks by roughly a third (165.5 -> 112 symbols) without any status
changing (39 OK / 1 demoted / 0 exceeded, both runs; I4 and I6 continue to
hold). All 20 assertions in that file, plus the 11 new/existing assertions in
`tests/graph/test_budget_fixtures.py`'s candidate-discovery section, pass.

---

## A5.2 -- Root cause: `occurrence_nodes` does not prune the subtrees it means to skip

Investigated because a containment edge showing up as a `CALLS` edge at all is
itself the interesting fact -- the packing fix above stops the symptom from
reaching the packer, but the edge is wrong from the moment it is extracted.

`extract/ast_occurrences.py::occurrence_nodes()` walks a symbol's own AST
region to find its call sites:

```python
for node in ast.walk(root):
    if node is not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        continue
    if isinstance(node, ast.Call):
        out.append(("CALLS", node.func))
    ...
```

The `continue` is meant to stop a module's or class's own occurrence scan at
the boundary of any function or nested class it contains, so that a call
inside `Foo.method_a` is attributed to `method_a`, not to `Foo` or to the
module. It does not do that. `ast.walk` is a flat BFS that enqueues every
descendant of `root` up front, independent of what the loop body does with
node N -- `continue` only skips *classifying node N itself*; it cannot stop
`ast.walk` from later yielding N's children, because they were already queued
before the loop body ran. So a module or class symbol's occurrence scan
collects every `Call`, type annotation and decorator anywhere in its entire
subtree, at any nesting depth, not only the ones in its own scope.

Confirmed directly:

```python
>>> occurrence_nodes(module_symbol_for("""
... def outer(): pass
... class Foo:
...     def method_a(self): inner_call()
... def top(): Foo().method_a()
... """))
[('CALLS', <Attribute Foo().method_a>), ('CALLS', <Name inner_call>), ('CALLS', <Name Foo>)]
```

`inner_call()`, two scope levels below the module, is attributed to the
module symbol.

Confirmed at scale against the real Django extraction
(`~/out/django/{symbols,edges}.jsonl`, 43,420 symbols):

* `django.db.models.sql.query` (module, the file `django/db/models/sql/query.py`)
  has 92 outgoing `CALLS` edges. The union of `CALLS` edges from every
  function/method *actually defined in that file* is also 92 -- 91 of the
  module's 92 targets are exact duplicates of a real method-level edge; the
  one exception (`django.utils.regex_helper._lazy_re_compile`) is a genuine
  module-scope call.
* `django.db.models.sql.query.Query` (class) has 84 outgoing `CALLS` edges.
  **All 84** are a strict subset of edges its own methods already carry --
  zero of "Query calls X" is anything `Query` itself calls; every one belongs
  to some method of `Query` and is simply re-attributed to the class as well.

This is not specific to this one file: `occurrence_nodes` runs identically for
every `Symbol`, so every module- and class-kind node in the graph carries this
duplication in its forward `CALLS`/`REFERENCES_TYPE`/`DECORATED_BY` edges,
proportional to how much code it contains.

**Not fixed here.** The fix is a real recursive walker that stops descending
into nested `FunctionDef`/`AsyncFunctionDef`/`ClassDef` bodies -- `ast.walk`
cannot be coerced into that with a `continue`, the walk has to be written by
hand. That is a change to `extract/`, requires re-running extraction over the
whole Django corpus (~105 MB `symbols.jsonl`, ~15 MB `edges.jsonl`) and
re-ingesting into HydraDB, and needs its own validation pass against
`tests/graph/test_budget_django.py` and the Item 6 emission suite -- well
outside this amendment's blast radius. Recorded as a required follow-up (A5.4).

---

## A5.3 -- Yes: the same edges fire in the mandatory direction, not only in packing

This is the question that made the investigation worth doing on its own,
independent of the packing fix: `expand()` (`graph/expand.py`) reads the exact
same `edges.jsonl` rows the reverse reader does -- `CALLS` is one relation,
read forward by the mandatory closure and backward by candidate discovery.
Nothing in the forward path is aware A5.1 exists. So the question is not
"could this affect the closure," it is "does anything already sitting in a
mandatory floor have this shape."

It does, in this exact seed pair, with no packing involved. `WhereNode` and
`ColPairs` are both legitimately admitted at L2 in the **mandatory** floor --
`Query.build_filter` really does construct `WhereNode([...])` and
`ColPairs(...)`, so `CALLS(L3)->L2` correctly promotes both. Both are
class-kind symbols, so both carry A5.2's duplication on their own outgoing
edges. Because a promotion to L2 puts a node on the next hop's frontier
(`closure._run_hops`: `if required > L1: next_frontier.append(dst)`), and
`MAX_HOPS = 2` gives seeds exactly one more hop, the *plain two-hop mandatory
closure* -- not any optional bundle -- reads `WhereNode`'s and `ColPairs`'s
out-edges on hop 2 and pulls in roughly three dozen unrelated symbols at L1:
`Query.join`, `Query.ref_alias`, `JoinPromoter` and its two methods,
`WhereNode.as_sql`/`clone`/`leaves`/`_resolve_node`/`_resolve_leaf`/
`_contains_aggregate`/`_contains_over_clause`/`output_field`, `When`,
`BooleanField`, `Exact`, `Mod`, `Case`, `Col.as_sql`, `ColPairs.get_cols`,
among others -- none of which `build_filter` or its real dependencies call.
Verified directly by running `graph.closure.closure()` on this seed pair
against the live graph and reading `ClosureResult.provenance` for every L1
node: each traces back through `WhereNode` or `ColPairs` via `CALLS(L2)->L1`,
never through a seed or a real dependency.

**Today's severity is capped, but only by a fact about seeds, not about the
edges.** Every row of the Sec 4 propagation table maps `L3 -> L2` and
`L2 -> L1` -- nothing maps a level-2 source forward to a level-2 or level-3
target -- so a class or module only ever reaches L3 by being seeded directly.
No seed in this codebase's current seed resolution (`mcp/seeds.py`, still
Item 8's placeholder) is ever a class or module, so today this corruption
tops out at zero-cost L1 identity bookkeeping, not full declarations.

**But it is not harmless even today.** `budget.identity_hints()`'s eligible
pool is defined as `{n for n, lv in levels.items() if lv == L1}` -- literally
every L1-lattice member, with no distinction between "genuinely referenced
elsewhere" and "swept in by a corrupted edge." Rerunning the fixed §8.1
example (A5.1's "after" column) and inspecting the 23 identity hints shown to
the model: 13 of them --  `Case`, `OuterRef`, `Ref`, `ResolvedOuterRef`,
`BooleanField`, `Exact`, `QuerySet._chain`, `JoinPromoter`, `Query.add_filter`,
`Query.trim_start`, `Query.try_transform`, `Query.unref_alias`,
`WhereNode.clone` -- are exactly this leak: `WhereNode`/`ColPairs`
containment artefacts, not anything `build_filter` depends on. A5.1's fix
does not touch this, because `WhereNode` and `ColPairs` are correctly admitted
by a real `CALLS` edge; the corruption is entirely inside *their own*
outgoing edges, upstream of anything `graph/pack.py` controls.

**The ceiling rises the moment a class or module can be a seed.** A3.5 already
flagged that Item 8's real seed resolution will produce class-level seeds.
A class seeded directly enters at L3, and `CALLS(L3)->L2` would then require
a full L2 *declaration* -- not a zero-cost identity -- for every symbol its
occurrence-scan artefact sweeps in. For `Query` that is 84 unrelated
declarations forced into the mandatory floor of any task that seeds the
`Query` class itself, an order of magnitude larger than anything visible in
this document's example and not something A5.1's packing-side fix can reach.

---

## A5.4 -- Decisions

**Ship A5.1 now.** It is a correct, self-contained fix to `graph/pack.py`
(the file this amendment's scope covers), it measurably shrinks and cleans up
every compile that touches a class or module with a populated body, and it
regresses nothing: 20/20 `test_budget_django.py` trials and the full
`tests/unit`/`tests/mcp`/`tests/graph` suite pass, I4 and I6 continue to hold.

**Do not fix A5.2/A5.3 here.** The root cause is in `extract/`, requires a
real recursive AST walker (not `ast.walk` plus a `continue`), a full
re-extraction of the ~105 MB Django corpus, a re-ingest into HydraDB, and
re-validation against the whole Django test suite -- out of proportion to a
packing amendment and outside the "don't touch extraction to ship a graph fix"
discipline this codebase has followed since Item 7.

**Required before Item 8 ships class-level seeds.** A5.3's severity analysis
is conditional on "no seed is ever a class or module" being true, and Item 8
is explicitly expected to break that condition (A3.5). The root-cause fix
must land, and the corpus must be re-extracted and re-validated, before that
happens -- not deferred indefinitely as a nice-to-have. Until then, this
document is the record of why a class- or module-seeded task should not yet be
trusted at face value.

**Watch `identity_hints` composition as a cheap live signal.** A5.3's leak is
visible today without any spec change: any compile whose emitted context
includes a class-kind dependency (a constructed type, a caught exception
type, a decorator) is a candidate for contaminated identity hints. No
suppression is proposed here -- doing so would hide the corruption rather than
fix it, and the hints tier is explicitly documented as `budget.py`'s "only
tier allowed to lose entries," not the place to paper over an upstream defect.

### Summary

| Section | Change |
|---|---|
| A5.1 | `graph/pack.py`: `build_candidates()` excludes any candidate whose FQN strictly contains a seed's FQN. Applies to every `CandidateSource`. |
| A5.1 | §8.1 re-measured: -2,169 optional tokens, -29% emitted tokens, -46% closure size, 2.4x faster, one seed pair; -32% median final symbols across 40 Django trials. Freed budget is not reallocated -- there was nothing else to admit. |
| A5.2 | Root cause identified: `extract/ast_occurrences.py::occurrence_nodes()`'s subtree-skip is a no-op under `ast.walk`, so every module/class symbol's forward hard edges duplicate every edge of everything nested inside it. Not fixed here. |
| A5.3 | Confirmed present in the mandatory direction: legitimately-admitted class nodes (`WhereNode`, `ColPairs`) carry the same corrupted out-edges, which the plain two-hop closure reads on hop 2 with no packing involved. Capped at zero-cost L1 identities today only because no seed is a class/module; visibly leaks into `identity_hints` today (13/23 in this example); becomes full-declaration-sized once Item 8 seeds classes. |
| A5.4 | Root-cause fix + re-extraction + re-validation required before Item 8 ships class-level seeds. Not deferred indefinitely. |
