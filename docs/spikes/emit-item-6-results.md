# Item 6 — Emission: Results

HydraDB `0.1.0`, commit `6a2fbb192f37f51a93690a2ae2d2f5e27e6e4219`. Python 3.14.4,
`neo4j` 6.2.0, Ubuntu 26.04 under WSL2. Django re-extracted with the A2.2 cap and
the three extraction fixes in §7, then re-ingested — so every figure here is
against current data, and Items 7–9 should build on it.

```
Item 6  fixture suite            PASS   72 tests + 2 strict-xfail, no database, 4 s
Item 6  Django emission          PASS   12 tests + 1 strict-xfail, 50 contexts, 54 s
Item 6  ast.parse on output      PASS   86,840 / 86,840 canonical representations
Item 6  mandatory identities     PASS   50/50 contexts, none dropped
Item 6  unresolvable FQNs        PASS   0 across 50 contexts
Item 6  I4  token_margin <= 0    FAIL   39/50 contexts. Upstream under-count, §4.
```

**The one number the task asked for is a failure, and it is not emission's.**
`token_margin` is positive on 11 of 50 Django contexts, median **−130** and max
**+493** tokens. The cause is that §6.2's `cost()` charges a flat 40 tokens for
"the context header" and nothing for the rest of the model-visible structure that
§7.1's file grouping requires. §4 quantifies it and recommends the amendment; per
the task's instruction it is reported rather than papered over, and §6.2 is left
alone.

Two findings run in opposite directions and partly cancel, which is why the
median margin is negative while the tail is positive:

* `cost()` **under-counts framing** by ~6 tokens per emitted symbol plus ~13 per
  file group (§4).
* `cost()` **over-counts source text**, because it cannot see emission-time
  dedup — median **591 tokens**, max **2,550** (§5). One context finishes 26%
  under its own budget.

Fixing only the second would push far more contexts over budget. They should be
fixed together.

---

## 1. Reproduction

```bash
cd ~/context-compiler
source .venv/bin/activate

# A2.2 + the §7 fixes, then current data for Items 7-9
python -m context_compiler.extract.pipeline --repo ~/targets/django \
    --out ~/out/django --no-reindex
python -m context_compiler.graph.ingest --symbols ~/out/django/symbols.jsonl \
    --edges ~/out/django/edges.jsonl \
    --offset-index ~/out/django/offsets.json --no-verify

python -m pytest tests/unit -q                             # 72 tests, no DB, 4 s
CC_TRIALS=50 python -m pytest tests/graph/test_emit_django.py -q
```

---

## 2. What emission is

`emit(context, source, sidecar) -> EmittedContext`. Input is Item 5's `Context`
verbatim — a level map, provenance, hints, a profile and a status. Emission
renders it and **decides nothing**: it does not select, score, budget, or
recompute a cost. Every token figure it prints is read from the `Context` or from
the sidecar that `Context` was budgeted against.

The Item 5 dataclass landed before this work started, so the task's placeholder
`CompiledContext` was not needed — `emit()` reads `graph.compile.Context` through
a structural `ContextLike` protocol, and the fixture suite exercises the same
protocol with the real `Compiler` over a stub edge oracle.

### §7.1 ordering, as shipped

```
header
# --- seeds (6) ---                 the task's L3 seeds
# --- dependencies (17) ---         non-seeds reached by a rule fired from a seed
# --- optional context (23) ---     everything packing bought
# --- mandatory identities (3) ---  never truncated
# --- identity hints (23) ---       truncatable
```

Within each content section, symbols are grouped by file and then by class:

```
# django/db/models/query.py
from django.db.models import sql            <- hoisted once for the whole group

class QuerySet:                             <- hoisted once for the whole class
# filter  [L3, 61t]
    def filter(self, *args, **kwargs):
        ...
# _filter_or_exclude  [L3, 107t]
    def _filter_or_exclude(self, negate, args, kwargs):
        ...
```

