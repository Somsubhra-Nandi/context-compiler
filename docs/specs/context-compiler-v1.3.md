# Context Compiler — Implementation Spec v1.3 (FROZEN)

**Track 2B, Hack Hydra.** Design review is closed. Changes after this point require a written reason and a changelog line.

- **v1.1** patched nine implementation errors that would have surfaced as HydraDB parse-time rejections after the ingest layer was written.
- **v1.2** closed the optional-packing bug: enrichment broke the closure property it advertised (I6).
- **v1.3** closes five residual inconsistencies: `Test` nodes were invisible to closure (defeating I6 on its own motivating example), truncatable L1 identities made the completeness claim false in the emitted artifact, I4 did not budget model-visible metadata, relationship identity was undefined and would have destroyed versioned runtime evidence, and §9.2/§9.4 contradicted each other on which instances form the headline result.

**Thesis.** Context retrieval for coding agents is a *budget-constrained closure* over typed graph relations at multiple representation levels, not top-k similarity search. Similarity is acceptable for finding entry points; it is the wrong tool for expansion.

> **Read Appendix A before writing a single Cypher string.** HydraDB implements a deliberate subset of OpenCypher. Most Neo4j idioms are rejected at parse time.

---

## 0. Invariants

| # | Invariant | Why |
|---|---|---|
| **I1** | Mandatory propagation is **strictly level-decreasing**; mandatory depth from an L3 seed is ≤ 2 productive hops. | Static depth bound. The one claim to defend in the video. |
| **I2** | Closure **size** is unbounded by I1 and controlled *only* by profile selection and budget admission. | Depth ≠ size. Do not overclaim. |
| **I3** | Statically computable transitive structure is precomputed into node properties at ingest, never traversed at query time. | Lets I1 stay strict without losing inheritance or constant chains. |
| **I4** | **Budgeted cost is an upper bound on emitted cost.** Node token counts describe the *canonical emitted representation*, not the raw source span. | Without this, `7,842 / 8,000` reconstructs to 9,130 and the budget is a lie. *(new in v1.1)* |
| **I5** | **Runtime provenance is revision-scoped.** Only evidence from the indexed revision is labelled *current*; matching endpoint body hashes permit reuse at lower confidence as *historical*. | An observation is evidence about a revision. Endpoint hashes are necessary but not sufficient — see §2.4. *(revised in v1.2)* |
| **I6** | **Closure preservation under enrichment.** Optional context may only be admitted together with the incremental mandatory closure it induces. | Without this, packing silently breaks the closure property the product advertises. *(new in v1.2)* |

Cut from v1: git co-change, symbol lineage across renames, cross-service HTTP edges, non-Python languages.

---

## 1. Representation levels

```
L0  absent
L1  identity      FQN + file:line. Lattice member only — NOT an emitted tier.
L2  declaration   Canonical emitted text: signature, annotations, decorator lines,
                  docstring first line, required imports, enclosing class header.
                  Classes: flattened MRO surface (§3.2).
                  Constants: folded value if evaluable, else defining expression.
L3  body          Canonical emitted text: full source + required imports + enclosing
                  class header + module globals the body reads.
```

Ordering `L0 < L1 < L2 < L3`; the level map `Symbol → Level` is a finite lattice under pointwise ordering.

### 1.1 L1 semantics — three tiers, not two

v1.0 contradicted itself on L1; v1.1 resolved that by giving L1 zero cost; **v1.2 was still wrong**, because a truncatable identity index means `is_closed(merged)` can return true while the model receives a context with unresolvable names in it. The header would claim completeness that the artifact does not have.

Three distinct things were being conflated:

| Tier | Definition | Cost | Truncatable |
|---|---|---|---|
| **L1-lattice** | Every symbol the fixpoint assigns L1. Bookkeeping; discharges the proof obligation internally. | 0 | n/a — never emitted |
| **L1-mandatory** | The subset whose FQN **appears textually** in emitted L2/L3 text but which is not itself emitted. | **budgeted** | **never** |
| **L1-hints** | Anything else worth listing (nearby siblings, sources of unemitted candidates). | reserve | yes |

> **Rule.** L1-mandatory identities are charged against the budget and are never truncated. If they do not fit, demote the profile; if they do not fit at P0, return `CLOSURE_BUDGET_EXCEEDED`. Only L1-hints use the capped reserve, and only they may set `identity_index_truncated`.

**Breaking the circularity.** L1-mandatory depends on emitted text, which is not known until after packing. Resolve it the same way I4 resolved token counts — by precomputing at ingest. Each canonical representation carries the symbols it textually references:

```
repr_L2_refs, repr_L3_refs           list[NodeId] — symbols named in that text
repr_L2_identity_tokens,
repr_L3_identity_tokens              INT — cost of rendering one identity line for each
```

Then during admission, `mandatory_identity_cost(S) = tokens(⋃ refs(n) for n in S  −  S)`, computed as set algebra over precomputed lists with no round trips and no source reads. It remains an upper bound, because emission-time deduplication can only shrink it.

**Expect this to bite.** A 300-identity closure at ~10 tokens each consumes 3,000 of an 8,000-token budget. P3 will fail more often than v1.2 implied and demotion will be common. That is the honest cost of the completeness claim, not a defect.

---

## 2. Graph schema

### 2.1 Nodes

