# Item 0 — HydraDB Compatibility Spike: Results

HydraDB version / commit tested: `0.1.0`, commit `6a2fbb192f37f51a93690a2ae2d2f5e27e6e4219` (2026-08-13), checked out at `~/hydradb`

How HydraDB was started: `scripts/run_hydradb.sh start` — `cargo run --locked --features server-runtime --bin graph-node`, backgrounded with `nohup ... &`, polled against `/readyz` every 2s up to 60 times

Required environment variables:
```
CLOUD_PROVIDER=local
LOCAL_PATH=<data-root>/store           # must already exist
GRAPH_NAMESPACE=default
GRAPH_ID=default
GRAPH_CELL_ID=cell-0
GRAPH_CELLS=cell-0
GRAPH_NODE_ID=node-0
GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687
GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687
GRAPH_DATA_CACHE_DIR=<data-root>/cache
GRAPH_AUTH_TOKEN_FILE=<data-root>/auth-token   # file containing a >=32-byte token
GRAPH_ALLOW_PLAINTEXT=true
RUST_MIN_STACK=33554432                # without this: builds, serves /readyz, aborts on first query
```

Bolt endpoint: `bolt://127.0.0.1:7687` (HTTP: `127.0.0.1:8443`, admin/readyz/metrics: `127.0.0.1:9090`)

Python driver / version: `neo4j` 6.2.0 (in `.venv`), Python 3.14.4

```
just smoke:                  PASS   ("graph object-store smoke passed at epoch 10", 2m18s cold build)

UNWIND node write:           PASS
UNWIND relationship write:   PASS
UNWIND frontier read:        FAIL   (spec §5.1's verbatim query form is rejected at parse time — see §1 below)
Symbol+Test multilabel:      PASS   (via two sequential single-label UNWIND SET batches — see §2 below)

SPpaths:                     AVAILABLE
SSpaths:                     AVAILABLE
MSpaths:                     AVAILABLE

Property selector behaviour:
See §3 below. Summary: sourceLabel/sourceProperty/sourceValues/targetValues apply ONLY to
algo.MSpaths; algo.SPpaths and algo.SSpaths take a direct integer sourceNode/targetNode
(VertexId), not a label/property selector. algo.MSpaths' sourceValues/targetValues must be
lists of STRINGS — a selector property must be string-typed (fqn works; a bare INT id
property does not: "sourceValues must be a list of strings").

Rejection tests (each should be REJECTED):
IN in WHERE:                 REJECTED
Multi-type relationship:     REJECTED
type(r) projection:          REJECTED
RETURN *:                    REJECTED
Unbounded var-length path:   REJECTED
CREATE INDEX:                REJECTED

Differences from spec v1.3:
See "Differences from spec v1.3" section below — three items, all in §5.1 / the UNWIND-batch
write grammar. None require an architectural redesign; §5.1's batched-frontier-read
performance claim (12 requests/hop) needs revisiting once these are decided.
```

---

## Reproduction

```bash
# 1. One-time environment setup (already done, recorded for completeness)
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet neo4j pytest

# 2. Build + smoke-test HydraDB
cd ~/hydradb
export RUST_MIN_STACK=33554432
just native-check      # PASS, silent
just smoke             # PASS: "graph object-store smoke passed at epoch 10"

# 3. Start the node (idempotent, backgrounded, polls for readiness)
cd ~/context-compiler
bash scripts/run_hydradb.sh start
bash scripts/run_hydradb.sh status   # "hydradb: ready (pid ...)"

# 4. Run the spike script
source .venv/bin/activate
python scripts/hydradb_spike.py

# 5. Run the automated compatibility test suite
python -m pytest tests/integration/test_hydradb_compat.py -v

# 6. Stop the node when done
bash scripts/run_hydradb.sh stop
```

## Test output summary

`scripts/hydradb_spike.py`: 16/17 checks pass. The one documented failure
(`unwind_frontier_read_verbatim`) is expected and does not fail the script
(explicit `expected_failures` allowlist) — see below.

