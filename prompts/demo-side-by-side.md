# Prompt: The demo side-by-side — traceback seeds, compiler vs Arm B

**Read first:** `docs/spikes/baseline-arm-b-example.md`,
`docs/spikes/baseline-arm-b-results.md`, `src/context_compiler/mcp/seeds.py`
(traceback path), `scripts/worked_example.py`,
`docs/spikes/seeds-item-8-results.md`.

**Time-box: 90 minutes.** This artifact is the video's centerpiece and the
README's headline comparison. Nothing else is scheduled until it exists.

---

## Why the existing example does not work

`baseline-arm-b-example.md` reports compiler **32** emitted symbols against Arm
B's **33**, with four identical mandatory identities and heavily overlapping
dependency blocks. On screen those two panels look the same, and a viewer
concludes closure changes nothing.

The cause is the seed set, not Arm B. Trial 5's six seeds are randomly sampled
unrelated symbols — an admin changelist method, a `compilemessages` command, a
migration autodetector, a `CreateModel` method, a SQL compiler method, and a
template tag. There is no causal chain joining them, so a two-hop closure has
nothing to reach that a one-hop neighbourhood misses.

Random seeds are the right instrument for the 200-trial invariant validation and
that table stays exactly as it is. They are the wrong instrument for showing
what closure buys.

---

## Task 1 — Seed selection, rule stated before inspecting output

Use Item 8's traceback resolver on the canonical Django filter path:

```text
Traceback (most recent call last):
  File "django/db/models/query.py", line 1682, in filter
  File "django/db/models/query.py", line 1699, in _filter_or_exclude
  File "django/db/models/sql/query.py", line 1510, in build_filter
```

**Selection rule, fixed now:** this traceback is chosen because it is the
canonical `QuerySet.filter` → SQL-construction path in Django and because A5/A6
already characterised its neighbourhood in detail, so the graph structure around
it is independently documented. It is **not** chosen by comparing arm outputs.
State this rule verbatim in the artifact.

Resolve seeds through `resolve_task` so the demo exercises the real Item 8 path,
not hand-picked FQNs. Record the resolved ids and FQNs. **Both arms take this
identical seed list** — assert set equality at runtime and state that the
assertion ran.

If the traceback resolves fewer than three seeds, stop and report rather than
substituting FQNs; that would be an Item 8 defect worth knowing about.

---

## Task 2 — Run both arms and report what actually differs

Same seeds, same 8,000 budget, same `cost()`, same emitter. Report:

- emitted symbols and tokens, budgeted tokens, utilisation
- **level composition** (L3 / L2 / L1) for each arm
- **identities named but not emitted** — count and the actual FQN lists
- `is_closed()` for both
- round trips, latency
- **the set difference**: which symbols the compiler emits or names that Arm B
  does not, and vice versa. Name them. This is the substance of the comparison
  and a table of aggregate counts does not convey it.

Per A5/A6, hop 2 from `build_filter` reaches `WhereNode`, `ColPairs`,
`JoinPromoter`, `OuterRef`, `Exists`, `ResolvedOuterRef`. Report explicitly
whether each appears in the compiler's output and in Arm B's. If they appear in
both, say so — that is a finding, not a failure.

**Report the difference as measured.** If the arms again look similar on a
causal chain, that is a much more important result than a flattering example and
must be stated plainly at the top of the artifact. Do not select a different
traceback to obtain a better contrast; the rule was fixed in Task 1.

---

## Task 3 — Identify the differentiator honestly

In the weak example the one real separation was the identity tier: the compiler
named 25 symbols (4 mandatory + 21 hints) against Arm B's 4, for roughly 60
tokens. If that holds here, it is the story — closure tells the model about
symbols it cannot afford to emit, and one-hop ranking does not.

State which single measure best separates the two arms on this example, with its
numbers. One sentence, quotable in a video voiceover. If no measure separates
them meaningfully, say that instead.

Note for the record that the dangling-reference metric did **not** separate the
arms in the 200-trial run (median 4 vs 4, p90 9 vs 9, compiler worse in the tail
at max 56 vs 20). It was expected to be decisive and was not. Do not revive it
as a headline.

---

## Task 4 — The artifact

Write `docs/spikes/demo-side-by-side.md`:

1. The traceback, and the resolved seeds with ids and FQNs
2. The selection rule from Task 1, verbatim
3. The comparison table, then the named set differences
4. The Task 3 differentiator sentence
5. Both full emitted contexts, compiler first

Keep `baseline-arm-b-example.md` as-is and cross-reference it: the random-seed
case is the corpus-representative one and the traceback case is the
causal-structure one. Both being present is stronger than either alone, and the
contrast between them is itself the argument for why seed selection matters.

Add a one-line pointer in `baseline-arm-b-results.md` to the new artifact.

---

## Task 5 — Small fixes, same session

- **`frozen_params.json` is still `{}`.** Spec §9.4 requires it committed with
  the SHA cited in the README before the evaluation run. Populate:
  `rng_seed=20260817`, budget `8000`, `HINT_RESERVE`, `POOL_CAP`,
  `HUB_SKIP_DEGREE=500`, the Item 8 20-candidate rerank cap, and
  `model_id="jinaai/jina-embeddings-v2-base-code"`,
  `model_revision="516f4baf13dec4ddddda8631e019b5737c8bc250"` for the deferred
  Arm A. Cite the commit SHA in the README when the results section is written.
- **Amend A7** with the fixture-leak finding: `tests/integration/test_hydradb_compat.py`
  wrote twelve nodes (including one `Test`) that persisted through A6-bis, Item
  8 and Arm B before the count assertions caught them. Three lines. The next
  session that sees node-count drift should not have to re-run that
  investigation.
- **Move the "Arm B needs no re-measurement" reasoning** from the session report
  into `baseline-arm-b-results.md`, including the sidecar-membership filtering
  argument.
- **Grep for surviving stale figures**: `2.00x`, `50.4%`, `4.57x`, `95.0%`,
  `1,059 ms`, `29.8 L1`, `116,758`, `24 symbols`, `6,932`. Correct each.

---

## Deliverables

- `docs/spikes/demo-side-by-side.md`
- `frozen_params.json` populated; A7 amended; Arm B results doc updated
- Full four-suite run reported with counts and suite names

## Constraints

- Never substitute Neo4j or mock HydraDB.
- Do not modify `compile.py`, `closure.py`, `pack.py`, or `arm_b.py` — both arms
  must be the shipped implementations.
- Do not change the seed selection rule after seeing output.
- Report numbers as measured. No tuning to preserve a prior figure.
- If the traceback path fails to resolve, stop and report.