```
(:Symbol {
  id:               INT,     // HydraDB identity. Non-negative integer. REQUIRED.
  fqn:              STRING,  // application-level unique name
  kind:             STRING,  // function | method | class | constant | module | test
  file:             STRING,
  start_line:       INT,
  end_line:         INT,
  body_hash:        STRING,  // for runtime-evidence validity (I5)
  repr_L2_text:     STRING,  // canonical emitted declaration
  repr_L2_tokens:   INT,
  repr_L2_refs:     STRING,  // JSON list[NodeId] — symbols named in repr_L2_text
  repr_L3_text:     STRING,  // canonical emitted body
  repr_L3_tokens:   INT,
  repr_L3_refs:     STRING,  // JSON list[NodeId] — symbols named in repr_L3_text
  identity_tokens:  INT,     // cost of one identity line for THIS symbol (§1.1)
  provenance_tokens:INT,     // cost of one provenance trailer line (§2.1.1, I4)
  evaluable:        BOOLEAN, // constants only
  static_value:     STRING   // constants only, null if not evaluable
})

(:Symbol:Test { ...same fields..., kind: "test" })   // DUAL-LABELLED. See below.
(:Module      { id, path, package })
```

**Tests are `:Symbol` (fixes the v1.2 I6 hole).** v1.2 declared `(:Test {...})` as a separate node type while `expand()` matched only `(x:Symbol)-[…]->(y:Symbol)`. Since §6.3 admits covering tests at **L3**, the closure of an admitted test never fired — meaning I6 failed on precisely the example that motivated it:

```
TestRefreshRotation :Test  L3
   ├── CALLS           → make_refresh_token()    ← never expanded
   └── REFERENCES_TYPE → MockClock               ← never expanded
```

Dual-labelling puts every emitted code entity through identical closure machinery. `:Test` survives as a filter for candidate typing and for `COVERS` endpoints. HydraDB supports multiple labels via `SET n:Symbol, n:Test`; **confirm the multi-label match in item 0** before ingest.

Property values in this engine are integers, floats, booleans and strings only, so list-valued fields (`repr_*_refs`) are stored as JSON strings and parsed application-side.

#### 2.1.1 Model-visible metadata is budgeted (I4)

v1.2 charged canonical source and the identity reserve, but the MCP response also ships a header and per-item provenance trailers, and those tokens reach the model. Under I4 they must be counted.

```
total_budget = source + mandatory_identities + provenance + header
```

`provenance_tokens` is precomputed per symbol; `HEADER_TOKENS = 40` is a fixed allowance. `verbose_provenance` is **opt-in** — the default response ships a one-line trailer per item, and full derivation chains are available on demand through `explain_inclusion`, which is not budget-bound because it is a separate call.

**Node identity (fixes v1.0 §2).** HydraDB nodes match on `id` and ids are non-negative integers. `fqn` cannot be the key.

```python
def node_id(fqn: str) -> int:
    return int.from_bytes(blake2b(fqn.encode(), digest_size=8).digest(), 'big') >> 1  # 63-bit
```

Deterministic, so **the ingest pipeline owns the FQN→id map and the query layer never looks an id up in the graph.** This matters more than it looks: HydraDB has no `IN` and no multi-value property lookup outside `algo.MSpaths`, so a graph-side name lookup would be a per-seed round trip. Owning the map application-side removes the need entirely.

Persist the map to `symbols.jsonl` at ingest. On collision (astronomically unlikely at 63 bits, but check anyway) probe `id+1`. **Probe deterministically:** assign ids by iterating symbols in sorted-FQN order, so a clean rebuild produces byte-identical ids and stored graphs remain comparable across runs.

#### 2.1.3 Relationship identity

`MERGE` matches relationships on `id`, so every edge needs one, and the choice is load-bearing for I5. A stable `H(type, src, dst)` for runtime edges would make each new trace **overwrite** the previous `commit_sha`, body hashes and hit counts — destroying exactly the history I5 promises to retain.

```python
eid(CALLS, s, d)              = H("CALLS", s, d)                      # static: stable
eid(REFERENCES_TYPE, s, d)    = H("REFERENCES_TYPE", s, d)            # static: stable
eid(OBSERVED_CALLS, s, d, r)  = H("OBSERVED_CALLS", s, d, r)          # r = trace_revision
eid(COVERS, t, s, r)          = H("COVERS", t, s, r)
```

Static edges are idempotent under re-ingest. Runtime edges accumulate one row per `(src, dst, revision)`; `call_sites` stays aggregated on the single static `CALLS` edge.

**Query consequence:** selecting the most recent observation for a pair needs `max()`, which this engine does not have. Fetch all revisions for the pairs in play and reduce application-side.

**Token counts are of canonical emitted text (I4).** `repr_L2_text` and `repr_L3_text` are generated at ingest by the same reconstruction code that runs at emission (§7.2), including imports, decorators and enclosing class headers. Deduplication at emission can then only make real output *smaller* than the budgeted figure. Never compute tokens from `end_line - start_line`.

### 2.2 Mandatory relations

```
-[:REFERENCES_TYPE { resolver, confidence }]->
-[:CALLS           { resolver, confidence, call_sites }]->
-[:OVERRIDES       { resolver, confidence }]->
-[:IMPLEMENTS      { resolver, confidence }]->
-[:DECORATED_BY    { resolver, confidence }]->
-[:READS_CONSTANT  { resolver, confidence }]->
```