`pytest tests/integration/test_hydradb_compat.py -v`: **16/16 passed** (0.4s).
This suite tests the *actual accepted grammar*, including a test that pins the
verbatim-spec-form rejection as an explicit `pytest.raises` (a future HydraDB
upgrade that starts accepting the literal spec form would surface as a test
failure here, which is the point — it flags the spec assumption for review).

## Commands run (chronological, condensed)

```
ls docs/specs/, ls ~/hydradb, cat /etc/os-release, pkg-config --modversion cypher-parser,
rustc --version, just --version, free -h, nproc      # environment verification
git status; git log                                   # confirm empty repo
mkdir -p src/context_compiler tests/integration scripts docs/spikes
git add ...; git commit                                # Phase 1 root commit
cd ~/hydradb && head -150/400 README.md; cat justfile; cat scripts/runtime_smoke.sh
python3 -m venv .venv && pip install neo4j pytest
cd ~/hydradb && RUST_MIN_STACK=33554432 nohup just smoke > /tmp/hydradb-smoke.log 2>&1 &
bash scripts/run_hydradb.sh start                       # builds + starts graph-node
curl http://127.0.0.1:9090/readyz ; curl http://127.0.0.1:9090/metrics
curl http://127.0.0.1:8443/v1/graphs/default/query (write + read, per README)
python scripts/hydradb_spike.py                         # first run surfaced 3 real findings
grep -rn "one-hop relationships only|do not support labels|..." src/query/opencypher.rs
Read src/query/opencypher.rs (lower_unwind_batch, unwind_edge_template, etc.)
grep -rn "sourceLabel|sourceProperty|sourceValues" src/query/path_procedure.rs
Read src/query/path_procedure.rs (parse_native_path_procedure, required_selector)
<~20 isolated one-off Python/bolt probes to pin exact accepted grammar>
git rev-parse HEAD (commit + date pinned above)
bash scripts/run_hydradb.sh stop/start (fixed a stop/start race — see below)
python -m pytest tests/integration/test_hydradb_compat.py -v      # 16/16 pass
```

## Files created or modified

```
.gitignore                                 (new, Phase 1)
LICENSE                                    (new, MIT, Phase 1)
frozen_params.json                         (new, "{}", Phase 1)
pyproject.toml                             (new, Phase 1)
src/context_compiler/__init__.py           (new, empty package marker)
tests/__init__.py                          (new)
tests/integration/__init__.py              (new)
scripts/run_hydradb.sh                     (new, Phase 2 — idempotent start/stop/status)
scripts/hydradb_spike.py                   (new, Phase 3/4 spike script)
tests/integration/test_hydradb_compat.py   (new, Phase 5 — 16 tests, permanent guardrails)
docs/spikes/hydradb-item-0-results.md      (this file)
```

No files under `docs/specs/` were modified. `~/hydradb` was not modified (no
commits, no local changes) other than build artifacts (`target/`, git-ignored
upstream) and runtime data under `~/.local/state/hydradb-dev/` (outside the
repo, git-ignored).

---

## Differences from spec v1.3

Three real discrepancies were found, all inside the **UNWIND-batch fast path**
that HydraDB's OpenCypher engine implements as a syntactic-shape classifier
(`lower_unwind_batch` in `src/query/opencypher.rs`) rather than a general
query planner. None require redesigning Context Compiler's architecture; all
are narrow, mechanical adjustments to §5.1's exact query text and cost model.
Recorded here per the acceptance gate rather than silently worked around.

### 1. §5.1's batched frontier-read query is rejected verbatim (LOAD-BEARING)

Spec §5.1's canonical form:

```cypher
UNWIND $rows AS row
  MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol)
  RETURN x.id AS src, y.id AS dst, y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3
```

is rejected at parse time:

```
OpenCypher query is not supported yet: UNWIND batch node patterns do not support labels
```

Root cause (`unwind_edge_template` / `unwind_node_id_field`,
`src/query/opencypher.rs:2921-3021`): the `UNWIND ... MATCH ... RETURN` batch
classifier categorically forbids **any label** on either endpoint node, and
(once labels are removed) further requires **exactly two projections** —
`row.<sourceField> AS ..., <destAlias>.id AS ...` — with no additional
property projections permitted (`src/query/opencypher.rs:1176-1208`).

