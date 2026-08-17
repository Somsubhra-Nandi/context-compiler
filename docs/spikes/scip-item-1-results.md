# Item 1 SCIP Spike Results

Date: 2026-08-16 UTC; verification completed 2026-08-17 UTC

## Result

`scip-python` successfully indexed the `requests` checkout. SCIP remains usable as
the semantic half of the hybrid extractor. One version discrepancy was observed:
the host interpreter is Python 3.14, while scip-python 0.6.6 prints `Python version
3.14 from interpreter is unsupported` and uses its Python 3.11 standard-library
model. It nevertheless completed without an indexing error.

## Reproduction and measurements

```text
scip-python version: 0.6.6
SCIP CLI version: v0.7.1
Index command: /usr/bin/time -v scip-python index .
Index read method: scip print --json index.scip
Wall clock to index requests: 6.77 seconds
Peak resident memory: 337,676 KB
index.scip size: 991,249 bytes
Documents: 19
Symbols emitted (definition occurrences): 1,485
All occurrences: 8,226
```

The CLI was downloaded from the official `scip-code/scip` v0.7.1 Linux amd64
release. Its `print --json` output is stable machine-readable JSON and avoids
maintaining a second generated protobuf implementation in this project.

## SCIP symbol to FQN mapping

A SCIP global symbol has five space-separated package-manager/package/version
header fields followed by descriptor syntax. For example:

```text
scip-python python requests <revision> `src.requests.sessions`/Session#get().
```

Mapping rules for repository-owned definitions:

1. Reject `local N` symbols. They identify index-local temporaries and cannot form
   stable cross-run FQNs.
2. Parse the descriptor portion after the scheme, manager, package and version.
3. Remove backticks, then remove the configured source-root prefix (`src.` for the
   requests layout). A module descriptor ending in `/__init__:` denotes the
   package module itself; other module descriptors use their dotted path.
4. `/name().` denotes a module function, so `module/name().` becomes
   `module.name`.
5. `/Class#` denotes a class. Each following `member#` is a nested class/member
   scope; `method().` denotes a callable member. Thus
   ``module/Outer#Inner#method().`` becomes `module.Outer.Inner.method`.
6. `__init__` is retained literally (`module.Class.__init__`). It is not collapsed
   into the class FQN.
7. Terminating `.` fields/properties become their identifier name. Parameter
   descriptors in parentheses, such as `(self)`, are not graph symbols and are
   excluded.
8. Module-level code has a module symbol but no invented callable FQN. Imports and
   executable statements at module scope are attributed to that module node.
9. Overload disambiguators, when present, are removed only after SCIP descriptor
   parsing; overload definitions coalesce to the Python runtime FQN. Duplicate
   occurrences are counted and logged.

Observed examples:

```text
`src.requests.sessions`/Session#get().     -> requests.sessions.Session.get
`src.requests.sessions`/Session#__init__(). -> requests.sessions.Session.__init__
`src.requests.api`/request().              -> requests.api.request
`src.requests.sessions`/Session#           -> requests.sessions.Session
```

## Resolution behavior and failures

The requests index contains 8,226 occurrences: 1,485 definition occurrences and
6,741 reference occurrences. Local symbols are intentionally not resolvable to
project FQNs. External package and stdlib symbols resolve semantically in SCIP but
are not emitted as project nodes, so edges to them are omitted to preserve JSONL
referential integrity.

`scip-python` emits `is_implementation` relationships (64 relationship records in
this index), including both class inheritance/implementation and method override
relationships. It does not emit a general-purpose call relationship, confirming
the hybrid AST-classification plus SCIP-resolution architecture in spec §2.2.

Resolution rates are measured by the pipeline per AST occurrence kind. A semantic
resolution succeeds only when the occurrence's SCIP range maps to a non-local
symbol which maps to an emitted repository node. The final requests/flask/scale
rates are appended after end-to-end extraction.

Known failures encountered:

- Python 3.14 is unsupported by scip-python 0.6.6; it assumes the Python 3.11
  platform/library model.
- `scip stats index.scip` attempted to stat an empty project-root-derived path and
  reported `Couldn't count lines of code: stat : no such file or directory` while
  still producing valid occurrence/definition statistics. This does not affect
  `print --json` or extraction.
- The prompt described JSON conversion as available from the SCIP CLI; this is
  confirmed as `scip print --json`, not a separate `convert` command in v0.7.1.

## End-to-end extraction results

| Repository | Result | Symbols | Edges | Wall time | Peak RSS |
|---|---:|---:|---:|---:|---:|
| requests | pass | 760 | 802 | 6.77s SCIP; 1.55s reconstruction | 337,676 KB (SCIP) |
| flask | pass | 992 | 935 | 11.79s combined | 441,948 KB |

Requests edge counts: 601 `CALLS`, 106 `REFERENCES_TYPE`, 34
`READS_CONSTANT`, 29 `IMPLEMENTS`, 3 `OVERRIDES`, and 29 non-mandatory
`INHERITS_FROM`.