**Extraction is hybrid syntax + semantic resolution (fixes v1.0 §2.2).** v1.0 claimed all of these come from SCIP. They do not — the SCIP protocol carries definitions, references, type definitions and implementations, but **no general-purpose `CALLS` relationship.**

```
Python AST / tree-sitter          SCIP (scip-python / Pyright)
  identifies OCCURRENCE KIND        resolves OCCURRENCE → SYMBOL
  ─────────────────────────         ────────────────────────────
  call expression            ──►    exact callee symbol      ──►  CALLS
  annotation position        ──►    exact type symbol        ──►  REFERENCES_TYPE
  decorator syntax           ──►    exact decorator symbol   ──►  DECORATED_BY
  constant read              ──►    exact constant symbol    ──►  READS_CONSTANT
  (SCIP relationships)                                       ──►  IMPLEMENTS, OVERRIDES
```

This is deterministic analysis, not heuristics, and it is a *stronger* engineering story than either tool alone: AST says what kind of occurrence it is, SCIP says what it resolves to.

`resolver ∈ {scip-python, ast+scip, tree-sitter, runtime}`; `confidence ∈ [0,1]`. `scip-python` gets `0.95`, not `1.0` — it is a production indexer with known indexing and duplicate-symbol failures. Nothing is asserted as ground truth.

`INHERITS_FROM` is **not** a mandatory relation; it is consumed at ingest by MRO flattening (§3.2). Store it for display and ranking only.

### 2.3 Evidence relations

Never trigger mandatory inclusion. Rank the optional tier only.

```
-[:OBSERVED_CALLS { id, hits, src_body_hash, dst_body_hash, commit_sha, trace_revision }]->
-[:COVERS { id, hits, call_depth, phase, directness, lines_covered,
            src_body_hash, dst_body_hash, commit_sha, trace_revision }]->
-[:READ_BY { resolver }]->
```

`COVERS` is noisy; the properties are how you de-noise it. `phase ∈ {setup, body, teardown}` from pytest hooks; `directness = 1/(1+call_depth)`. A symbol executed at depth 9 inside a DB fixture is not "tested by" that test.

### 2.4 Evidence validity rules

Two separate rules. v1.0 conflated them and got the second one wrong.

**Asymmetry (unchanged).**
> Presence of `OBSERVED_CALLS(A,B)` is positive evidence that A calls B. **Absence is not evidence that A cannot call B.** Runtime edges raise confidence on static edges and add edges static analysis missed; they never delete or downweight an unobserved static edge.

**Revision scoping (I5 — v1.0 was wrong, v1.1 overclaimed).**
> An observation is evidence about the revision it was made on. `observed in an old revision` does **not** imply `currently happens` — and matching endpoint body hashes do **not** repair that.

Counterexample that kills the v1.1 rule: `handle()` calls `service.process()`, and elsewhere `container.bind(Service, ServiceA)` becomes `container.bind(Service, ServiceB)`. Neither endpoint body changed, yet `handle → ServiceA.process` may no longer occur. Same failure mode for `COVERS` when a fixture, decorator, config value or dispatch condition changes.

So there are three states, not two:

```python
def evidence_state(edge) -> str:
    if edge.commit_sha == indexed_revision:
        return "current"                    # confidence 1.0
    if (edge.src_body_hash == node[edge.src].body_hash and
        edge.dst_body_hash == node[edge.dst].body_hash):
        return "historical_endpoints_intact" # confidence 0.6 — reusable, not confirmed
    return "stale"                           # excluded from scoring, retained as history
```

Repo-SHA-only validity would invalidate every runtime edge on any unrelated commit, and retracing costs ~20 minutes, so the middle tier is what makes evidence survive normal editing. But it is labelled honestly, and the UI says which:

```
runtime-confirmed: current (312 hits, 8 tests)
runtime-observed:  historical, endpoints unchanged since observation
```

Much harder to attack than a blanket "runtime-confirmed."

### 2.5 Indexes — two mechanisms, neither one is DDL

**v1.0 was wrong** to write `CREATE INDEX ON :Symbol(fqn)`; it is not in HydraDB's accepted clause list and is rejected at parse time. **v1.1 then overcorrected** by calling the lifecycle unknown. It is mostly documented, and the confusion came from looking for a Neo4j-style DDL statement that does not exist because it is not needed.

| Mechanism | Nature |
|---|---|
| **Property index** | Canonical database structure. Vertex and relationship property indexes are part of the graph records, and mutation commits update them in the same transaction. The planner uses them automatically. Nothing to declare. |
| **CSC traversal index** | Background accelerator built asynchronously by `graph-indexer` and published via atomic object-store pointers. Queries stay correct when it is absent or behind, because the visible WAL tail is applied over the indexed base. |

**Action for item 0** is therefore *verification*, not discovery: confirm property-selector behaviour in the local deployment (particularly `sourceLabel` / `sourceProperty` resolution for the `algo.*` procedures) and confirm the path procedures are available and returning. **Do not write ingest code before that round-trips.**

Robustness note: the design barely depends on this, because §2.1 keeps the FQN→id map application-side and all hot-path access is by `id`.

---

## 3. Ingest-time precomputation (I3, I4)

### 3.1 Constant folding

