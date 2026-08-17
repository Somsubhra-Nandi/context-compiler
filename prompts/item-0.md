# Task: Item 0 — HydraDB Compatibility Spike

You are the primary implementation engineer for **Context Compiler**, a Hack Hydra Track 2B project. The repository is nearly empty.

---

## 0. Source of truth

Before doing anything else, read completely:

```
docs/specs/context-compiler-v1.3.md
```

That is the **frozen implementation specification**. Read especially:

- §0 — invariants I1–I6
- §2 — graph schema
- §2.5 — HydraDB indexing / property-selector behaviour
- §5.1 — HydraDB frontier expansion
- §10 — build order
- **Appendix A — HydraDB Cypher constraints** (the compatibility contract this whole task exists to verify)

**Do not redesign the architecture. Do not substitute Neo4j or any other graph database. Do not write Cypher outside Appendix A.**

The spec has been through four rounds of design review. Change it only if running software proves a stated assumption false — and if that happens, document the exact observed failure rather than silently designing around it.

---

## 1. Environment — verified facts, do not re-verify

### Platform
Ubuntu **26.04 LTS (resolute)** under WSL2 on Windows 11 — **not 24.04**. Package versions differ from HydraDB's README. Host: Intel i5-1340P (12 cores), WSL capped at 10 GB RAM / 8 processors.

### Already installed and confirmed working — do not redo
| Component | Version |
|---|---|
| `libcypher-parser` | 0.6.2 (`pkg-config --modversion cypher-parser` → `0.6.2`) |
| SuiteSparse GraphBLAS | 7.4.0, headers at `/usr/include/GraphBLAS.h` |
| Rust | 1.97.1 (spec floor is 1.91) |
| `just` | 1.58.0 |
| `just native-check` in `~/hydradb` | **PASSES** |

### Filesystem — do not violate
- Project root: `~/context-compiler`
- HydraDB is **already cloned** at `~/hydradb` — do not clone it again, and never into this repository. Add it to `.gitignore` if it ever appears here.
- Work **only** inside the Linux filesystem (`~/`). **Never** create, build, or run anything under `/mnt/c/`. Windows-mounted paths are 5–10× slower for this workload and break file watching. If you find yourself in `/mnt/c/`, stop and report it.

### You cannot use sudo
System prerequisites are installed. If a build fails on a missing system library, **stop and report exactly which one**. Do not attempt to install it.

### PEP 668 is enforced
Plain `pip install` will be **refused** by this Ubuntu. Always use a virtualenv:
```bash
python3 -m venv .venv && source .venv/bin/activate
```

### Known non-errors — ignore these
- systemd triggers printing `Failed to connect to system scope bus` during apt operations. Normal in WSL. **Not an error.**
- `just native-check` producing no output. Silent exit = success.

---

## 2. Critical operational hazards

### graph-node runs in the FOREGROUND and never returns
**This is the single most likely way you will hang.** From HydraDB's own README: *"The node runs in the foreground and does not return; that is it working, not hanging."*

Always background it with a log redirect:
```bash
cd ~/hydradb && <start command> > /tmp/hydradb-node.log 2>&1 &
```
Then poll readiness before proceeding:
```bash
for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:9090/readyz && break
  sleep 2
done
```
**Never** run `graph-node` as a blocking foreground command.

### RUST_MIN_STACK
Export `RUST_MIN_STACK=33554432` in **every** shell that starts graph-node. Without it the node answers `/readyz` and then aborts with a stack overflow on the **first query**. That failure looks like an application bug and is not one.

### CLOUD_PROVIDER
Must be `local`, with `LOCAL_PATH` pointing at a directory that **already exists**. The error `invalid environment variable CLOUD_PROVIDER value \`null\`` means the variable is **unset**, not that it contains the string "null".

### Build time and memory
Cold `cargo build` with GraphBLAS bindings takes **20–45 minutes** on this hardware. Background it to a log and poll; do not block on one command that may time out. WSL is capped at 10 GB — do not run parallel cargo builds. If a build is OOM-killed, retry with `cargo build -j 4` and report it.

---

## 3. Time box and escalation

**Phase 2 (getting HydraDB running) is time-boxed to 2 hours wall clock.**

If HydraDB is not serving a round-tripped write by then: **STOP**. Do not continue to Phase 3. Write `docs/spikes/hydradb-item-0-results.md` with every command run, exact error output, and your best diagnosis. Report back.

**Do NOT attempt a workaround, a mock, an in-memory substitute, or a different graph database.** Substituting Neo4j or mocking HydraDB is a disqualification risk for this hackathon, not a shortcut. If you find yourself considering one, that is the signal to stop and escalate.

**Report at each phase boundary, not only at the end.** If a phase fails, stop at that phase and tell me.

---

## 4. Phase 1 — Repository initialization

Git is already initialized on branch `main`. Create:

```
src/context_compiler/
tests/
tests/integration/
scripts/
docs/spikes/
```

Preserve `docs/specs/context-compiler-v1.3.md`.

Create `.gitignore` (Python, Rust artifacts, `.venv/`, `hydradb/`, logs), `LICENSE` (MIT), and `frozen_params.json` containing exactly `{}`. Do not populate benchmark parameters yet.

Create only the minimal Python project configuration needed to run the spike and its tests. No application features.

**Make the first commit before writing any spike code.** A missing open-source license is an automatic disqualification for this hackathon.

---

## 5. Phase 2 — Get HydraDB running

Use the actual OSS HydraDB at `~/hydradb`. Follow its documented setup; do not guess.

