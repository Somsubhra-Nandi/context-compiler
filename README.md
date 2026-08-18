# Context Compiler

Compiles a token-budgeted, structurally-closed context for a task against a
Python codebase: given a task description or explicit symbol names, it
returns exactly the declarations, bodies and dependency identities needed to
answer it, under a fixed token budget — with a machine-checked guarantee
(invariant I4) that the actual token count never exceeds what was budgeted.

The compiler itself (`context_compiler.graph`, `context_compiler.emit`) is a
library over a HydraDB (Neo4j-compatible) graph of a repository's symbols and
call edges. `context_compiler.mcp` exposes it as three tools over the Model
Context Protocol, so an agent like Claude Code can call it directly instead of
grepping the repo.

See `docs/specs/context-compiler-v1.3.md` for the full design and
`docs/spikes/` for the validation record of each numbered item.

## Install and use the MCP server

### Prerequisites

1. **HydraDB running** and reachable at `bolt://127.0.0.1:7687` (or set
   `CC_BOLT_URI`). See `docs/spikes/hydradb-item-0-results.md` for setup;
   `scripts/run_hydradb.sh` starts a local instance.
2. **A repository extracted and ingested.** For Django, the reference target:

   ```bash
   source .venv/bin/activate
   python -m context_compiler.extract.pipeline --repo ~/targets/django --out ~/out/django
   python -m context_compiler.graph.ingest \
       --symbols ~/out/django/symbols.jsonl \
       --edges ~/out/django/edges.jsonl \
       --offset-index ~/out/django/offsets.json
   ```

   This produces `~/out/django/{symbols.jsonl,edges.jsonl,offsets.json}` — the
   MCP server's defaults point here, so no further config is needed for the
   Django target specifically.
3. **The `mcp` package**, installed via this project's own dependencies:

   ```bash
   pip install -e .
   ```

### Register the server with Claude Code

```bash
claude mcp add -s user context-compiler -- \
  /path/to/context-compiler/.venv/bin/python \
  -m context_compiler.mcp.server
```

`-s user` registers it for every project, not just the current one, since the
server is repo-agnostic (it points at whatever `~/out/<repo>` the env vars
name). Verify it started:

```bash
claude mcp get context-compiler
```

which should report `Status: ✔ Connected`. If it instead fails to connect,
the server printed a one-line diagnosis to stderr naming the exact missing
path or unreachable URI — check `claude mcp get context-compiler`'s logs, or
run the module directly to see the message:

```bash
python -m context_compiler.mcp.server
```

### Configuration

All via environment variables, with defaults that work out of the box against
the Django reference target:

| Variable | Default | |
|---|---|---|
| `CC_SYMBOLS` | `~/out/django/symbols.jsonl` | the sidecar |
| `CC_OFFSETS` | `<CC_SYMBOLS dir>/offsets.json` | byte-offset index for emission text |
| `CC_EDGES` | `<CC_SYMBOLS dir>/edges.jsonl` | out/in-degree tables |
| `CC_BOLT_URI` | `bolt://127.0.0.1:7687` | HydraDB |
| `CC_BUDGET` | `8000` | default token budget, overridable per call |

To point at a different repository, pass these as `env` entries on `claude mcp
add` (`--env CC_SYMBOLS=... --env CC_OFFSETS=... --env CC_EDGES=...`) or export
them before starting the server.

### The three tools

| Tool | What it does |
|---|---|
| `compile_context(task \| seeds, budget)` | The product: a budgeted, structurally-closed context, rendered as text plus a trailing JSON block of the figures (status, profile, token counts, latency). |
| `explain_inclusion(fqn, task \| seeds)` | The full derivation chain for one symbol back to a seed — not the one-line trailer `compile_context`'s output shows. |
| `impact_cone(fqn, max_depth)` | Bounded reverse-`CALLS` closure: what could be *potentially* affected if `fqn` changes. |

`seeds` accepts exact fully-qualified names or unambiguous suffixes
(`QuerySet.filter` resolves to `django.db.models.query.QuerySet.filter`).
Task-based resolution first parses CPython traceback frames through the
sidecar's file/line ranges, then uses deterministic identifier similarity and a
small connectivity rerank. Similarity is used for entry and rejected for
expansion: structural closure follows graph edges, never similarity results.
BM25, embeddings, and LLM proposal are deliberately out of scope for this
session, so prefer explicit `seeds` when you know them. See
`docs/spikes/seeds-item-8-results.md` for the worked Django traceback.

### Example

Once registered, in any Claude Code session:

> Use context-compiler to get context on why QuerySet.filter produces the SQL
> it does, then explain what build_filter is doing.

Claude resolves the seeds, calls `compile_context`, and answers from the
returned declarations and bodies — 22 symbols, 4,368 tokens (4,715 budgeted),
one tool call.
See `docs/spikes/mcp-item-7-results.md` for the verbatim transcript and two
more (`explain_inclusion`, `impact_cone`).

The 200-trial A6 baseline separates the mandatory floor from the compiled
context: **21 emitted / 3,718.5 tokens** at the floor, versus **34 emitted /
5,602 tokens** after packing (median, 8,000-token budget). “Emitted” counts
L2/L3 code blocks; “named” also includes zero-cost L1 identities. Candidate
supply is the binding constraint at this utilisation: 12.0 candidates and
11.0 admissions at the median.

Item 10a's controlled comparison is against **graph-ranked top-k without
structural closure** (Arm B). The vector top-k arm is the obvious next arm.
The Arm A stretch was abandoned after the pinned CodeBERT download failed to
complete within its 20-minute limit; no vector measurements are claimed.

## Development

```bash
python -m pytest tests/unit tests/mcp -q   # no database, seconds
python -m pytest tests/graph -q            # needs HydraDB + an ingested repo
```