```
evaluable(c) := defining expression contains only literals and
                references to other evaluable constants
```

| Source | Result |
|---|---|
| `TIMEOUT = 30` | `evaluable: true`, `static_value: "30"` |
| `TIMEOUT = BASE * 2`, `BASE` evaluable | fold → `evaluable: true`, `static_value: "60"` |
| `TIMEOUT = int(os.getenv("TIMEOUT","30"))` | `evaluable: false` |
| `FEATURE = settings.FEATURE_X` | `evaluable: false` |

Folding runs over the constant-definition DAG once, offline; cycles mark all members non-evaluable and log.

Evaluable constants have no outgoing mandatory edges by construction, so they terminate naturally — no path-dependent terminality needed. Non-evaluable constants keep `REFERENCES_TYPE` and `CALLS`, which fire from L2 and land at L1. I1 holds in both cases.

### 3.2 MRO flattening — implement C3 yourself

**v1.0 hand-waved this.** Pyright understands inheritance internally, but the exported SCIP model does not expose a resolved-MRO surface.

Implementation (option B):
1. SCIP resolves each base-class reference to its exact symbol.
2. AST extracts each class's own fields and method signatures.
3. **Implement C3 linearization yourself** (~40 lines) over the resolved base graph.
4. Render the flattened surface into `repr_L2_text`, annotating each inherited member with its defining class.

```python
class OrderService(BaseService, Auditable):   # repr_L2_text contains:
    def process(self, order: Order) -> Result: ...   # own
    def _retry(self, fn: Callable) -> Any: ...       # from BaseService
    audit_log: AuditLog                              # from Auditable
```

Cycles or unresolvable bases → fall back to own-members-only and set `mro_partial: true`.

This is why `INHERITS_FROM` is not a closure relation: a three-deep chain would otherwise decay to L1 and lose base-class fields, and the only alternative would be an exception to I1.

### 3.3 Runtime tracing

```
pytest --collect-only            → test inventory
pytest under sys.settrace        → OBSERVED_CALLS + COVERS
```

Hook records `(caller_fqn, callee_fqn, depth, phase)`; phase from `pytest_runtest_setup` / `_call` / `_teardown`. **Stamp every emitted edge with both endpoint `body_hash` values and the `commit_sha`** (I5). Cap at 20 min wall clock per repo; partial traces are fine under §2.4. Serialize to JSONL and load as a separate pass so a trace failure never blocks the static graph.

---

## 4. The propagation table

*If X is included at level ℓ, Y must be included at level ≥ f(edge, ℓ).*

| Source | Edge | Target | Rationale |
|---|---|---|---|
| L3 | `REFERENCES_TYPE` | **L2** | Cannot read the body without the type's shape |
| L3 | `CALLS` | **L2** | Need the callee contract, not its implementation |
| L3 | `OVERRIDES` | **L2** | Need the contract being overridden |
| L3 | `IMPLEMENTS` | **L2** | Need the protocol/ABC surface |
| L3 | `DECORATED_BY` | **L2** | Decorator changes the call contract |
| L3 | `READS_CONSTANT` (evaluable) | **L2** | Value is the content; terminal by §3.1 |
| L3 | `READS_CONSTANT` (non-evaluable) | **L2** | Expression is the content; its edges fire from L2 |
| L2 | *(any mandatory)* | **L1** | Name suffices to avoid a dangling reference |
| L1 | *(any)* | **L0** | Terminal |

**Every row strictly decreases. No exceptions.** Nothing from §2.3 appears here.

**This table is derived from Python semantics, not tuned on benchmark results** (§9.4). It is a claim about what a reader needs in order to understand a call site, defensible from first principles.

---

## 5. Fixpoint algorithm

```python
def closure(seeds: dict[NodeId, Level], profile: Profile) -> dict[NodeId, Level]:
    """
    Least fixpoint over the finite lattice (Symbol -> Level), pointwise ordered.
    Monotone rules + finite lattice => convergence (Kleene).
    Strict decrease => at most 2 productive hops from any L3 seed.
    """
    level = dict(seeds)
    frontier = [n for n, lv in seeds.items() if lv > L1]

    for _hop in range(2):                       # depth bound is structural, not a cutoff
        if not frontier:
            break
        edges = expand(frontier)                # §5.1 — 6 batched round trips
        next_frontier = []
        for src, edge_type, dst, meta in edges:
            required = PROPAGATION[edge_type][level[src]]
            required = profile.adjust(edge_type, required)
            if required > level.get(dst, L0):
                level[dst] = required           # levels only ever rise
                provenance[dst].append(Reason(via=src, edge=edge_type,
                                              rule=f"{edge_type}({level[src]})->{required}"))
                if required > L1:
                    next_frontier.append(dst)
        frontier = next_frontier
    return level
```

### 5.1 Frontier expansion — corrected for HydraDB

**v1.0's query was invalid on three counts:** `IN` in `WHERE`, multiple relationship types in one pattern, and `type(r)` as a projection. All three are rejected at parse time.

The working form uses `UNWIND` with a parameter list of maps, one query per edge type:

```cypher
UNWIND $rows AS row
  MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol)
  RETURN x.id AS src, y.id AS dst, y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3
```

