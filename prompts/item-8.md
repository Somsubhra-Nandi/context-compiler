# Prompt: Item 8 — Seed resolution (scoped), plus three A6-bis corrections

**Read first:** `docs/specs/context-compiler-v1.3.md` §6.4 and §2.1,
`src/context_compiler/mcp/seeds.py`, `src/context_compiler/mcp/server.py`
(`_resolve_ids`), `docs/specs/amendment-a3.md` §3.5, `docs/specs/amendment-a6.md`
§A6.6, `docs/specs/baseline-vector.md` if present.

**Time-box: 3.5 hours total.** Task 0 is ~30 min; Item 8 gets the remaining 3.
Stop and report rather than working around. This is the last item blocking a
demo recording, so scope discipline matters more than completeness.

---

## Task 0 — Three corrections to A6-bis (do these first, they are quick)

These are documentation and labelling defects in work already done, not new
measurement. All three land in `docs/specs/amendment-a6.md`.

**0.1 — State which test suites ran.** A6 reports "291 passed, 3 skipped";
A6-bis reports "123 passed". The repository contains 293 test functions, of
which `tests/graph/` alone holds 166. So 123 is approximately
`unit + mcp + integration` with the graph suite not run. That is fine if
HydraDB was occupied, but as written the two documents read as a 168-test
regression. Add one line naming the suites executed, the suites skipped, and
why. If the graph suite did not run, say so explicitly and re-run it now if
the database is free.

**0.2 — Fix the mislabelled closure triple.** A6.6 prints
`36 / 56 / 0` labelled "(median / p90 / max; 0/200 over 8,000)". `0` cannot be
a maximum when p90 is 56 — it is the over-budget count, double-labelled. The
reported max is 114. Correct the row to four explicitly named figures:
median 36, p90 56, max 114, over-8,000 count 0/200. Confirm 114 against the
harness output rather than trusting this prompt.

**0.3 — Reconcile A6.3 against A6.6.** Both claim 200 trials, the same 1,891
seed pool, `rng_seed=20260817`, six seeds, 8,000-token budget, corrected
graph. They disagree on the central quantity:

- A6.3: `token_margin` median −308.5, implying ~7,691 tokens, ~96% utilisation
- A6.6: compiled total median 5,602 tokens, ~70% utilisation

A ~2,090-token gap. Print the exact formula each harness uses for its figure —
`validate_budget_django.py`'s `token_margin` and the closure harness's
"compiled total tokens (hints included)" — and state which is budgeted cost
and which is emitted cost, including whether each includes the framing term
(`6 × emitted + 13 × files + 100`) and the hint reserve. Then add one sentence
to A6.6 naming **which figure the README should quote for utilisation**, and
correct or annotate whichever of the two is mislabelled.

Do not adjust either number to make them agree. If they measure different
things, say what each measures.

**0.4 — Retire the stale thesis one-liner.** The pre-A6 claim "optional
packing doubles the emitted content" is dead: A6.6 gives a mandatory floor of
21 emitted symbols / 3,718.5 tokens against a compiled 34 symbols / 5,602
tokens, i.e. ~1.6× symbols and ~1.5× tokens, with an 18.7% median optional
share. Rewrite the one-line summary from the A6.6 table rather than patching
the old sentence, and grep the repo for any surviving instance of `2.00x`,
`50.4%`, `4.57x`, `95.0%`, `1,059 ms` or `29.8 L1` and correct each.

Commit Task 0 separately before starting Item 8.

---

## Task 1 — Item 8 scope: two mechanisms, not five

Spec §6.4 lists five: traceback parsing, BM25, embedding top-k, LLM proposal,
connectivity rerank. **Build two.** There is not time for five and the two
below carry nearly all the demonstrable value.

### 1.1 Traceback parsing

Highest precision, and free whenever the task contains a traceback. Parse
CPython traceback frames (`File "<path>", line <n>, in <name>`) out of a free-text
task string, map each frame to a symbol, and return the frames innermost-first.

- Path→symbol resolution goes through the sidecar's file/line data and the
  application-side FQN map, **never a graph query** (§2.1).