- Establish a **reproducible** local startup procedure.
- Run `just smoke` if the checked-out version supports it.
- **Do not merely prove a health endpoint responds.** Prove the database executes real graph mutations and queries.
- Record every exact command used.

---

## 6. Phase 3 — Bolt connectivity

Connect from Python using the Neo4j driver over HydraDB's Bolt interface. Create a minimal spike script. Verify:

1. a Bolt connection can be established
2. a trivial valid query executes
3. errors from invalid Cypher surface cleanly

Do not build a general abstraction layer. This is a spike.

---

## 7. Phase 4 — Verify the exact Cypher forms the spec depends on

Test these forms; do not assume they work.

### A. Batched node write
```cypher
UNWIND $rows AS row
MERGE (n {id: row.v})
SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind,
    n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3
```
Insert multiple rows in one batch. Read them back and verify properties.

### B. Batched relationship write
Create ≥2 `:Symbol` nodes and write a directed typed `CALLS` relationship using the Appendix A pattern. Relationship identity must use an integer `id`. Verify that repeating the same write is idempotent for a static edge.

### C. Batched frontier read (§5.1)
```cypher
UNWIND $rows AS row
MATCH (x:Symbol {id: row.v})-[:CALLS]->(y:Symbol)
RETURN x.id AS src, y.id AS dst,
       y.repr_L2_tokens AS t2, y.repr_L3_tokens AS t3
```
Use multiple source ids in `rows`. Prove the expected edges return.

### D. Multi-label behaviour — LOAD-BEARING FOR v1.3
Create a node carrying both `:Symbol` and `:Test` using the form HydraDB supports. Verify **both**:
- `(x:Symbol)` matches the node
- `(x:Test)` matches the same node

**If multi-label behaviour differs from the spec, STOP and document the actual behaviour before attempting any architectural workaround.** Spec §2.1 depends on this for invariant I6; a difference here requires a spec amendment, not a code workaround.

### E. Property-selector and path-procedure verification (§2.5)
Investigate and test, for the installed version:
- property selector behaviour
- `sourceLabel`, `sourceProperty`
- availability of `algo.SPpaths`, `algo.SSpaths`, `algo.MSpaths`

**Verification only** — build no application logic on these yet. Record exact observed signatures and behaviour.

---

## 8. Phase 5 — Automated compatibility tests

Create integration tests covering the subset Context Compiler relies on:
- `UNWIND $rows AS row`
- `MERGE` node by integer `id`
- `SET` labels and properties
- directed single-type relationship creation
- `UNWIND MATCH ... RETURN`
- multiple labels
- bounded path syntax, if available

Then add **rejection tests** confirming HydraDB refuses these Neo4j idioms from Appendix A:
- `IN` in `WHERE`
- multiple relationship types in one pattern
- `type(r)` projection
- `RETURN *`
- unbounded variable-length path
- Neo4j-style `CREATE INDEX`

These rejection tests exist so future agents cannot silently reintroduce unsupported syntax. Treat them as permanent guardrails.

---

## 9. Deliverables

```
scripts/run_hydradb.sh
scripts/hydradb_spike.py
tests/integration/test_hydradb_compat.py
docs/spikes/hydradb-item-0-results.md
```
plus minimal project configuration.

**`scripts/run_hydradb.sh`** — one idempotent script that exports all required environment variables, starts HydraDB backgrounded, waits for readiness, and exits non-zero if the node never becomes ready. This will be run a hundred times over the next four days. It must be reliable.

**`docs/spikes/hydradb-item-0-results.md`** must contain:

```
HydraDB version / commit tested:
How HydraDB was started:
Required environment variables:
Bolt endpoint:
Python driver / version:

just smoke:                  PASS / FAIL

UNWIND node write:           PASS / FAIL
UNWIND relationship write:   PASS / FAIL
UNWIND frontier read:        PASS / FAIL
Symbol+Test multilabel:      PASS / FAIL

SPpaths:                     AVAILABLE / UNAVAILABLE
SSpaths:                     AVAILABLE / UNAVAILABLE
MSpaths:                     AVAILABLE / UNAVAILABLE

Property selector behaviour:
<actual observed behaviour>

Rejection tests (each should be REJECTED):
IN in WHERE:                 REJECTED / ACCEPTED
Multi-type relationship:     REJECTED / ACCEPTED
type(r) projection:          REJECTED / ACCEPTED
RETURN *:                    REJECTED / ACCEPTED
Unbounded var-length path:   REJECTED / ACCEPTED
CREATE INDEX:                REJECTED / ACCEPTED

Differences from spec v1.3:
<none, or explicit list>
```

---

## 10. Acceptance gate

**Item 0 is not complete merely because code was written.** Before finishing you must have:

1. started HydraDB
2. run the actual spike
3. run the integration tests
4. proven at least one real graph write **and** read through Bolt
5. proven the `:Symbol:Test` multi-label behaviour
6. recorded every command executed
7. recorded every file created or modified
8. recorded tests passed and failed
9. recorded any discrepancy between HydraDB's behaviour and spec v1.3

If HydraDB behaves differently from the frozen spec, **do not silently redesign the application.** Document the exact observed behaviour and stop at the smallest necessary boundary so we can decide whether the spec needs an amendment.

**Do not proceed to Item 1.**

---

## 11. Final report

When finished, give me:

- implementation summary
- commands to reproduce
- actual test output summary
- files changed
- HydraDB compatibility matrix
- unresolved issues
- recommended commit message