```python
HARD_EDGES = ['REFERENCES_TYPE','CALLS','OVERRIDES',
              'IMPLEMENTS','DECORATED_BY','READS_CONSTANT']

def expand(frontier: list[NodeId]):
    rows = [{"v": n} for n in frontier]
    out = []
    for et in HARD_EDGES:                       # one type per pattern — engine constraint
        q = (f"UNWIND $rows AS row "
             f"MATCH (x:Symbol {{id: row.v}})-[:{et}]->(y:Symbol) "
             f"RETURN x.id AS src, y.id AS dst, "
             f"y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3")
        for r in session.run(q, rows=rows):     # edge type known from the loop, not projected
            out.append((r["src"], et, r["dst"], r))
    return out
```

**Cost: six typed query templates per productive hop — normally 12 batched requests for an unchunked two-hop closure.** Not a traversal loop, and independent of *graph* size. It is **not** independent of frontier size: HydraDB enforces result limits, deadlines, backpressure and cache budgets, so a large frontier is chunked into batches of `B` rows and the count becomes `6 × ceil(|frontier|/B) × hops`. Set `B` from what the spike finds; start at 500.

The depth bound is structural and unaffected by chunking. Do not attach a constant-network-cost claim to it — the v1.0 "two queries" figure was wrong, and an unqualified "12 regardless of frontier" would be wrong in the same direction.

Constraints this obeys, all documented: one relationship type per pattern; `UNWIND` input is a parameter holding a list of maps; `UNWIND MATCH` ends in `RETURN`, takes no `WHERE`, no `OPTIONAL`, one hop, directed. **`UNWIND` only works through the Bolt/HTTP client transport — the in-process shard API rejects it, with an error message about row execution that does not mention batching.**

`algo.SSpaths` remains available if a frontier ever gets pathological, but at depth 2 the `UNWIND` form is simpler and faster. `algo.MSpaths` is for §6.3, where you genuinely have both a source set and a target set.

---

## 6. Budget admission

### 6.1 Profiles

Monotone family; each profile's level assignment is pointwise ≤ the previous, so token cost is monotone and a linear scan is valid.

| Profile | Seeds | 1st hop | 2nd hop |
|---|---|---|---|
| **P3 FULL** | L3 | L2 | L1 |
| **P2 COMPACT** | L3 | L2 (direct callees/types), L1 otherwise | L0 |
| **P1 MINIMAL** | L3 | L1 | L0 |
| **P0 FLOOR** | L2 | L1 | L0 |

### 6.2 Selection

```python
HINT_RESERVE   = 0.05
HEADER_TOKENS  = 40

def cost(level_map) -> int:
    """Everything the model will see, per I4."""
    src  = sum(tok_L3(n) if lv == L3 else tok_L2(n) if lv == L2 else 0
               for n, lv in level_map.items())
    prov = sum(provenance_tokens(n) for n, lv in level_map.items() if lv >= L2)
    emitted = {n for n, lv in level_map.items() if lv >= L2}
    dangling = union(refs_at(n, lv) for n, lv in level_map.items()) - emitted
    ident = sum(identity_tokens(n) for n in dangling)      # L1-mandatory, never truncated
    return src + prov + ident + HEADER_TOKENS

def compile_context(task, budget):
    hint_reserve = int(budget * HINT_RESERVE)
    effective    = budget - hint_reserve
    seeds        = resolve_seeds(task)                     # §6.4
    for profile in (P3, P2, P1, P0):
        c = closure(seeds, profile)
        floor = cost(c)
        if floor <= effective:
            merged = pack(effective - floor, c)            # §6.3 — returns c + bundles
            hints  = identity_hints(merged, cap=hint_reserve)  # §7.3 — truncatable
            assert is_closed(merged)                       # I6, checked in CI
            assert cost(merged) + tokens(hints) <= budget  # I4, checked in CI
            return Context(merged, hints, profile,
                           status="OK" if profile is P3 else f"DEMOTED:{profile}")
    return Context(status="CLOSURE_BUDGET_EXCEEDED",
                   deficit=cost(closure(seeds, P0)) - effective,
                   suggestion="narrow the task or raise the budget")
```

Note `cost()` now includes mandatory identities, so the L1-mandatory tier is admission-controlled rather than truncated after the fact (§1.1). Only hints use the reserve.

Ordering matters: the identity index depends on what was emitted, so it cannot be computed before packing. Reserve up front, pack against the remainder, build and truncate the index last.

Profiles are coarse by design; residual budget is absorbed by optional packing, so a 9k floor against 16k wastes nothing.

`CLOSURE_BUDGET_EXCEEDED` is a first-class return value and a genuine product feature: *"your task's mandatory dependency floor is 22k tokens; it is too broad for one shot."* No top-k system can say this.

### 6.3 Optional packing as closure bundles (I6)

**The v1.1 bug.** Optional items were emitted at L2/L3 without participating in closure. An admitted test referencing `RefreshTokenFactory`, `MockClock` and `AuthFixture` left the final context *not closed* while the header still printed `Structural closure: complete`. The cost model was wrong in the same way: `value = score/tokens_at(y)` ignored the mandatory dependencies `y` drags in.

**The rule.** Nothing is emitted at L2 or L3 without its induced mandatory closure. Optional context is admitted as a **bundle**, never as a bare item.

#### Candidate admission levels

A covering test at L2 is useless, so candidates carry a type-specific level:

| Candidate type | Admitted at | Ranked by |
|---|---|---|
| Covering test | **L3** | `directness × phase_weight × hits_idf` |
| Runtime-observed caller | **L2** | evidence state × path proximity |
| Static caller of a seed | **L2** | path proximity |
| Sibling implementation | **L2** | shared-interface proximity |

#### Bundle arithmetic

```python
def bundle(current: dict[NodeId, Level], cand: NodeId,
           lvl: Level, profile: Profile) -> tuple[dict, int]:
    expanded   = closure(seeds=current | {cand: lvl}, profile=profile)
    delta      = {n: l for n, l in expanded.items() if l > current.get(n, L0)}
    delta_cost = cost(expanded) - cost(current)
    return delta, delta_cost

value(cand) = score(cand) / delta_cost(cand | current)
```

#### Making it fast enough to ship

Naive recomputation is ~200 candidates × one closure each × 12 requests ≈ 2,400 round trips per compile. Unusable for an interactive MCP tool. Two mitigations; implement the first, keep the second in reserve.

**1. Precompute the candidate envelope (do this).** Depth is ≤ 2 and candidates already sit in the evidence neighbourhood, so fetch the mandatory neighbourhoods of *all* candidates in one extra pass — 12 more batched requests — then every bundle computation is in-memory set algebra with zero further round trips.

```
12 requests   mandatory closure of seeds
12 requests   mandatory envelope of all candidates
────────────
24 requests   total, chunking aside
```

**2. Lazy greedy, if the envelope still costs too much.** `delta_cost(y | S)` is non-increasing in `S` — the more already included, the less new material `y` drags in — so true value only *rises* during packing and a stale value is a **lower** bound. That inverts standard CELF pruning. Use `score(y) / tokens_at(y)` as the heap priority instead: since `tokens_at(y) ≤ delta_cost(y|S)` always, it is an admissible **upper** bound, so compute the exact bundle only when a candidate reaches the top of the heap, then re-heap. Correct, and typically 20–40 exact evaluations instead of 200.

#### Loop

```python
def pack(remaining: int, mandatory: dict) -> dict:
    current  = dict(mandatory)
    envelope = candidate_envelope(candidates)      # one batched pass
    while True:
        best = best_delta = best_cost = None
        for c in candidates_not_in(current):
            delta, dcost = bundle_local(current, c, LEVEL[c.type], envelope)
            if dcost <= remaining and (best is None or
                                       score(c)/dcost > score(best)/best_cost):
                best, best_delta, best_cost = c, delta, dcost
        if best is None:
            return current
        current.update(best_delta)                 # I6: bundle, not bare item
        remaining -= best_cost
```

Because bundles share dependencies, admitting one candidate can make the next cheaper. That is a feature: the packer naturally clusters context around a coherent region of the graph rather than scattering it.

#### Scoring

```
score(y) = relevance(y) × idf(y) × confidence(y)
idf(y)   = log(N / (1 + degree(y)))          # hub suppression
```

`idf` is what stops `Logger`, `BaseModel` and `Config` dominating — representation decay limits their *token* damage, not their *score*, and those are separate problems. `confidence(y)` reads the §2.4 evidence state, so historical-but-intact evidence contributes at 0.6.

> **Do not blur:** closure inclusion is decided by semantic rules; ranking is decided by scores. A high score never forces inclusion; a mandatory rule never consults a score. I6 does not violate this — a bundle's mandatory members are included by *rule*, triggered by a candidate that scoring merely proposed.

`algo.MSpaths` fits here — given a seed set and a candidate set, resolve bounded paths pairwise in one server-side call and use path count and length as relevance features. `min`/`max` aggregates do not exist in this engine; compute extrema application-side.

**Known non-optimality, accepted:** a lower profile with a richer optional set could occasionally beat a higher profile with a thin one. §6.2 does not search that space. Document it; do not fix it.

### 6.4 Seed resolution

1. **Traceback parsing** — highest precision, free when the issue contains one.
2. **BM25** over identifiers, paths, docstrings.
3. **Embedding top-k** over `repr_L2_text`.
4. **LLM proposal** from a repo map.
5. **Connectivity rerank** — candidates mutually reachable within 2 hops outrank isolated ones. The graph-native part.

Top 5–8 become L3 seeds. FQN→id via the application-side map (§2.1), never a graph query. Say plainly in the video that similarity is used for entry and rejected for expansion; the honesty is worth more than purity.

---

## 7. Emission

### 7.1 Ordering

Seeds (L3) → direct L2 dependencies grouped by file → optional items → identity index.

### 7.2 Canonical source reconstruction

**Runs at ingest, cached as `repr_L2_text` / `repr_L3_text` (I4).** Emission reads the cache; it does not re-derive.

For each symbol:
- prepend only the `import` lines the body actually needs, from SCIP references — not the file's full import block
- include decorators
- for methods, include the enclosing `class` header and relevant field declarations
- include module-level globals the body reads
- preserve the docstring, strip unrelated comments

Budget a full day. It is invisible in every architecture diagram and it is where slices break. Emission-time dedup (two symbols sharing an import) can only shrink output below the budgeted figure, which is what makes I4 an upper bound rather than an estimate.

### 7.3 Identity output — two sections

**Mandatory identities** (§1.1, L1-mandatory): every FQN appearing in emitted L2/L3 text that is not itself emitted. Rendered as `fqn — file:line`. Charged in `cost()`, admission-controlled, **never truncated**. If they did not fit, the profile was demoted or the call returned `CLOSURE_BUDGET_EXCEEDED` before reaching here.