Three properties of that shape are load-bearing rather than cosmetic:

1. **All framing is a `#` comment**, so a file group is valid Python. The Item 1
   `ast.parse()` check therefore extends to assembled output, which is what
   caught two of the three extraction defects in §7.
2. **Block headers carry the leaf name, not the FQN.** A Python FQN is the module
   path plus the leaf, and the group header already prints the path. Printing the
   module prefix on all 39 blocks would be paying twice for the same information
   — about 300 tokens per context.
3. **Provenance is a suffix, not a second line.** See §4: there is no budget for
   a second line per emitted symbol.

### §7.4 provenance

```
# add_q  [L2, 96t]  <- QuerySet._filter_or_exclude  CALLS
```

One line, the first rule that pulled the node in. Seeds get none — a seed is not
in the context because something pulled it in. `verbose_provenance=True` renders
every recorded `Reason` with its rule string; full derivation *chains* are Item
7's `explain_inclusion`, which is a separate call and not budget-bound.

The `(rule: CALLS(L3)->L2)` clause the spec shows moved to the verbose form, and
the Unicode arrow became ASCII `<-`. Both for the same reason as everything else
in §4: the rule is recoverable from the edge type and the two levels, which the
line already shows.

---

## 3. Text comes from the offset index (A2.1)

`sidecar.read_repr_text` is read, not reimplemented and not modified.
`OffsetTextSource` memoises per compile, because a symbol is asked for more than
once — once as its own block, again as another node's provenance `via`.

| | Per context |
|---|---:|
| Seeks | **47** median, 82 p90, 91 max |
| Total seek time | **2.65 ms** median, 8.17 ms max |
| Per seek | **57.7 µs** median, 200.6 µs max |

**2.65 ms against a ~1,000 ms compile.** A2.1 traded a 56–82% slowdown of the
closure's hot path for a quarter of a percent of emission, and it also removed
the 164-symbol `>32 KiB` property problem outright. One seek returns the whole
record, so `file` and `start_line` arrive with the text — both are needed
(grouping by file, and rendering `fqn — file:line`) and neither is in
`SymbolMeta`.

---

## 4. Invariant I4 — the check the task asked for, and it fails

**Budgeted cost must be an upper bound on emitted cost.** Measured over 50
compiled contexts at an 8,000-token budget:

| | `token_margin` | as a fraction |
|---|---:|---:|
| min | **−2,067** | −26.2% |
| median | **−130** | −1.9% |
| p90 | +185 | +2.4% |
| max | **+493** | **+6.2%** |
| `<= 0` | **39 / 50** | |

The 11 positive cases are a genuine I4 violation: those artifacts contain more
tokens than the budget said they would, and the worst is 8,465 tokens against an
8,000-token budget.

### 4.1 Where the excess comes from

`cost() = src + prov + ident + HEADER_TOKENS`, and every term is spent exactly:

| Term | What emission renders | Slack |
|---|---|---|
| `src` | the canonical text, verbatim | dedup only (§5) |
| `ident` | `fqn — file:line`, byte-identical to the costed string | none, +1/line for the newline |
| hints | the same, from the 5% reserve | none, +1/line |
| `prov` | the one-line trailer | seeds' share, ~19 tokens × 6 |
| `HEADER_TOKENS` = 40 | the three-line header (39–47 tokens) | ~0 |

Nothing in that list pays for the **file group headers, the section markers, or
the blank lines between blocks** — and §7.1 requires the grouping that makes the
file headers necessary. At the median that is 21 file groups at ~10 tokens each,
which is most of the gap on its own.

Fitted over all 50 contexts, the residue is:

```
6 tokens per emitted symbol  +  13 tokens per file group  +  40 fixed
```

and no context exceeds it (minimum headroom 123 tokens, median 649). Using only
data `cost()` already has — it iterates the level map but does not know file
counts — **15 tokens per emitted symbol** also covers every case.