Flask edge counts: 685 `CALLS`, 125 `REFERENCES_TYPE`, 49 `DECORATED_BY`, 9
`READS_CONSTANT`, 16 `IMPLEMENTS`, 33 `OVERRIDES`, and 18 non-mandatory
`INHERITS_FROM`.

Resolution rate uses the deliberately strict denominator described above: all AST
occurrences, including external/stdlib calls, with success requiring an emitted
repository endpoint. Requests resolved 905/8,046 classified occurrences (11.25%):
calls 789/7,292 (10.82%), types 116/636 (18.24%), decorators 0/118. Flask resolved
1,085/10,003 (10.85%): calls 900/8,766 (10.27%), types 136/1,029 (13.22%), and
decorators 49/208 (23.56%). SCIP itself resolves many additional occurrences to
external symbols; those cannot become edges because the JSONL contract requires
every endpoint to be an emitted repository symbol.

Both successful repositories passed endpoint and representation-reference
integrity checks, unique-ID checks, and byte-for-byte determinism across two runs.

## Large-repository capability survey

The scale survey was limited to SCIP indexing and timing, as required. It did not
change the extractor or begin later work. SymPy's default V8 heap failed; an 8 GB
retry was stopped at the VM safety boundary; a 6 GB retry completed. Django was
then surveyed with the viable 6 GB setting. The generated indexes are target-repo
artifacts and are not project deliverables.

| Repository / attempt | Result | Files/documents | Wall time | Peak process RSS | Index size | Evidence |
|---|---|---:|---:|---:|---:|---|
| sympy, default heap | fail | 973 / 1,557 | 2:45.94 | 2,287,232 KB | 70 bytes (incomplete) | V8 heap exhaustion, signal 6 |
| sympy, 8 GB heap | safety abort | 886 / 1,559 emitted | not retained | 7,203,016 KB at abort | incomplete | system use reached 8.3/9.7 GiB, 1.4 GiB available, swap began |
| sympy, 6 GB heap | pass | 1,557 indexed | 14:25.54 | 6,331,428 KB | 150,482,490 bytes | complete `index.scip`, exit 0 |
| django, 6 GB heap | pass | 2,926 indexed | 4:25.82 | 4,415,040 KB | 103,954,429 bytes | complete `index.scip`, exit 0, zero swaps |

The successful large-repository command was:

```text
/usr/bin/time -v env NODE_OPTIONS=--max-old-space-size=6144 scip-python index .
```

This establishes conditional capability rather than safe default behavior:
large-repository indexing works on this 10 GB WSL VM with an explicit 6 GB V8
heap, but the default heap is insufficient for SymPy and the 8 GB setting creates
unsafe whole-VM pressure. Counterintuitively, 6 GB was safer and completed.

## Item 1-2 verification findings

| Check | Result | Evidence |
|---|---|---|
| 1. Ground-truth `CALLS` | PASS | `Session.request` contains the required calls to `Request`, `prepare_request`, `merge_environment_settings`, and `send`; `Session.send` is therefore covered as a concrete callee. Permanent regression: `tests/unit/test_repository_verification.py::test_session_request_ground_truth_calls`. |
| 2. Canonical Python syntax | PASS | All 3,504 L2/L3 representations from requests and Flask parsed with `ast.parse`: 0 failures and 0 exemptions. Permanent regression: `tests/unit/test_repository_verification.py::test_all_canonical_representations_parse`. |
| 3. Large-repository capability | PASS with operational constraint | SymPy and Django both produced complete indexes with a 6 GB V8 heap. SymPy does not work with the default heap on this VM; 8 GB is unsafe. |

Final verification commands and results after recovery from the interrupted run:

```text
SCIP_CLI=~/targets/.tools/scip .venv/bin/pytest -q tests/unit/test_repository_verification.py
2 passed in 8.78s

SCIP_CLI=~/targets/.tools/scip .venv/bin/pytest -q
15 passed, 16 skipped in 5.18s
```

The 16 skips are the pre-existing HydraDB integration matrix, which requires the
external Item 0 runtime; there were no test failures or errors. The Check 1
ground-truth set specifically requires these five resolved `CALLS` destinations:
`requests._types.is_prepared`, `requests.models.Request`,
`requests.sessions.Session.prepare_request`,
`requests.sessions.Session.merge_environment_settings`, and
`requests.sessions.Session.send`.

### Additional observed failures

Flask's current `pyproject.toml` was rejected by scip-python's TOML parser at line
192, column 54 on all six parse attempts. scip-python then continued with default
configuration, indexed all 83 files, and wrote a usable index. No target source or
configuration was changed.

The initial scale check against SymPy did not complete. At 973 of 1,557 files,
Node/V8 aborted with:

```text
FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory
Command terminated by signal 6
```

Elapsed time was 2:45.94 and peak RSS was 2,287,232 KB. The incomplete 70-byte
`index.scip` was not usable. The later heap-size capability survey did not debug
or modify SymPy or the extractor: 8 GB was rejected at the VM safety boundary,
while 6 GB completed as recorded above.