**Identity hints** (L1-hints): anything else worth listing. Charged against `HINT_RESERVE`, truncated to fit, sets `identity_index_truncated: true` when cut. Truncating hints does not affect the closure claim, which is the whole point of separating them.

### 7.4 Provenance

```
TokenPolicy.rotate                                    [L2, 47 tokens]
  ← AuthService.refresh  CALLS  (rule: CALLS(L3)→L2)
  runtime-confirmed: current (312 hits, 8 tests)
```

Simultaneously the debugging tool, the demo, and the answer to "how do you know this is the right context."

---

## 8. Product surface

Mathematics stays underneath. Default MCP response header:

```
Compiled context · 11 symbols · 7,842 / 8,000 tokens
4 mandatory dependencies · 3 runtime-confirmed · 2 relevant tests
Structural closure: complete (P3 FULL)
```

| Tool | Purpose |
|---|---|
| `compile_context(task, budget)` | The product |
| `explain_inclusion(fqn)` | Provenance chain for one symbol |
| `impact_cone(fqn)` | Reverse closure — the **potentially affected** set |

`impact_cone` is ~30 lines over the same machinery and demos beautifully. **Say "potentially affected", never "what breaks."** Reverse graph reachability establishes that a symbol *could* be affected, not that it will be; the tool computes an over-approximation and should present itself as one. Overclaiming here is the easiest thing for a judge to puncture, and the honest version is still impressive.

### 8.1 Reindexing after code changes

**v1.1 downgrade.** v1.0 assumed a `scip-python` incremental file-indexing mode. The documented usage is a whole-project `scip-python index .`; no incremental interface is documented.

```
v1  (ship this):     full semantic reindex after mutation. Slow, correct, honest.
v2  (if verified):   Pyright-internal incremental integration, or custom invalidation:
                     changed file → reindex → collect symbols whose signature or
                     existence changed → query graph for referencing files →
                     re-resolve that set → delete + reinsert edges for the union
```

Note the v2 sketch is the *correct* invalidation logic regardless — file-level reparse alone leaves stale incoming edges from every file that imported a renamed symbol. The question is only whether SCIP can be driven incrementally.

Runtime edges are **not** deleted on reindex. They are re-validated by body hash (§2.4) and retained as history otherwise.

---

## 9. Evaluation protocol — freeze before running

### 9.1 Arms

Identical seeds, model, and token budget across all four:

| Arm | Retrieval |
|---|---|
| **A. Vector** | Embedding top-k over chunks, honestly built |
| **B. Graph top-k** | Graph, ranked, no closure |
| **C. Closure** | This system |
| **D. Gold-edit-surface** | Files touched by the gold patch |

Arm B is load-bearing. Without it, a win means "graphs help," not "closure helps."

Arm D is deliberately **not** called an oracle or an upper bound. §9.3 concedes that gold-patch files are the developer's final edit, not the context they needed to read, so a system can legitimately exceed it. Naming it an upper bound would contradict our own caveat.

### 9.2 Confirmatory vs descriptive (v1.2 contradicted itself here)

v1.2 said "run on all of Verified" in §9.2 while §9.4 required repository-held-out tuning. Those cannot both describe the headline number without reintroducing the leakage §9.4 exists to prevent.

**Primary / confirmatory — held-out test repositories only.** Four arms, dose-response by gold-patch file count. Every parameter frozen beforehand. This is the result that goes in the video and the README.

**Secondary / descriptive — all of Verified, after freezing.** Reported and explicitly labelled *descriptive*, including the dev repositories. Useful for coverage and failure analysis; never presented as the confirmatory finding.

The hypothesis, tested on the held-out set, is a **monotone gradient in `C − B` as patch breadth grows**, not a headline number. A gradient in the predicted direction is strong evidence even if the pooled difference is flat, and it cannot be dismissed as benchmark selection.

### 9.3 Secondary