### 4.2 Recommended amendment (not applied)

Add a framing term to §6.2's `cost()`:

```python
FRAMING_TOKENS_PER_EMITTED = 15          # or 6 + 13 per file, given a file count
prov = sum(provenance_tokens(n) + FRAMING_TOKENS_PER_EMITTED
           for n, lv in level_map.items() if lv >= L2)
```

This is §2.1.1's own rule — *"model-visible metadata is budgeted (I4)"* — applied
to the metadata §7 actually emits. The price is real: 15 × 39 ≈ 585 tokens of an
8,000-token budget at the median, which will move some trials from P3 to P1 and
lower the 91.5% P3 hit rate. That is the honest cost of the guarantee, and it is
a §6.2 change, so it is recommended here rather than made.

`cost()` was left untouched deliberately. The task says Item 5 owns the cost
model and that two implementations would diverge; amending it would also
invalidate the 200-trial figures Item 5 validated hours earlier.

Both the fixture suite and the Django suite carry a `token_margin <= 0` test
marked `xfail(strict=True)`. Strict, so it fails loudly if someone marks it fixed
without fixing it, and turns into a hard pass the moment the amendment lands. A
second, *passing* test holds emission to the measured envelope, so a regression
that makes framing meaningfully more expensive is caught.

---

## 5. Dedup — and the over-count on the other side

Two shrink-only transformations, both licensed by §7.2's *"emission-time dedup
can only shrink output below the budgeted figure, which is what makes I4 an upper
bound rather than an estimate"*:

* **Import hoisting.** The union of a file group's members' import statements,
  rendered once in the group header, deduped globally in output order. Import
  statements can span lines — `discover_file` stores the whole source segment, so
  a parenthesised `from x import (A, B, C)` arrives as one multi-line entry, and
  those are the expensive ones. Handling them raised median savings from 168 to
  591 tokens and cut the I4 violation from 48/50 contexts to 11/50.
* **Class-header hoisting.** `canonical_l2` re-emits the enclosing `class X:` in
  front of every method, so two methods of one class each carry a copy. Merged
  under one header, keyed on `(shell, owner)` so two nested `class Meta:` in one
  file are not conflated.

| | Per context |
|---|---:|
| Tokens saved | **591** median, 1,606 p90, **2,550** max |
| Lines saved | **52** median, 155 max |
| As a share of the emitted context | **8.7%** median, **43.8%** max |

**`cost()` cannot see any of this, so it over-counts `src` by that much.** The
consequence is visible in the margin's left tail: one context emits 26% fewer
tokens than budgeted, which is 1,200 tokens of budget the packer could have spent
on another caller. The task flagged this case too — *"if it is very negative (say
under −20%), the model is over-counting and wasting budget"* — and here it is,
with a mechanism attached.

The two errors are not symmetric and must not be fixed separately. Crediting
dedup back to the packer without charging for framing would push the positive
tail higher, because the packer would admit more symbols and each admitted symbol
costs unbudgeted framing.

---

## 6. A2.2 — folded constants capped at 4 KB

Shipped in `extract/constants.py` as `render_folded()`, wired into the pipeline,
and recorded in `docs/specs/jsonl-contract.md`. On Django, three constants exceed
the cap:

| Constant | Folded size | `evaluable` | `static_value` |
|---|---:|---|---|
| `tests.validators.tests.INVALID_URLS` | 66,517 B | `true` | `null` |
| `django.conf.locale.LANG_INFO` | 9,197 B | `true` | `null` |
| `tests.validators.tests.VALID_URLS` | 6,526 B | `true` | `null` |

`evaluable` stays `true` — the constant *is* statically evaluable, the extractor
is declining to inline it — and the literal is **never truncated**. `repr_L2_text`
carries the defining expression plus a `# folded value omitted: 66,517 bytes (cap
4,096)` line, so the model learns the constant is a list of invalid URLs without
paying for 2,000 of them.

