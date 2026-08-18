# Prompt: Fix the two graph-suite failures, and preserve the Arm B raw data

**Read first:** `tests/graph/test_ingest.py` (lines 30–60 and 150–180),
`docs/specs/amendment-a6.md` §A6.2, `docs/spikes/baseline-arm-b-results.md`
(Verification section).

**Time-box: 90 minutes.** Diagnose before fixing. Stop and report rather than
working around.

---

## Task 1 — Preserve the Arm B raw data (do this first, 2 minutes)

`docs/spikes/baseline-arm-b-results.md` says raw per-trial measurements live at
`/tmp/baseline-arm-b-200.json`. `.gitignore` ignores `/tmp/`, and the file will
not survive a reboot. Move it into the repository — suggested
`docs/spikes/data/baseline-arm-b-200.json` — force-add it if a gitignore rule
catches it, and update the path reference in the results document. Confirm the
file is tracked with `git ls-files`.

---

## Task 2 — Diagnose the two failures before changing anything

The mandated run produced `315 passed, 3 skipped, 2 failed`. The Arm B results
document attributes both to graph-state drift: HydraDB reporting 43,432
`Symbol` / 21,967 `Test` against a sidecar contract of 43,420 / 21,966.

**That attribution has not been verified, and at least one failure has a
different and simpler cause.** `tests/graph/test_ingest.py` hardcodes:

```
EXPECTED_SYMBOLS        = 43_420
EXPECTED_MANDATORY_EDGES = 116_758
EXPECTED_INHERITS_FROM   = 7_149
```

`EXPECTED_MANDATORY_EDGES = 116_758` is a **pre-A6** figure. A6.2 reduced
`CALLS` by 38,944, so the post-A6 mandatory total is **77,814**
(84,963 total − 7,149 `INHERITS_FROM`, which `PROPAGATION` excludes). This
assertion has been stale since A6 landed and went unnoticed because the graph
suite was not collected in the intervening sessions.

**Report the actual failures verbatim** — test names and assertion messages —
before proposing any fix. Run:

```
python -m pytest tests/graph/test_ingest.py -v
```

For each failure, state which of these it is:

- **(a) A stale post-A6 constant.** Then the fix is to correct the constant,
  citing A6.2 for the new value. Prefer deriving the expected value from
  `edges.jsonl` at test time over hardcoding a new literal, so the next
  re-extraction cannot silently invalidate it again — the same defect would
  otherwise recur at the next amendment.
- **(b) A genuine node-count mismatch** between the live graph and
  `~/out/django/symbols.jsonl`. Then continue to Task 3.

Do not change a constant until you have said which category the failure is in.
A stale expectation and a corrupted graph need opposite responses.

---

## Task 3 — Only if a real node-count mismatch exists

If, and only if, the live graph genuinely holds more `Symbol` or `Test` nodes
than `symbols.jsonl` contains:

1. **Identify the extra nodes.** Get their ids, `fqn`, `kind` and `file`. Twelve
   Symbols and one Test node, if the reported figures are right.
2. **Classify them.** Are they Django symbols absent from the current
   `symbols.jsonl`? Fixture nodes from a test run? Leftovers from the pre-A6
   graph that `run_hydradb.sh reset` did not clear? The `fqn` values should make
   this obvious.
3. **Determine whether any read path writes.** `mcp/seeds.py`'s connectivity
   rerank and `baseline/arm_b.py` both ran against this graph today and both are
   supposed to be read-only. Confirm neither issues a `CREATE` or `MERGE`.
   **This is the important question** — a read path that writes is a real bug
   and matters more than the count itself.
4. **Report, then propose.** If it is benign leftover state, a targeted cleanup
   or a re-ingest plus a numbered amendment. If a query path is writing, stop
   and report; do not paper over it with a cleanup.

If the mismatch turns out not to exist — i.e. both failures are category (a) —
say so plainly and correct the Arm B results document, which currently states
graph drift as fact. **A wrong diagnosis recorded in a results document is
worse than no diagnosis**, because later sessions will build on it.

---

## Task 4 — Re-verify

After the fix, run the full mandated command and report the counts and which
suites ran:

```
python -m pytest tests/unit tests/mcp tests/integration tests/graph -q
```

Expect 0 failed. If the Arm B 200-trial numbers were measured against a graph
that has since changed, say whether they need re-measuring or whether the
change cannot have affected them — and justify which.

---

## Deliverables

- Arm B raw JSON tracked in the repository, path reference updated
- Verbatim failure output, each classified (a) or (b) with reasoning
- Whichever fix follows from the classification, with any spec-contract change
  raised as a numbered amendment per project practice
- Arm B results document corrected if its drift attribution was wrong
- Full four-suite run at 0 failed, with the test count and suite list stated

## Constraints

- Never substitute Neo4j or mock HydraDB.
- Do not re-ingest or reset the graph as a first move — diagnose first. A reset
  destroys the evidence needed to tell (a) from (b).
- Do not modify `compile.py`, `closure.py`, or `pack.py`.
- No background polling loops. Long runs go to a log; read the log.
- Report numbers as measured. No tuning to preserve a prior figure.