The only query shape this build's UNWIND-batch reader accepts is:

```cypher
UNWIND $rows AS row
  MATCH (x {id: row.v})-[:CALLS]->(y)
  RETURN row.v AS src, y.id AS dst
```

This returns edges only — no destination-node properties (token counts,
`repr_L2_refs`, etc.) can be fetched in the same batched round trip.

**Confirmed NOT a general engine limitation.** The exact spec form — labels
and all four projections — works perfectly as a **plain, non-UNWIND,
single-source query**:

```cypher
MATCH (x:Symbol {id: $v})-[:CALLS]->(y:Symbol)
RETURN x.id AS src, y.id AS dst, y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3
```

(Verified working during the spike.) So the restriction is specific to the
UNWIND-multi-row optimization; the general Cypher engine supports what the
spec needs. `UNION`/`UNION ALL` (documented as available for reads, same
columns per arm) is a candidate way to batch multiple single-source queries
into one round trip, but this was **not implemented or tested** — verification
only, per Phase 4's scope; building it would be redesigning §5.1's algorithm,
which is out of scope for Item 0.

**Impact on the spec's cost model:** §5.1 claims "six typed query templates
per productive hop... 12 batched requests for an unchunked two-hop closure,"
which depends on returning `y`'s token-count properties in the same call that
fetches the frontier edges. That specific mechanism does not work as written
against this HydraDB build. A follow-up decision is needed on one of:
(a) a second batched-by-id property fetch per hop (note: spec §2.1.1 already
observes HydraDB has no `IN` and no multi-value property lookup outside
`algo.MSpaths`, so this isn't a trivial addition),
(b) `UNION`-based batching of single-source reads (untested),
(c) accepting N single-source round trips per hop instead of 1 batched one.
This is a decision for the spec owner, not something to resolve here.

**Also found, same code path — writes have the opposite constraint.** The
`UNWIND ... MATCH ... MERGE` bound-edge-write form (used for creating/merging
relationships between two matched nodes) requires **exactly one label** per
endpoint — zero labels there is rejected
(`"UNWIND MATCH CREATE endpoints require exactly one label"` /
equivalent for MERGE). So: batched **writes** need exactly one label per
endpoint; batched **reads** need exactly zero. Both are satisfiable
individually (Appendix A's canonical write form already carries one label per
endpoint) — it is only the read form's extra property projections that break.

### 2. `SET n:Symbol, n:Test` in one UNWIND batch is rejected; two batches work (LOAD-BEARING for I6)

Spec §2.1 says `HydraDB supports multiple labels via SET n:Symbol, n:Test`.
Literally, in one UNWIND vertex-upsert batch, this is rejected:

```
OpenCypher query is not supported yet: UNWIND vertex upsert requires exactly one SET label
```

(`src/query/opencypher.rs:1488`, `:1549`). The UNWIND vertex-upsert batch
path allows exactly one label per `SET` clause.

**Confirmed workaround exists and was verified, not merely assumed:** issuing
two sequential single-label UNWIND batches against the same `id` —

```cypher
UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind
UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Test
```

— produces a node matched correctly by **both** `(x:Symbol)` and `(x:Test)`.
This was tested directly (`tests/integration/test_hydradb_compat.py::test_multilabel_symbol_and_test_both_match`)
and passes. I6's dual-labelling requirement (§2.1, "Tests are `:Symbol`") is
therefore achievable — ingest just needs to emit the label-`SET` as two
batched passes per node type combination instead of one, a small, mechanical
ingest-pipeline detail rather than a spec-level problem.

Also worth noting: a plain (non-UNWIND, non-batched) `MERGE (n {id: ...}) SET ...`
single-statement query is rejected outright — `"MERGE with following clauses
is not executable in Query engine"`. All vertex upserts in this build **must**
go through the UNWIND-batch path (even for a single row), confirming Appendix
A's framing of `UNWIND` as "the workhorse," not an optional optimization.

### 3. `algo.MSpaths`' selector values must be strings; `SPpaths`/`SSpaths` take direct integer node ids, not selectors

The README's `algo.MSpaths` example uses `sourceLabel`/`sourceProperty`/
`sourceValues`/`targetValues`. Verified in `src/query/path_procedure.rs`:

- `algo.SPpaths` and `algo.SSpaths` take a direct `sourceNode`/`targetNode`
  integer `VertexId` parameter — **not** a label/property selector at all.
  Using `sourceLabel`/`sourceProperty` with `SPpaths`/`SSpaths` is rejected
  (those options are `MSpaths`-only:
  `"{option} is only supported by algo.MSpaths"`).
- `algo.MSpaths`'s `sourceValues`/`targetValues` must be **lists of strings**
  (`config_string_list`) — resolved via `sourceProperty` naming a
  **string-typed** property. Using the INT `id` property directly as the
  `MSpaths` selector property fails: `"sourceValues must be a list of
  strings"`.

This is consistent with spec §2.1's design (node identity is `id: INT`,
owned application-side, never looked up by the graph) for `SPpaths`/`SSpaths`,
since those take the integer id directly. But §6.3's plan to use `MSpaths`
"given a seed set and a candidate set" of node ids will need a string-typed
selector property (e.g. `fqn`, which is already on every `:Symbol` node) —
not the INT `id` — for the `sourceProperty`/`targetProperty` selectors. This
is a minor, already-satisfiable detail (spec's `fqn` field is a string) but
is worth stating explicitly since the spec text doesn't currently say which
property `MSpaths` should select on.

### Non-spec observations (informational, not blocking)

- `RETURN` in the general (non-UNWIND-batch) query engine currently supports
  only `<binding>.<property>` or `count(*)` — a bare `RETURN 1 AS one` and
  `MATCH (n) RETURN count(n)` (no property filter on `n`) are both rejected.
  Not relevant to any form in Appendix A, encountered only while writing a
  "trivial query" smoke check for Phase 3.
- Relationship *properties* project fine in the general (non-UNWIND-batch)
  query engine — `MATCH (x:Symbol {id:...})-[r:CALLS]->(y:Symbol) RETURN
  r.resolver AS resolver, r.confidence AS conf` works and returns real values.
  Specifically `r.id`, however, is rejected as `"unbound variable r"` in
  every form tried (with or without endpoint labels, with or without a
  literal id filter on `r` itself). This narrow gap is worth a note before
  Item 9 (runtime tracing → evidence edges), since I5's evidence-state logic
  reads edge properties (`commit_sha`, body hashes, `confidence`) — those are
  fine — but code that needs to read back a relationship's own `id` (e.g. to
  confirm the `eid(...)` hash landed correctly, §2.1.3) will need to do so
  some other way (e.g. by re-deriving the expected id application-side rather
  than reading it back from the graph, which the design already does anyway
  since `eid()` is a pure function of `(type, src, dst[, revision])`).