The oversized-property failure that motivated the cap is gone: the `static_value`
ingest pass now writes 1,435 rows with no oversize skips.

### 6.1 The cap is not idempotent under re-ingest — engine constraint

`static_value` shrinking to `null` cannot be propagated to an existing node.
Ingest is `MERGE` + `SET`, the `static_value` pass only covers the non-null
subset, and **this engine can neither remove a property nor set one to null**:

| Attempt | Diagnostic |
|---|---|
| `MATCH (n:Symbol {...}) REMOVE n.static_value RETURN n.id` | `mutation queries cannot continue with MATCH, RETURN, or W…` |
| `MATCH (n:Symbol {...}) SET n.static_value = null RETURN n.id` | `property values support integer, float, boolean, and stri…` |
| `UNWIND $rows AS row MATCH (n {id: row.v}) REMOVE n.static_value` | `UNWIND MATCH must end in RETURN or DELETE` |

So after re-ingesting over the pre-A2.2 store, those three nodes still carry the
old oversized `static_value` in the graph while `symbols.jsonl` has `null`.
**Nothing reads `static_value` from the graph** — every scalar the closure, cost
model and emitter use comes from the sidecar — so this is stale data, not a
functional defect. Clearing it requires `scripts/run_hydradb.sh reset` followed
by a full ingest, which was not run: it destroys the local dev store to fix three
properties no code path reads. Flagged for whoever wants the graph to match the
contract exactly.

**Appendix A addition:** *a property cannot be removed or set to null; a value
that shrinks out of range on re-extraction survives in the graph until the store
is rebuilt.*

---

## 7. Three extraction defects, found by emitting

All three were invisible until assembled output was rendered and parsed. All are
in `extract/representations.py`; all are fixed, tested, and required the
re-extraction.

**7.1 Unterminated docstring literal — made declarations unparseable.**
`canonical_l2` wrapped a docstring's first line in `"""…"""`.
`SQLUpdateCompiler.pre_sql_setup` documents itself as `munge the "where"`, which
produced `"""… the "where""""` — an unterminated literal. Two Django symbols were
affected and both had *unparseable* `repr_L2_text`. `docstring_literal()` now
keeps triple quotes when safe and falls back to `repr()` otherwise, which is a
docstring just the same.

**7.2 Method bodies indented twice.** `ast.get_source_segment` starts at the
node's column, so the first line arrives dedented while continuation lines keep
the file's indentation. Re-indenting all of them uniformly put every Django
method body four columns deeper than its own `def`. It parses — Python only needs
the block internally consistent — so nothing caught it until the worked example
made it visible. `dedent_source_segment()` fixes it, and at **zero token cost**:
`cl100k` folds the extra spaces into the same whitespace tokens.

**7.3 Multi-line imports were never deduped.** Not an extraction bug but an
emission one, in the same family: `split_imports` bailed out on parenthesised
imports. §5 has the numbers.

After the fixes, **`ast.parse()` succeeds on all 86,840 canonical
representations** — 43,420 symbols × two levels — not merely on those appearing
in the sampled contexts.

---

## 8. Worked example

A real task: *why does `QuerySet.filter()` with a related lookup produce this
SQL?* Six hand-picked seeds along the filter path rather than a random sample:

```
django.db.models.query.QuerySet.filter
django.db.models.query.QuerySet._filter_or_exclude
django.db.models.sql.query.Query.add_q
django.db.models.sql.query.Query.build_filter
django.db.models.query_utils.Q._combine
django.db.models.sql.query.Query.names_to_path
```

| | |
|---|---:|
| Status | `OK` (P3 FULL) |
| Closure | **92 symbols** |
| Emitted | **46** — 6 bodies, 40 declarations |
| Files | 17 |
| Budgeted / emitted tokens | 7,954 / **7,724** |
| `token_margin` | **−230** |
| Mandatory identities | 3 |
| Identity hints | 23 |
| Dedup saved | 604 tokens over 57 lines |
| Round trips | **24** |
| Compile | 1,563 ms |
| Offset-index seeks | 72, 2.96 ms total |

