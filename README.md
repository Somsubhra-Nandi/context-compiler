# Context Compiler

Context Compiler is a structural retrieval/compiler layer for coding agents. It takes a natural-language task or explicit code-symbol seeds, resolves them against a repository symbol/call graph, and emits a token-budgeted context of declarations, bodies, provenance, and identity hints. Unlike ordinary top-k retrieval, it expands through typed structural relationships and preserves the identities needed to interpret selected code. It is a navigation and verification aid—not a guarantee that an agent will produce the right answer.

## Why?

An agent can find the function named in a bug report and still miss the caller, callee, type, or invariant that explains its behavior. Context Compiler makes those relationships explicit, selects the richest profile that fits the budget, and checks structural closure.

## What it does

```text
task / explicit symbols
        │
        ▼
   seed resolution
        │
        ▼
 structural graph expansion
        │
        ▼
 profile + mandatory floor
        │
        ▼
 budgeted candidate packing
        │
        ▼
 code + identity emission
        │
        ▼
 MCP-capable coding agent
```

The extractor writes symbol and relationship sidecars; HydraDB stores the graph; the compiler resolves seeds, expands mandatory relationships, ranks optional candidates, packs closure bundles under a token budget, and emits canonical code plus identities.

## Architecture

- **Extractor and sidecars.** `context_compiler.extract` combines Python syntax analysis with symbol resolution and writes `symbols.jsonl`, `edges.jsonl`, and an offset index. Canonical L2 declarations and L3 bodies, references, token counts, and source locations are stored for later use.
- **HydraDB.** `context_compiler.graph.ingest` loads symbols and typed relationships into a Neo4j-compatible HydraDB graph. The compiler reads forward edges for mandatory propagation and reverse `CALLS` neighborhoods for optional candidates.
- **Graph compiler.** Profiles are monotone: P3 FULL, P2 COMPACT, P1 MINIMAL, and P0 FLOOR. The first profile whose mandatory context fits is selected; a too-large minimum closure returns `CLOSURE_BUDGET_EXCEEDED`.
- **Packing and emission.** Optional candidates are admitted as closure bundles: a candidate and the mandatory context it induces enter together. The emitter groups canonical code by file/class and adds mandatory identities and truncatable identity hints.
- **MCP layer.** `context_compiler.mcp` serves the compiler over stdio for an MCP-capable coding agent.

## Quick start

The commands below use the repository's Django reference layout.

```bash
git clone https://github.com/Somsubhra-Nandi/context-compiler.git
cd context-compiler
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Start HydraDB at bolt://127.0.0.1:7687.
bash scripts/run_hydradb.sh start

# Extract and ingest a target repository.
python -m context_compiler.extract.pipeline --repo ~/targets/django --out ~/out/django
python -m context_compiler.graph.ingest \
  --symbols ~/out/django/symbols.jsonl \
  --edges ~/out/django/edges.jsonl \
  --offset-index ~/out/django/offsets.json

# Start the MCP server, or register this stdio command in your agent.
CC_SYMBOLS=~/out/django/symbols.jsonl \
CC_EDGES=~/out/django/edges.jsonl \
CC_OFFSETS=~/out/django/offsets.json \
python -m context_compiler.mcp.server
```

Defaults are `~/out/django/{symbols.jsonl,edges.jsonl,offsets.json}`, `bolt://127.0.0.1:7687`, and an 8,000-token budget. Override them with `CC_SYMBOLS`, `CC_EDGES`, `CC_OFFSETS`, `CC_BOLT_URI`, and `CC_BUDGET`. The MCP server has been tested with Codex CLI and Claude Code. Register it with Codex CLI:

```bash
codex mcp add context-compiler -- \
  /path/to/context-compiler/.venv/bin/python \
  -m context_compiler.mcp.server
```

Claude Code is an additional tested-client example:

```bash
claude mcp add -s user context-compiler -- \
  /path/to/context-compiler/.venv/bin/python \
  -m context_compiler.mcp.server
```

## MCP tools

| Tool | Purpose |
|---|---|
| `compile_context(task \| seeds, budget)` | Primary path: resolve seeds, compile a budgeted context, emit code and identities, and return status/metrics JSON. |
| `explain_inclusion(fqn, task \| seeds)` | Show the full derivation chain explaining why a symbol was included. |
| `impact_cone(fqn, max_depth)` | Return a bounded reverse-`CALLS` cone of symbols potentially affected by a change; this is reachability, not a behavioral prediction. |

Task resolution parses CPython traceback frames, then uses deterministic identifier similarity plus a small connectivity rerank. Similarity is used only for entry; structural expansion follows graph edges. It is not a production-grade semantic retrieval layer, so explicit seeds are more reliable when concrete symbols are known.

## Example

```text
compile_context(
  seeds=[
    "django.db.models.query.QuerySet.filter",
    "django.db.models.sql.query.Query.build_filter"
  ],
  budget=8000
)
```

The intended workflow is:

