# Task: Item 6 — Emission

You built Items 1–2 and own the extraction layer. Item 6 is its natural continuation: you already own canonical representations (§7.2), and emission renders them.

The graph agent is building Item 5 in parallel. You will not touch their code.

---

## 0. Source of truth

```
docs/specs/context-compiler-v1.3.md    §1.1, §7.1–§7.4, invariants I4 and I6
docs/specs/amendment-a1.md
docs/specs/amendment-a2.md             ← A2.1 and A2.2 both bind you
docs/spikes/graph-item-3-4-results.md  §3, §4.1 — why text is not in the graph
```

Do not redesign. Document, stop, escalate.

## 1. Scope

**You own** `src/context_compiler/emit/`, `src/context_compiler/extract/` (for the A2.2 fix), `tests/unit/`, `docs/spikes/emit-item-6-results.md`.

**You must not touch** `src/context_compiler/graph/`, `tests/graph/`, `scripts/`, `docs/specs/`, `~/hydradb`, `~/targets/`.

**You must not implement** budget admission or packing (Item 5, in progress now), the MCP server (Item 7), seed resolution (Item 8), runtime tracing (Item 9), evaluation (Item 10).

## 2. Two decisions that bind you

### A2.1 — text comes from the offset index, never the graph

A1.1's plan to fetch `repr_*_text` by single-source query is **withdrawn**. 164 Django symbols exceed the engine's ~32 KiB string cap (`tests.admin_views.tests` needs 347 KB), and graph-resident text slowed the closure's own hot path 56–82% in a controlled measurement.

`sidecar.read_repr_text(id, level)` already exists in the graph layer and seeks into `symbols.jsonl` via `{id: [offset, length]}`. **Read it; do not reimplement it, and do not modify it.** If it needs a change, report and stop.

### A2.2 — cap folded constants at 4 KB (your fix, in `extract/constants.py`)

One Django constant folds to 66,517 bytes and broke the default ingest. The engine limit is incidental — the real point is that a 66 KB folded constant is *bad context*. The model doesn't need 2,000 invalid URLs, it needs to know the constant is a list of invalid URLs.

```
folded ≤ 4,096 B  →  evaluable: true,  static_value: "<literal>"
folded >  4,096 B  →  evaluable: true,  static_value: null,
                      static_value_bytes: <int>
```

`evaluable` stays **true** — the constant is statically evaluable, we are declining to inline it. `repr_L2_text` shows the defining expression and notes the size. **Never truncate**: a clipped constant looks valid and is wrong.

Update `docs/specs/jsonl-contract.md` with the new field. Re-extract Django afterwards to `~/out/django/` so Item 5 and Item 7 build on current data — and tell the user, since the graph agent may need to re-ingest.

## 3. What you build

`emit(context) -> EmittedContext`, where `context` is Item 5's output: a level map, hints, profile and status. Emission renders it. It does **not** select, score or budget.

Item 5 is still landing, so define the input as a small dataclass and build against fixtures:

```python
@dataclass
class CompiledContext:
    levels: dict[int, Level]              # id -> L1 | L2 | L3
    provenance: dict[int, list[Reason]]   # Reason(via, edge, rule)
    hints: list[int]
    profile: str                          # "P3" | "P2" | "P1" | "P0"
    status: str                           # "OK" | "DEMOTED:P2" | "CLOSURE_BUDGET_EXCEEDED"
    budget: int
```

Agree the exact shape with the user before writing against it.

### §7.1 — ordering

Seeds (L3) → their direct L2 dependencies **grouped by file** → optional bundle members → mandatory identity index → hints.

Grouping by file matters: a model reading two methods from `django/db/models/query.py` should see them together, not interleaved with unrelated modules.

### §7.3 — two identity sections, not one

| Section | Content | Truncatable |
|---|---|---|
| **Mandatory identities** | FQNs appearing textually in emitted L2/L3 but not themselves emitted | **never** |
| **Identity hints** | anything else worth listing | yes → set `identity_index_truncated: true` |

Item 5 budgets mandatory identities and guarantees they fit. **Your job is to render them all and never silently drop one** — that is what makes `Structural closure: complete` a true statement rather than a claim.

### §7.4 — provenance

Compact trailer per non-seed item, one line:

```
TokenPolicy.rotate                                    [L2, 47 tokens]
  ← AuthService.refresh  CALLS  (rule: CALLS(L3)→L2)
```

`verbose_provenance` is **opt-in**; full derivation chains live in `explain_inclusion` (Item 7), which is a separate call and not budget-bound.

### Header

```
Compiled context · 22 symbols · 7,842 / 8,000 tokens
17 declarations · 5 bodies · 30 identities
Structural closure: complete (P3 FULL)
```

Numbers come from the input. Do not recompute costs — Item 5 owns the cost model, and two implementations will diverge.

## 4. Invariant I4 — the check that makes this real

**Budgeted cost must be an upper bound on emitted cost.**

Item 5 asserts `cost(merged) + hints ≤ budget` using precomputed token counts. Your emitted string is the ground truth. Measure the actual token count of what you produce and compare.

```
token_margin = actual_emitted_tokens − budgeted_tokens
```

**Must be ≤ 0 on every case.** If it is ever positive, I4 is violated and something upstream is under-counting — report it, do not paper over it. If it is very negative (say under −20%), the model is over-counting and wasting budget; also worth reporting.

Emission-time dedup — two symbols sharing an import — can only shrink output. Preserve that property: dedup is why the bound holds.

Use the same `count_tokens()` wrapper from Item 1. Do not introduce a second tokenizer.

## 5. Tests

**Unit, fixtures only:**
- ordering: seeds first, L2 deps grouped by file, hints last
- mandatory identities all rendered; none dropped
- hint truncation sets the flag; mandatory truncation is impossible by construction
- provenance line present for every non-seed at L2 or L3
- `CLOSURE_BUDGET_EXCEEDED` renders a useful message, not an empty context
- dedup shrinks output when two symbols share imports
- **`ast.parse()` succeeds on every emitted L3 block** (extends the Item 1 check to assembled output)

**Django end-to-end** (needs an Item 5 context, or a hand-built one from the real graph):
- `token_margin ≤ 0` across 50 compiled contexts — report the distribution
- no emitted text references an FQN absent from both the emitted set and the mandatory identity list

## 6. Results doc

`docs/spikes/emit-item-6-results.md`: `token_margin` distribution over 50 contexts; dedup savings; offset-index seek latency; a **full worked example** — one real Django task's compiled context, verbatim, header to hints.

That example is the single most useful artifact for the video. Make it a good one: a real Django function with genuine dependencies, not a toy.

## 7. Time box and gate

A2.2 fix and re-extraction: 45 min. Emission core: 2 h. Tests and the worked example: 1.5 h. Overrun → report and stop.

Gate: all unit tests green; `token_margin ≤ 0` on every Django case; every L3 block parses; worked example in the results doc; A2.2 shipped and the JSONL contract updated. Commit after A2.2, again after emission. Do not start Item 7 or 8.