The full context is in
[`emit-item-6-example.md`](emit-item-6-example.md), verbatim, header to hints.
Three things in it are worth pointing at during the video:

* **`build_filter` costs 1,665 tokens on its own** — 21% of the budget for one
  method — and the packer still fits 40 declarations around it.
* **The optional section is all reverse `CALLS`**, and most of it is Django's own
  tests for `names_to_path`. That is the packer finding the covering tests
  *statically*, before Item 9's `COVERS` edges exist.
* **Three mandatory identities**: `Expression`, `Node`, `Node.create`. Named in
  emitted text, not themselves emitted, and rendered because
  `Structural closure: complete` is otherwise not a true statement. A3.6's
  measured cost — 98 tokens at the median, not §1.1's predicted 3,000 — holds
  here at 3 identities.

---

## 9. Test inventory

```
tests/unit/test_emit.py                  44 passed, 2 xfailed   no DB
tests/unit/test_representations.py       11 passed              no DB
tests/unit/  (whole directory)           72 passed, 2 xfailed   4 s
tests/graph/test_emit_django.py          12 passed, 1 xfailed   HydraDB, 54 s
```

`test_emit_django.py` lives under `tests/graph/` rather than `tests/unit/`
because it needs HydraDB and a 117 MB `symbols.jsonl`; `tests/unit` stays
fixtures-only and sub-second, which is the property the earlier results docs rely
on. `CC_TRIALS` bounds the context count and defaults to the 50 quoted here.

The fixture suite builds its sidecar from real Python text through the same
`count_tokens()` the emitter uses, so an I4 check on fixtures is a check on two
views of the same strings — not a check that invented numbers agree with
themselves.

---

## 10. Files

```
src/context_compiler/emit/__init__.py            new
src/context_compiler/emit/render.py              new   Sec 7.1-7.4, dedup, I4 evidence
src/context_compiler/emit/source.py              new   A2.1 offset-index text access
src/context_compiler/extract/representations.py  mod   docstring_literal(),
                                                       dedent_source_segment()
src/context_compiler/graph/profiles.py           mod   Profile.label ("P3 FULL")
src/context_compiler/graph/pack.py               mod   stale A3.4 docstring
tests/unit/test_emit.py                          new
tests/unit/test_representations.py               mod   4 tests for the Sec 7 fixes
tests/graph/test_emit_django.py                  new
docs/spikes/emit-item-6-results.md               this file
docs/spikes/emit-item-6-example.md               the worked example, verbatim
```

`src/context_compiler/graph/budget.py` and `compile.py` are **unchanged** — no
cost was recomputed and no admission decision was touched. `sidecar.py` is
unchanged; `read_repr_text` is read as-is. Nothing under `docs/specs/`, `scripts/`,
`~/hydradb` or `~/targets/` was modified. No MCP server, seed resolution, runtime
tracing or evaluation code was written.

The A2.2 fix, the JSONL contract update and A3.1/A3.2/A3.4 were already in the
tree at `4bf0921` when this work started; §6 verifies A2.2 end to end on
re-extracted data rather than re-implementing it.

---

## 11. Unresolved issues

1. **I4 is violated on 11 of 50 contexts** (§4). Emission's framing is already
   spare; the fix is a framing term in §6.2's `cost()`, measured at 15 tokens per
   emitted symbol, and it will cost ~7% of the budget and some P3 hit rate. Needs
   a spec decision before Item 7 exposes `compile_context` over MCP, because the
   MCP response will carry both the claimed and the actual token count.
2. **`cost()` over-counts source text by the dedup savings** (§5) — median 591
   tokens, max 2,550, one context 26% under budget. Fix it *with* issue 1, never
   before it.