- A frame that lands in a file you did not index, or on a line inside no known
  symbol, is skipped with a recorded reason, not an error.
- Deduplicate repeated frames (recursion) while preserving first-seen order.
- If no frames parse, fall through to the ranking path rather than raising.

### 1.2 Connectivity rerank

This is the graph-native part and the thing judges should see. Given a
candidate set, prefer candidates mutually reachable within 2 hops over
isolated ones.

- Reuse the existing machinery in `graph/pack.py`: `HUB_SKIP_DEGREE = 500` and
  the `in_degrees` table. Do not introduce a second hub policy.
- **Bound the reads.** Per A3, no batched reverse read exists — reverse
  discovery is one request per node. Cap the rerank input at **20 candidates**,
  not `pack.py`'s 150. Two reasons: reverse reads are serial, and A5 showed
  the candidate pool contains genuine garbage that a small cap filters for
  free. State the cap and its justification in the results doc.
- Score should be simple and explainable in one sentence on camera. Mutual
  reachability count within 2 hops, tie-broken deterministically. Do not
  invent a weighted composite.

### 1.3 What not to build

BM25 and LLM proposal are **out of scope for this session**. Embedding top-k is
out of scope *unless* `~/out/django/embeddings/` already exists from the
baseline work, in which case wire it as a third candidate source behind a flag,
in under 30 lines, and only after 1.1 and 1.2 are green.

---

## Task 2 — Contract constraints (these protect the benchmark)

**2.1 — Keep the signature.** `resolve_task(task, sidecar, top_k) -> list[int]`
and `resolve_seeds(queries, by_fqn) -> list[int]` must keep returning
`list[int]`. `server.py::_resolve_ids` and the vector baseline's B2 arm both
call these directly. Extend with keyword-only optional parameters; do not
change the return type or the positional signature.

**2.2 — Seed parity is the point.** The evaluation compares closure expansion
against similarity expansion **on identical seeds**. Whatever this resolver
returns must be obtainable by the baseline arms through the same call. If
connectivity rerank needs a graph handle, pass it as an explicit optional
parameter so a caller can supply the same one — do not read a module-level
global or an implicit connection, because that makes the resolver
non-reproducible from outside the MCP server.

**2.3 — Class-level seeds now fire.** Per A3.5 and A6's closing note, this
resolver will start returning class and module symbols, which enter at L3 and
propagate `CALLS(L3)→L2`. A6 removed the fabricated container edges that made
this dangerous. **Add a test that seeds a class directly** (`Query` is the
natural choice — its outgoing `CALLS` is now 0) and assert the resulting
closure is bounded and closed. This is the regression guard for the whole
A5/A6 chain.

**2.4 — Determinism.** Same task string plus same sidecar must give the same
seed list, every run. Tie-break on `(score, fqn)` explicitly. The eval and the
baseline both depend on this.

---

## Task 3 — Deliverables

- `src/context_compiler/mcp/seeds.py` extended; `PLACEHOLDER(item-8)` docstring
  replaced with what was actually built and what was deliberately not.
- Unit tests: traceback parsing (including a frame in an unindexed file and a
  recursive traceback), connectivity rerank ordering, determinism, and the
  class-seed closure guard from 2.3.
- `docs/spikes/seeds-item-8-results.md`, following the established pattern:
  what was built, what was cut and why, the 20-candidate cap justification,
  worked example from a real Django traceback, and any discrepancy raised as a
  numbered amendment rather than designed around silently.
- `README.md`: replace the "Task-based resolution is a `PLACEHOLDER(item-8)`"
  paragraph. **Say plainly that similarity is used for entry and rejected for
  expansion** — §6.4 asks for this explicitly and the honesty is worth more
  than the appearance of purity.

## Constraints

- Never substitute Neo4j or mock HydraDB.
- No background polling loops, no scheduled wakeups. Long runs go to a log.
- Report numbers as measured; no tuning to preserve a prior figure.
- If 1.1 and 1.2 are not both green at the 3-hour mark, stop, commit what
  works, and report which one is incomplete. A working traceback path alone is
  enough to record the demo.