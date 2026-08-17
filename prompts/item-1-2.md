# Task: Items 1–2 — Extraction Layer

You are the extraction engineer for **Context Compiler**, a Hack Hydra Track 2B project. Another agent is building the graph layer in parallel. You will never touch their code and they will never touch yours.

---

## 0. Source of truth

Read completely, before anything else:

```
docs/specs/context-compiler-v1.3.md
docs/specs/amendment-a1.md
docs/spikes/hydradb-item-0-results.md
```

Sections that govern your work:

- **§2.1** — node schema and the fields you must produce
- **§2.2** — mandatory relations, and the hybrid AST + SCIP extraction architecture
- **§3.1** — constant folding
- **§3.2** — MRO flattening (C3, implemented by us)
- **§7.2** — canonical source reconstruction
- **§0 invariants I3 and I4** — these are your invariants. You own them.
- **Amendment A1.1** — `symbols.jsonl` is now the **runtime cost table**, not just an ingest artifact. This raises the stakes on getting the scalar fields right.

Do not redesign. If running software proves a spec assumption false, document the exact observed failure and stop at the smallest boundary — do not design around it silently. That is how Item 0 was run and it worked.

---

## 1. Strict scope boundary

### You own
```
src/context_compiler/extract/
tests/unit/
docs/spikes/scip-item-1-results.md
```

### You must not touch
```
src/context_compiler/graph/      # other agent
scripts/                         # other agent
tests/integration/               # other agent
docs/specs/                      # frozen
~/hydradb                        # not yours
```

### You must not implement
HydraDB, Cypher, any database call, the closure fixpoint, budget admission, emission, MCP, seed resolution, runtime tracing (`sys.settrace` is **Item 9**, not now), or evaluation.

**Your entire output is two files on disk.** If you find yourself writing a graph query, you have gone out of scope.

---

## 2. Environment — verified facts, do not re-verify

Ubuntu **26.04 LTS (resolute)** under WSL2. Host: Intel i5-1340P, WSL capped at 10 GB RAM / 8 processors.

| Component | Status |
|---|---|
| Python | 3.14.4 |
| Node.js | 22.x (installed) |
| `.venv` | exists at `~/context-compiler/.venv` |
| `neo4j`, `pytest` | installed in `.venv` |

**PEP 668 is enforced.** Plain `pip install` is refused. Always:
```bash
source .venv/bin/activate
```

**Filesystem:** work only in `~/`. Never under `/mnt/c/` — 5–10× slower and breaks file watching.

**No sudo.** If something needs a system package, stop and report which one.

**Ignore:** systemd printing `Failed to connect to system scope bus` during apt operations. Normal in WSL.

---

## 3. Target repositories

**Primary (develop against):** `requests` — small, clean, pure Python, present in SWE-bench Verified.

**Secondary (must also complete):** `flask`.

**Scale check (timing only, at the end):** clone one large repo (`sympy` or `django`) and record wall-clock extraction time and peak memory. Do not debug it if it is slow; just record the number. We need to know whether a full-scale run is feasible before Item 10.

Clone targets into `~/targets/`, never into this repository. Add `targets/` to `.gitignore`.

---

## 4. Phase 1 — SCIP spike (time-boxed: 90 minutes)

**Do this before writing any extractor code.** SCIP is the load-bearing external dependency and it can fail on real codebases.

1. Install the indexer:
   ```bash
   npm install -g @sourcegraph/scip-python
   ```
2. Run it against `requests`. The documented usage is a whole-project index (`scip-python index .`); there is **no documented incremental mode** — see spec §8.1, which already downgrades to full reindex for v1.
3. Determine how to **read** the resulting `index.scip`. It is protobuf. Preferred path is the `scip` CLI from `sourcegraph/scip` releases, which offers a JSON conversion. Investigate and record what actually works.
4. Establish the mapping from **SCIP symbol strings** to our **FQN** format. SCIP emits things like `scip-python python . . requests/sessions/Session#get().`; we need `requests.sessions.Session.get`. This mapping is a real task with real edge cases (properties, overloads, `__init__`, module-level code, locals). Write it down.

**Escalation:** if `scip-python` cannot index `requests` within 90 minutes, STOP and report. Do not fall back to AST-only silently — that would eliminate the semantic resolution the whole architecture depends on, and it is a decision for the spec owner. Write `docs/spikes/scip-item-1-results.md` with exact commands and errors.