3. **Three stale `static_value` properties in the graph** (§6.1). The engine
   cannot remove or null a property, so a clean reset + ingest is the only fix.
   No code path reads them.
4. **Section token counts do not sum exactly** to `EmittedContext.tokens`. The
   tokeniser is not additive across a boundary, so per-section figures are
   counted independently and are a token or two out. `tokens` is the
   authoritative figure and is what every assertion uses.
5. **`_classify`'s "dependency" definition is provenance-based.** A non-seed
   counts as a direct dependency if any recorded `Reason` has a seed as its
   `via`. A symbol reachable both from a seed and from a packed node therefore
   lands in the dependencies section. That is the more useful of the two
   placements, but it means the section counts are not a partition of the
   propagation graph.

---

## Appendix — A3 re-validation

A3.1's hub skip, A3.2's corrected `EXCEEDED` suggestion and A3.4's candidate-pool
cap were already in the tree at `4bf0921`. The 200-trial Django validation was
re-run on the re-extracted data to confirm A3.4's latency target and that nothing
else moved:

```bash
python scripts/validate_budget_django.py --verify-closure --out /tmp/cc-final.json
```

| | Item 5 (pre-A3.4) | Now | Target |
|---|---:|---:|---|
| `is_closed` (I6) | 200/200 | **200/200** | all |
| `cost + hints <= budget` (I4 as budgeted) | 200/200 | **200/200** | all |
| P3 / P2 / P1 / P0 | 183 / 1 / 16 / 0 | **183 / 1 / 16 / 0** | unchanged |
| `CLOSURE_BUDGET_EXCEEDED` | 0 | **0** | A3.2: unreachable |
| Candidate pool, max | 784 | **150** | A3.4 cap |
| Bundle evaluations, max | 62,370 | **8,910** | — |
| Round trips, median / max | 24 / 30 | **24 / 24** | 24 median |
| Latency, median | 994 ms | **1,059 ms** | interactive |
| Latency, **p99** | — | **2,782 ms** | **< 3,000 ms** |
| Latency, max | 21,854 ms | **3,941 ms** | — |

**A3.4's target is met: p99 = 2,782 ms.** It is met without much room, and the
figure moves between runs — the same 200 seed sets gave p99 = 3,010 ms on an
earlier run against the pre-re-extraction data. Both runs are the same code and
the same seeds, so treat p99 as sitting *at* the 3 s line rather than comfortably
under it.

**The remaining tail is the database, not the greedy loop.** Re-running the five
slowest trials with per-phase timing:

```
wall 3713 ms | reverse  570 | forward expand 3037 | cpu 107 | 150 cands, 4065 evals
wall 2470 ms | reverse  322 | forward expand 1978 | cpu 170 | 120 cands, 5369 evals
wall 2697 ms | reverse  104 | forward expand 2479 | cpu 114 | 150 cands, 8910 evals
```

CPU is 37–170 ms even on the 8,910-evaluation trial, so A3.4 did what it was for:
the quadratic packing loop is gone from the tail, and what is left is the 18–24
`UNWIND` round trips of the mandatory closure and the candidate envelope. Capping
the pool further would not help; only fewer or faster reads would.

Two side effects worth recording:

* **Round trips are now 24 on every trial, max included.** The one trial that
  paid 30 did so because a 784-candidate pool chunked the envelope into
  `6 × ceil(784/500) = 12`. A 150-cap is below `B = 500`, so the envelope is
  always 6 and A3.1's corrected model `12 + |seeds| + 6·ceil(|cands|/B)` collapses
  to a flat 24 at six seeds.
* **A3.1's hub skip fired once in 200 trials.** The eligible seed pool's median
  in-degree is 3, so a >500-in-degree seed is rare — but the one that appeared
  would have cost seconds. Cheap insurance, and it is a dict lookup against a
  table `idf` already loads.