- An operational hazard was found and fixed in `scripts/run_hydradb.sh`, not
  in HydraDB itself: the original `stop` command sent `SIGTERM` and returned
  immediately without waiting for the process to exit. Immediately wiping the
  data directory and starting a new node raced the old process's still-active
  writer lease and async compactor, corrupting the new instance's view of
  compacted SST files (`Object at location .../compacted/....sst not found`).
  Fixed by polling `kill -0` until the old process actually exits before
  `stop` returns. Verified reliable across 3 repeated stop/start/status
  cycles plus an idempotent start-while-running check.

---

## Unresolved issues

1. §5.1's frontier-read batching mechanism (as literally specified) does not
   work; a decision is needed among the three options listed in finding 1
   before Item 4 (fixpoint + `expand()`) is implemented.
2. `r.id` cannot be read back via any Bolt query form tried in this build
   (`"unbound variable r"`), although other relationship properties (e.g.
   `r.resolver`, `r.confidence`) read back fine. Not expected to block
   anything since §2.1.3's `eid()` is a pure function computed
   application-side rather than read from the graph, but worth confirming
   before Item 9 (runtime tracing → evidence edges) is built.
3. `UNION`/`UNION ALL`-based batching (as an alternative to per-hop fan-out
   for frontier reads) is documented as available per Appendix A but was not
   tested — flagged as a concrete next investigation, not attempted here to
   stay inside Item 0's verification-only mandate.