- Gold-file recall@k (useful, not sufficient — the gold patch is the final edit, not the developer's reading set)
- Tokens to reach full recall
- **Closure violation rate** — does the emitted slice resolve without dangling references? Report as a *soundness check on our own pipeline*, run in CI. Do **not** score the vector baseline on it; that would be a strawman.
- **Budget soundness**, two figures rather than one confused one:
  - `token_margin = emitted_tokens − budget`, which must satisfy `token_margin ≤ 0` on every instance (I4)
  - `budget_overrun_rate = fraction of instances with token_margin > 0`, which must be `0%`
- **Closure preservation rate** — fraction of emitted contexts where `is_closed(merged)` holds (I6). Must be 100%. Assert it in CI, not just in the eval; this is the property the product's header claims.

### 9.4 Split discipline

- **Repository-held-out**, not random-instance. Verified concentrates in few repos; random splitting leaks graph shape and you tune on Django then "validate" on Django.
- Propagation table (§4) derived from Python semantics, **never** tuned on results. This is the main defence against leakage.
- Dev set touches numeric knobs only: seed `k`, evidence weights, `idf` base, packing parameters, `HINT_RESERVE`, historical-evidence confidence (0.6).
- **Commit `frozen_params.json` before the test run**; cite the SHA in the README.

### 9.5 Limitations to state, not hide

- ProMax averages 11.4 modified files but is a *refactoring* benchmark, and refactoring is definitionally a reference-closure task. Winning there is closer to tautology than evidence. Use 2–3 Python instances as **qualitative case studies** only.
- The ProMax paper cites an audit finding ~60% of unsolved Verified instances have flawed tests, and possible gold-patch memorization. This inflates all arms including the gold-edit-surface arm. One README line; no video time.
- A negative result is valid. "No pooled effect, clean gradient by patch breadth" is a stronger submission than a marginal win on a self-selected metric. Decide the framing now, not at 3am on the 20th.

---

## 10. Build order

| # | Item | Blocks |
|---|---|---|
| **0** | **HydraDB spike**: `just smoke` green, `RUST_MIN_STACK=33554432` exported, one `UNWIND MERGE` batch and one `UNWIND MATCH … RETURN` round-tripped over Bolt, **multi-label `SET n:Symbol, n:Test` written and matched by `(x:Symbol)`**, property-selector behaviour verified (§2.5) | everything |
| 1 | SCIP + AST hybrid extractor → nodes with deterministic ids | everything |
| 2 | Constant folding, C3 MRO flattening, canonical repr + token precompute | I1, I3, I4 |
| 3 | Ingest via `UNWIND MERGE … SET` batches | everything |
| 4 | Fixpoint + propagation table + `expand()` | the thesis |
| 5 | Profiles + budget admission + identity reserve | `compile_context` |
| 6 | Emission from cached reprs | usable output |
| 7 | MCP server + `explain_inclusion` | the product |
| 8 | Hybrid seed resolution | eval validity |
| 9 | Runtime tracing → evidence edges with body hashes | differentiation |
| 10 | Four-arm eval, dev/test frozen | quality of results |
| 11 | `impact_cone` | demo polish |
| 12 | Full reindex path | honesty about "IDE assistant" |

Item 0 is now a hard gate. 1–7 are the product; 8–10 are the paper. Ship 1–7 before starting 10 — a rigorous ablation with no working artifact loses to a working artifact with a decent ablation.

---

## Appendix A — HydraDB Cypher constraints

Give this section to Codex/Claude Code verbatim. Everything here is from `cypher-compat.md`; anything outside the subset is rejected at parse time.

### Hard rejections
| Construct | Status |
|---|---|
| `IN`, `CONTAINS`, `ENDS WITH`, `IS NULL` in `WHERE` | **rejected** |
| Multiple relationship types in one pattern `-[:A\|B]->` | **rejected** |
| Undirected patterns `-[:A]-` | **rejected** |
| `type(r)`, `labels(n)`, any non-property projection | **rejected** |
| `RETURN *` | **rejected** |
| Unbounded variable length `*` or `*1..` | **rejected** (max is required) |
| `CREATE INDEX` and other DDL | not in the accepted clause list |
| `WITH` that aliases, filters, orders, or drops a binding | **rejected** |
| `min()`, `max()` | **rejected** (only `count`, `sum`, `avg`, `collect`) |
| `ON CREATE` / `ON MATCH` on `MERGE` | **rejected** |
| More than one statement per request | **rejected** |
| Inline literal list as `UNWIND` input | **rejected** (parameter only) |
| `UNWIND` via in-process shard API | **rejected** — Bolt/HTTP transport only |

### What you actually have
- `MATCH` with one type per pattern, directed, nodes matched on `id`
- `WHERE`: `AND`/`OR`/`NOT` over `=`, `<>`, `<`, `>`, `<=`, `>=`, `STARTS WITH` — `STARTS WITH` is the only string operator and takes a literal or parameter
- `RETURN` property projections and aggregates, with alias, `DISTINCT`, `ORDER BY`, `SKIP`, `LIMIT`
- `UNION` / `UNION ALL` for reads, same columns per arm, no nesting, no mixing
- `UNWIND $rows AS row` batches for reads and writes — the workhorse
- Bounded variable-length `*1..k`, `k` required
- `algo.SPpaths` / `SSpaths` / `MSpaths`, yielding `path`, `pathWeight`, `pathCost` only
- `EXPLAIN` via the shard API's `explain_opencypher_rows` — parser rejections surface identically, so it is a cheap pre-flight check

### Canonical write forms
```cypher
-- vertex upsert: MERGE by id, then SET. Folding properties into the
-- MERGE pattern is rejected, because the pattern is the identity matched on.
UNWIND $rows AS row
  MERGE (n {id: row.v})
  SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind,
      n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3

-- edges, one type per batch
UNWIND $rows AS row
  MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst})
  MERGE (s)-[r:CALLS {id: row.eid}]->(d)
  SET r.resolver = row.resolver, r.confidence = row.conf
-- row.eid comes from §2.1.3. Static edges use a stable H(type,src,dst);
-- runtime edges MUST include the trace revision, or each new trace
-- overwrites the previous observation and destroys I5's history.
```

### Pre-flight checklist
1. Does every relationship pattern name exactly one type and a direction?
2. Does `WHERE` use only the seven supported comparisons?
3. Is every `RETURN` item a `binding.property` or an aggregate?
4. Are all node matches by `id`?
5. Is `UNWIND` input a parameter holding a list of maps, going over Bolt/HTTP?
6. Is every variable-length bound explicit?
7. Run it through `EXPLAIN` before it touches data.