```text
user debugging question
        ↓
agent performs minimal localization
        ↓
agent identifies concrete symbols
        ↓
compile_context(seeds=[...])
        ↓
structural expansion and packing
        ↓
agent verifies current source and edits
```

## Measured results

Aggregate compiler measurements from the corrected six-seed Django run: 200 deterministic trials, `rng_seed=20260817`, and an 8,000-token budget. These are compiler measurements, not agent-success rates.

| Measure | Result |
|---|---:|
| P3 FULL admissions | 194/200 (97.0%) |
| I4 budget violations | 0/200 |
| I6 structural closure | 200/200 |
| Closure size | median 36; p90 56; max 114 |
| Compiled emitted symbols | median 34 |
| Compiled budgeted tokens | median 5,602 (70.0% of 8,000) |
| Compiler latency | 483.11 ms median; 1,549.99 ms p99 |
| HydraDB graph round trips | median 24 |

“Emitted symbols” are L2/L3 code blocks. A context may also name L1 identities: FQN plus file/line hints for symbols needed to interpret emitted code but not emitted as declarations or bodies. Mandatory identities are budgeted and never truncated; optional identity hints use a reserve and may be truncated. The mandatory floor in this run was median 21 emitted symbols / 3,718.5 tokens; packing brought that to median 34 / 5,602.

## Controlled comparison: vector, graph top-k, and structural compiler

Arm A is a frozen global vector baseline using one normalized
`repr_L2_text` embedding per symbol from
`jinaai/jina-embeddings-v2-base-code` at revision
`516f4baf13dec4ddddda8631e019b5737c8bc250`. It ranks all non-seed symbols by
cosine similarity to the mean seed vector and greedily admits them without a
graph or structural closure. Arm B ranks a one-hop graph neighborhood without
structural closure, profile propagation, or closure-bundle packing. Arm C is
the full structural compiler.

The canonical 8k traceback example uses the same resolved
`QuerySet.filter → QuerySet._filter_or_exclude → Query.build_filter` seeds,
budget, and emitter for all three arms:

| Measure | Arm A — Vector | Arm B — Graph top-k | Arm C — Context Compiler |
|---|---:|---:|---:|
| Emitted symbols | 58 | 27 | 26 |
| Actual emitted tokens | 7,308 | 4,271 | 4,674 |
| Budgeted tokens | 7,996 | 4,716 | 5,048 |
| Utilization | 99.95% | 58.95% | 63.10% |
| Overlap with compiler structural hints | 2/22 | 0/22 | 22/22 by construction |
| Structural identity hints exposed | 0 | 0 | 22 |

The separate frozen Arm A run covered 200 deterministic six-seed trials with
the canonical Arm B seed lists verified ID-for-ID. All 200 returned `OK`, with
zero actual or budgeted overruns; median vector retrieval was 43.267 ms and
median total Arm A processing was 1,338.907 ms. One-time embedding/index
construction is reported separately in the three-way evidence.

The 22 compiler hints are not independent ground-truth labels. Arm C's 22/22
is true by construction, so compiler-hint overlap is a descriptive diagnostic,
not an accuracy or correctness benchmark. In this example Arm A nearly fills
the token ceiling, while Arm C stops once its structural slice has been
selected: budget is a ceiling, not a target, and utilization alone is not a
quality measure.

One diagnostic connects this comparison to the independently existing B1 case
study: `django.db.models.sql.query.Query.trim_start`, the upstream regression
location repaired by the successful Compiler arm, ranked 147th globally by
vector similarity but was not admitted into Arm A's 8,000-token context. Arm C
surfaced it as an identity hint. This describes retrieval and packing in the
frozen example; it does not show that vectors cannot find `trim_start` or that
vector search is universally inferior.

See the [three-way evidence](docs/spikes/demo-three-way.md), its
[generated output](docs/spikes/data/demo-three-way.json), and the
[deterministic overlap/rank analysis](docs/spikes/data/demo-three-way-analysis.json).
The earlier canonical [Arm B/C comparison](docs/spikes/demo-side-by-side.md)
and [measurement JSON](docs/spikes/data/demo-side-by-side.json) remain available
unchanged.

## Agent case study: root-cause correctness

[benchmarks/b1-agent-case-study/RESULT.md](benchmarks/b1-agent-case-study/RESULT.md) is a controlled agent case study (`n = 1`), not an aggregate benchmark. All three primary conditions used the same broken Django fixture, natural-language bug report, project instructions, model (`gpt-5.6-luna`), reasoning level (`low`), and Codex CLI (`0.147.0`).

The final clean Compiler run independently selected:

```text
django.db.models.sql.query.Query.split_exclude
django.db.models.sql.query.Query.build_filter
django.db.models.query.QuerySet._filter_or_exclude
```

With an 8,000-token budget it returned P3 FULL: 32 emitted symbols, closure size 57, 29 declarations, 3 bodies, 25 identities, 7,139 budgeted tokens, 6,659 actual context tokens, 21 graph round trips, and 1,312.1 ms latency. The agent reached the upstream `Query.trim_start()` invariant and made:

```diff
- return trimmed_prefix, True
+ return trimmed_prefix, contains_louter
```

These figures are from the final clean rerun, which supersedes an earlier exploratory Compiler run whose worktree was reset before its patch artifact was preserved.

The focused regression passed and `Queries6Tests` passed 9/9 in all primary conditions.

| Condition | Repair location | Full `queries` suite |
|---|---|---|
| Context Compiler | upstream `Query.trim_start()` invariant repair | 505 tests, **0 failures** |
| Vector-enabled | downstream `Query.split_exclude()` repair | 505 tests, **2 failures** |
| Baseline | downstream `Query.split_exclude()` repair | 505 tests, **3 failures** |

The Vector-enabled failures were `ExcludeTests.test_exclude_m2m_through` and `ManyToManyExcludeTest.test_ticket_12823`. The Baseline failures were those two plus `ManyToManyExcludeTest.test_exclude_many_to_many`.

In the vector-enabled run, the vector MCP was available but was not invoked by the agent; the retrieval-level Arm A results are reported separately in the controlled [three-way benchmark](docs/spikes/demo-three-way.md).

The original expectation was that structural retrieval might reduce agent search/token consumption. B1 did not support that prediction: the Compiler arm consumed **more total Codex tokens**, 38,949 versus 18,564 for baseline. The observed separation was correctness:

```text
Compiler: upstream trim_start invariant repair → 505 tests run, 0 failures
Vector-enabled: downstream split_exclude repair → 505 tests run, 2 failures (vector MCP not invoked)
Baseline: downstream split_exclude symptom repair → 505 tests run, 3 failures
```

Reasoning-output tokens were lower in the Compiler arm (174 vs 566), but this single trial is not evidence of a general reasoning-efficiency claim. The vector-enabled condition used 67,506 total tokens and 1,733 reasoning tokens; it is likewise `n = 1` and does not support token-efficiency claims. See the [Compiler patch](benchmarks/b1-agent-case-study/compiler.patch), [vector-enabled patch](benchmarks/b1-agent-case-study/vector-enabled.patch), and [baseline patch](benchmarks/b1-agent-case-study/baseline.patch).

## Guarantees / invariants

- **I4:** budgeted cost is an upper bound on emitted cost, including model-visible header, provenance, and identity metadata. The validated 200-trial run had 0/200 violations.
- **I6:** optional context is admitted as a bundle with the mandatory closure it induces, not as an isolated high-scoring node. The validated run was closed in 200/200 trials.

These are checks on the compiled artifact and cost model. They do not guarantee semantic completeness, correct seed resolution, or a correct agent answer.

## Limitations

- Natural-language seed resolution is weaker than explicit symbol seeds; arbitrary prose may resolve poorly or fail. So this remains as future work.
- Candidate supply can be the binding constraint: the aggregate run had roughly 12.0 candidates and 11.0 admissions at the median.
- B1 is a single controlled case study (`n = 1`), not a statistically significant benchmark or token-efficiency estimate.
- An earlier exploratory pinned CodeBERT setup did not complete within its experiment time limit. It has been superseded by the completed frozen Jina Arm A vector baseline. The three-way result compares retrieval objectives on this corpus; it is not a claim that vector search is universally inferior.
- Current evidence is primarily Python/Django. Graph quality depends on extractor coverage, symbol resolution, and edge quality.

## Development / tests

```bash
pip install -e '.[dev]'
python -m pytest tests/unit tests/mcp -q
# Graph tests require HydraDB and an ingested target repository.
python -m pytest tests/graph -q
```

Useful validation scripts include `scripts/validate_budget_django.py`, `scripts/validate_closure_django.py`, `scripts/validate_emit_django.py`, `scripts/validate_baseline_arm_a.py`, `scripts/validate_baseline_arm_b.py`, `scripts/demo_three_way.py`, `scripts/demo_side_by_side.py`, and `scripts/capture_demo_side_by_side.py`.

## Repository evidence / docs

- [Implementation spec](docs/specs/context-compiler-v1.3.md)
- [Post-A6 measured results](docs/specs/amendment-a6.md)
- [Seed-resolution evidence](docs/spikes/seeds-item-8-results.md)
- [MCP evidence](docs/spikes/mcp-item-7-results.md)
- [Arm B aggregate results](docs/spikes/baseline-arm-b-results.md)
- [Arm B side-by-side example](docs/spikes/baseline-arm-b-example.md)
- [Three-way vector / graph top-k / structural compiler evidence](docs/spikes/demo-three-way.md)
- [B1 agent case study](benchmarks/b1-agent-case-study/RESULT.md)
- [B1 Compiler patch](benchmarks/b1-agent-case-study/compiler.patch), [vector-enabled patch](benchmarks/b1-agent-case-study/vector-enabled.patch), and [baseline patch](benchmarks/b1-agent-case-study/baseline.patch)