Record in that spike doc:
```
scip-python version:
Index command:
Index read method:
Wall clock to index `requests`:
index.scip size:
SCIP symbol -> FQN mapping rules:
Symbols emitted / resolution failures:
Known scip-python failures encountered:
```

---

## 5. Phase 2 — Hybrid extractor (§2.2)

Neither tool alone is sufficient. **SCIP has no general-purpose `CALLS` relationship** — it carries definitions, references, type definitions and implementations. The architecture is:

```
Python AST                        SCIP / Pyright
  classifies the OCCURRENCE         resolves it to a SYMBOL
  ────────────────────────          ───────────────────────
  call expression            ──►    exact callee      ──►  CALLS
  annotation position        ──►    exact type        ──►  REFERENCES_TYPE
  decorator syntax           ──►    exact decorator   ──►  DECORATED_BY
  constant read              ──►    exact constant    ──►  READS_CONSTANT
  (SCIP relationships direct)                         ──►  IMPLEMENTS, OVERRIDES
```

Every edge carries `resolver` and `confidence`. `scip-python` gets **0.95, not 1.0** — it is a production indexer with known indexing and duplicate-symbol failures. Anything AST-only and unresolved gets a lower confidence and an honest resolver tag. Never assert ground truth.

`INHERITS_FROM` is **not** a mandatory edge. It is consumed by MRO flattening in Phase 4. You may emit it for display, marked non-mandatory.

---

## 6. Phase 3 — Constant folding (§3.1, invariant I3)

```
evaluable(c) := defining expression contains only literals and
                references to other evaluable constants
```

| Source | Result |
|---|---|
| `TIMEOUT = 30` | `evaluable: true`, `static_value: "30"` |
| `TIMEOUT = BASE * 2`, `BASE` evaluable | fold → `evaluable: true`, `static_value: "60"` |
| `TIMEOUT = int(os.getenv("TIMEOUT","30"))` | `evaluable: false`, `static_value: null` |
| `FEATURE = settings.FEATURE_X` | `evaluable: false` |

Fold over the constant-definition DAG once, offline. **Cycles → mark all members non-evaluable and log.** Never evaluate arbitrary expressions; literal arithmetic on folded literals only. Do not `eval()` user code.

Why this matters: evaluable constants have no outgoing mandatory edges, which is what makes invariant I1's strict level-decrease hold without a special case.

---

## 7. Phase 4 — C3 MRO flattening (§3.2, invariant I3)

Pyright understands inheritance internally, but the **exported SCIP model does not expose a resolved MRO surface**. Implement it:

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

This is why `INHERITS_FROM` is not traversed at query time: a three-deep chain would decay to L1 and lose base-class fields, and the alternative would be an exception to I1.

---

## 8. Phase 5 — Canonical representations (§7.2, invariant I4)

**This is the phase that will take longest and the one most likely to be underestimated.** Selecting a symbol is not the same as producing valid source.

For each symbol produce two canonical texts:

**`repr_L2_text`** — signature, type annotations, decorator lines, first docstring line, required imports, enclosing class header. For classes: the flattened MRO surface. For constants: folded value if evaluable, else the defining expression.

