# Task: Item 7 — MCP Server

Items 0–6 are complete and committed. This turns the library into a product.

**Keep it small.** Everything underneath works and is validated. This item is a thin protocol layer over machinery that already exists — if you find yourself changing `graph/` or `emit/` logic, stop and ask.

---

## 0. Source of truth

```
docs/specs/context-compiler-v1.3.md    §8 (product surface)
docs/specs/amendment-a1.md .. a4.md
docs/spikes/graph-item-5-results.md    latency and round-trip figures
docs/spikes/emit-item-6-results.md     what emit() returns
docs/spikes/emit-item-6-example.md     what a compiled context looks like
```

## 1. Scope

**You own** `src/context_compiler/mcp/`, `tests/mcp/`, `docs/spikes/mcp-item-7-results.md`, and README additions.

**Do not modify** `graph/`, `emit/` or `extract/` logic. Read-only consumers. If a change is genuinely needed, report and stop.

**Do not implement** real seed resolution (Item 8 — see §3), runtime tracing (Item 9), or evaluation (Item 10).

## 2. Server shape

Python MCP SDK (`pip install mcp` in `.venv`), **stdio transport** — that is what Claude Code and Cursor speak.

Load once at startup, never per call:

```
sidecar        0.61 s, 20 MB
offset index   ~/out/django/offsets.json
Bolt driver    one connection, reused
```

Config via env vars with sane defaults: `CC_SYMBOLS`, `CC_OFFSETS`, `CC_BOLT_URI`, `CC_BUDGET` (default 8000).

**Fail fast and legibly.** If HydraDB is unreachable or `symbols.jsonl` is missing, exit at startup with a one-line diagnosis naming the exact path or URI. A server that starts and then fails every call is worse than one that refuses to start.

## 3. `compile_context` — the product

```
compile_context(task: str = None, seeds: list[str] = None, budget: int = 8000)
```

**Seed resolution is Item 8**, so ship a deliberately minimal placeholder:

- `seeds` given → resolve each as an exact FQN, else a unique suffix match (`QuerySet.filter` → `django.db.models.query.QuerySet.filter`). Ambiguous or missing → name it in the error.
- `task` given → tokenize, match tokens against FQN segments, rank by match count, take the top 6.

**~30 lines. Mark it `PLACEHOLDER(item-8)` in the code and say so in the tool description.** Item 8 replaces it with the hybrid resolver (BM25 + embeddings + traceback + LLM proposal + connectivity rerank). Do not build any of that now.

Returns the `emit()` string verbatim. It already carries the header, sections, provenance and identity index.

**Also return the structured figures** as a second content block or trailing JSON: status, profile, symbol count, budgeted and actual tokens, round trips, latency. §11's unresolved issue — the MCP response carrying both the claimed and actual token count — is now safe, because A4.1 made `token_margin ≤ 0` hold on 200/200.

## 4. `explain_inclusion`

```
explain_inclusion(fqn: str, task: str = None, seeds: list[str] = None)
```

Recompiles (or uses a cached last context) and renders the **full derivation chain** for one symbol — not the one-line trailer emission ships:

```
django.utils.tree.Node.add                          [L2, 28 tokens]
  ← Query.add_q          CALLS   rule: CALLS(L3)→L2
  ← Query._add_q         CALLS   rule: CALLS(L3)→L2
  included because: 2 mandatory rules fired
  runtime-confirmed: no evidence yet (Item 9)
```

This is the answer to *"how do you know this is the right context"* and it is **not budget-bound** — it is a separate call, per §7.4.

If the symbol is absent from the context, say so and say what *would* pull it in.

## 5. `impact_cone` — mind the reverse-read constraint

```
impact_cone(fqn: str, max_depth: int = 2)
```

Reverse closure: what could be affected if this symbol changes.

**A3.1 applies and it bites here.** There is no batched reverse read — the id-bound node must be the arrow's source — so reverse traversal is `|frontier|` single-source reads per hop. A hub costs seconds and `LIMIT` does not help, because it bounds rows returned, not the scan.

Required bounds:

- `max_depth` capped at 2
- **hub skip**: no reverse read on any symbol above 500 in-degree; report it as truncated rather than silently dropping it
- **frontier cap**: at most 200 nodes expanded per hop
- **hard deadline**: 10 s, return partial results with `truncated: true` rather than hanging

Return counts by depth plus the top 30 by `idf` relevance, grouped by file. Never dump 2,000 FQNs into a chat window.

**Wording: "potentially affected", never "what breaks."** Reverse reachability is an over-approximation, the tool computes one, and it should present itself as one. Overclaiming here is the easiest thing for a reviewer to puncture, and the honest version is still impressive.

## 6. Tool descriptions matter

An MCP tool description is a prompt — the calling model reads it to decide when to invoke the tool. Write them for that reader, not for a human browsing docs. State what each returns, when it is the right tool, and what its limits are. Mention the placeholder seed resolver so the model knows to pass explicit `seeds` when it can.

## 7. Acceptance gate — a real session, not just tests

**Unit** (`tests/mcp/`, no HydraDB): tool schemas validate; seed resolution handles exact, suffix, ambiguous and missing; errors are structured, not tracebacks; `impact_cone` bounds are enforced against a stub.

**Live**: register the server with Claude Code and use it.

```bash
claude mcp add context-compiler -- \
  /home/somsubhra_nandi/context-compiler/.venv/bin/python \
  -m context_compiler.mcp.server
```

Then in a fresh Claude Code session, from a directory that is **not** this repo, run three real interactions and **paste the transcripts verbatim** into the results doc:

1. *"Use context-compiler to get context on why QuerySet.filter produces the SQL it does, then explain what build_filter is doing."*
2. *"Why was TokenPolicy… "* — pick a symbol from context 1 and ask `explain_inclusion` about it.
3. *"What could be affected if I change Query.build_filter?"*

**That transcript is the demo.** It is worth more than any test in the suite, because it is the only artifact that shows the thing working as a product. Make interaction 1 a good one.

## 8. README

Add an install-and-use section: prerequisites (HydraDB running, Django ingested), the `claude mcp add` line, the three tools, and one screenshot-able example. Someone should be able to go from clone to a working tool call by following it.

## 9. Results doc

`docs/spikes/mcp-item-7-results.md`: startup time and memory; per-tool latency (median, p95); the three transcripts verbatim; `impact_cone` truncation rates on hub symbols; anything the protocol layer surfaced about the layers beneath.

## 10. Time box

Server core 1 h. Tools 45 min. Live session and transcripts 30 min. README 15 min. Overrun → report and stop.

Commit after the server works, again after the live transcripts. Do not start Item 8.

Don't spawn background polling loops — run long commands to a log and check the log.
