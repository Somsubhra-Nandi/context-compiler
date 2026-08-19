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

## Controlled comparison: structural compiler vs graph-ranked top-k

Arm B is a controlled graph-ranked top-k retriever: it ranks a one-hop neighborhood using the same graph scorer but does not run structural closure, profile propagation, or closure-bundle packing. It is not a vector or embedding baseline.

The canonical checked-in traceback side-by-side artifact for `QuerySet.filter → _filter_or_exclude → Query.build_filter` emitted nearly the same amount of code: 26 symbols and 4,674 actual tokens (5,048 budgeted) for the compiler, versus 27 symbols and 4,271 actual tokens (4,716 budgeted) for Arm B. The compiler additionally supplied 22 identity hints and named 24 identities in total, versus 0 hints and 2 named identities for Arm B; the compiler context satisfied structural closure while Arm B's independent `is_closed()` check was false. See [`docs/spikes/demo-side-by-side.md`](docs/spikes/demo-side-by-side.md) and its [measurement JSON](docs/spikes/data/demo-side-by-side.json).

## Agent case study: root-cause correctness

[benchmarks/b1-agent-case-study/RESULT.md](benchmarks/b1-agent-case-study/RESULT.md) is a controlled agent case study (`n = 1`), not an aggregate benchmark. Both arms used the same broken Django fixture, natural-language bug report, project instructions, model (`gpt-5.6-luna`), reasoning level (`low`), and Codex CLI (`0.147.0`). Context Compiler was available only in the Compiler arm.

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

The focused regression passed and `Queries6Tests` passed 9/9 for both arms. Full-suite results were: Compiler — 505 tests run, 0 failures; 14 skipped, 1 expected failure. Baseline — 505 tests run, 3 failures; 14 skipped, 1 expected failure. The baseline agent made a downstream `Query.split_exclude()` boolean rewrite; its failures were `ExcludeTests.test_exclude_m2m_through`, `ManyToManyExcludeTest.test_exclude_many_to_many`, and `ManyToManyExcludeTest.test_ticket_12823`.

The original expectation was that structural retrieval might reduce agent search/token consumption. B1 did not support that prediction: the Compiler arm consumed **more total Codex tokens**, 38,949 versus 18,564 for baseline. The observed separation was correctness:

```text
Compiler: upstream trim_start invariant repair → 505 tests run, 0 failures
Baseline: downstream split_exclude symptom repair → 505 tests run, 3 failures
```

Reasoning-output tokens were lower in the Compiler arm (174 vs 566), but this single trial is not evidence of a general reasoning-efficiency claim. See the [Compiler patch](benchmarks/b1-agent-case-study/compiler.patch) and [baseline patch](benchmarks/b1-agent-case-study/baseline.patch).

## Guarantees / invariants

- **I4:** budgeted cost is an upper bound on emitted cost, including model-visible header, provenance, and identity metadata. The validated 200-trial run had 0/200 violations.
- **I6:** optional context is admitted as a bundle with the mandatory closure it induces, not as an isolated high-scoring node. The validated run was closed in 200/200 trials.

These are checks on the compiled artifact and cost model. They do not guarantee semantic completeness, correct seed resolution, or a correct agent answer.

## Limitations

- Natural-language seed resolution is weaker than explicit symbol seeds; arbitrary prose may resolve poorly or fail. So this remains as future work.
- Candidate supply can be the binding constraint: the aggregate run had roughly 12.0 candidates and 11.0 admissions at the median.
- B1 is a single controlled case study (`n = 1`), not a statistically significant benchmark or token-efficiency estimate.
- No vector-top-k performance claim is made. An attempted pinned CodeBERT setup did not complete within the experiment time limit, so vector-baseline measurements remain future work; this is not a claim about vector search itself.
- Current evidence is primarily Python/Django. Graph quality depends on extractor coverage, symbol resolution, and edge quality.

## Development / tests

```bash
pip install -e '.[dev]'
python -m pytest tests/unit tests/mcp -q
# Graph tests require HydraDB and an ingested target repository.
python -m pytest tests/graph -q
```

Useful validation scripts include `scripts/validate_budget_django.py`, `scripts/validate_closure_django.py`, `scripts/validate_emit_django.py`, `scripts/validate_baseline_arm_b.py`, `scripts/demo_side_by_side.py`, and `scripts/capture_demo_side_by_side.py`.

## Repository evidence / docs

- [Implementation spec](docs/specs/context-compiler-v1.3.md)
- [Post-A6 measured results](docs/specs/amendment-a6.md)
- [Seed-resolution evidence](docs/spikes/seeds-item-8-results.md)
- [MCP evidence](docs/spikes/mcp-item-7-results.md)
- [Arm B aggregate results](docs/spikes/baseline-arm-b-results.md)
- [Arm B side-by-side example](docs/spikes/baseline-arm-b-example.md)
- [B1 agent case study](benchmarks/b1-agent-case-study/RESULT.md)
- [B1 Compiler patch](benchmarks/b1-agent-case-study/compiler.patch) and [baseline patch](benchmarks/b1-agent-case-study/baseline.patch)