**`repr_L3_text`** — full source body, plus:
- only the `import` lines the body actually needs (from SCIP references — **not** the file's whole import block)
- decorators
- for methods, the enclosing `class` header and relevant field declarations
- module-level globals the body reads
- docstring preserved, unrelated comments stripped

**Invariant I4:** token counts must describe these canonical texts, never `end_line - start_line`. Budgeted cost must be an upper bound on emitted cost. Emission-time deduplication can only shrink the result — never grow it. If you cannot guarantee that for some construct, over-count rather than under-count, and note it.

Also emit, per representation, the list of symbols it **textually references** (`repr_L2_refs`, `repr_L3_refs`). Spec §1.1 uses these to compute mandatory L1 identity cost without a second pass, and it cannot be done later.

Tokenizer: use `tiktoken` with `cl100k_base` as the stand-in. **Wrap it behind a single `count_tokens(text) -> int` function** so it can be swapped for the real agent tokenizer later without touching the extractor.

---

## 9. The JSONL contract — FROZEN, do not deviate

This is the interface with the graph agent. Field names are exact. If you believe a field is wrong, **stop and ask** — do not rename.

Node id is deterministic and both agents compute it identically:

```python
def node_id(fqn: str) -> int:
    return int.from_bytes(blake2b(fqn.encode(), digest_size=8).digest(), 'big') >> 1

# Assign ids iterating symbols in SORTED FQN ORDER so clean rebuilds are
# byte-identical. On collision, probe id+1 and log.
```

### `symbols.jsonl` — one JSON object per line

```json
{
  "id": 4611686018427387904,
  "fqn": "requests.sessions.Session.get",
  "kind": "method",
  "file": "requests/sessions.py",
  "start_line": 542,
  "end_line": 561,
  "body_hash": "sha256:ab12…",
  "repr_L2_text": "def get(self, url, **kwargs) -> Response: ...",
  "repr_L2_tokens": 47,
  "repr_L2_refs": [123, 456],
  "repr_L3_text": "from .models import Response\n\ndef get(self, …",
  "repr_L3_tokens": 312,
  "repr_L3_refs": [123, 456, 789],
  "identity_tokens": 11,
  "provenance_tokens": 15,
  "evaluable": null,
  "static_value": null,
  "mro_partial": false
}
```

`kind ∈ {function, method, class, constant, module, test}`.
`evaluable` and `static_value` are `null` except for constants.
Emit `kind: "test"` for test functions — the graph agent dual-labels them (§2.1, invariant I6).

### `edges.jsonl` — one JSON object per line

```json
{
  "type": "CALLS",
  "src": 4611686018427387904,
  "dst": 1152921504606846976,
  "resolver": "ast+scip",
  "confidence": 0.95,
  "call_sites": 3
}
```

`type ∈ {REFERENCES_TYPE, CALLS, OVERRIDES, IMPLEMENTS, DECORATED_BY, READS_CONSTANT}` for mandatory edges, plus `INHERITS_FROM` marked `"mandatory": false`.
`resolver ∈ {scip-python, ast+scip, tree-sitter}`.
`call_sites` is `CALLS` only.

Do **not** emit `id` on edges — the graph agent derives `eid()` from `(type, src, dst)` per §2.1.3.

Write the contract to `docs/specs/jsonl-contract.md` as your first commit, so the other agent can read it immediately.

---

## 10. Deliverables

```
docs/specs/jsonl-contract.md                    (first commit)
docs/spikes/scip-item-1-results.md
src/context_compiler/extract/__init__.py
src/context_compiler/extract/scip_reader.py
src/context_compiler/extract/ast_occurrences.py
src/context_compiler/extract/constants.py
src/context_compiler/extract/mro.py
src/context_compiler/extract/representations.py
src/context_compiler/extract/pipeline.py         # CLI entry point
tests/unit/…                                     # see below
```

CLI:
```bash
python -m context_compiler.extract.pipeline --repo ~/targets/requests --out ./out/
# writes out/symbols.jsonl and out/edges.jsonl
```

---

## 11. Required tests

**Unit** — small, hand-written fixtures, fast:
- constant folding: literal, folded chain, non-evaluable env var, cycle
- C3 MRO: diamond inheritance, three-deep chain, unresolvable base → `mro_partial`
- FQN mapping: method, nested class, module-level function, `__init__`
- canonical repr: method gets enclosing class header; only needed imports included
- `node_id` determinism: same FQN → same id across runs

**End-to-end** on `requests`:
- pipeline completes without exception
- node and edge counts are non-zero and plausible
- **no edge references a missing symbol id** (referential integrity — this is the one that catches real bugs)
- every `repr_*_refs` entry resolves to a symbol in `symbols.jsonl`
- all ids unique
- re-running produces byte-identical output (determinism)

---

## 12. Time box and escalation

- **Phase 1 (SCIP spike): 90 minutes.** Not indexing `requests`? Stop and report.
- **Phase 5 (canonical reprs): budget a full day.** It is invisible in every architecture diagram and it is where slices break. Do not rush it to reach the end-to-end test.

**Report at each phase boundary, not only at the end.** If a phase fails, stop at that phase.

Do not proceed past Item 2. Do not start runtime tracing.

---

## 13. Acceptance gate

Not complete because code was written. Before finishing you must have:

1. run the extractor end-to-end on `requests` **and** `flask`
2. produced both JSONL files, schema-conformant
3. passed all unit and end-to-end tests, including referential integrity
4. proven determinism — two runs, identical bytes
5. recorded scale-check timing on one large repo
6. recorded every SCIP resolution failure rate
7. recorded any discrepancy between observed behaviour and spec v1.3 / Amendment A1

If SCIP or Python behaves differently from the spec, **document the exact observed behaviour and stop** — do not silently redesign.

---

## 14. Final report

- implementation summary
- commands to reproduce
- test output summary
- files created or modified
- extraction stats: symbols, edges by type, SCIP resolution rate, timings for all three repos
- unresolved issues
- recommended commit message
