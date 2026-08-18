# Amendment A7 — Sidecar traceback ranges

**Status:** Adopted. Raised by Item 8's traceback-aware seed resolution.
**Scope:** `graph.sidecar.SymbolMeta` and its contract test only. The graph
schema and budget model are unchanged.

## A7.1 — The contract addition

Item 8 added `start_line` and `end_line` to `SymbolMeta`. Seed resolution uses
the inclusive source range to map traceback frames to the innermost indexed
symbol. These are scalar source-location facts, so they belong in the sidecar,
not in graph topology: Amendment A2's reasoning keeps application-owned
scalars in the sidecar and leaves relationships and reachability in the graph.

The fields are appended to the existing tuple to preserve the A1.1 ordering of
all prior fields:

```text
..., evaluable, start_line, end_line
```

Adding these two scalars does not change `cost()`, its framing terms, any node
token count, or any measured token figure. They are used only by seed
resolution and identity/source-location handling; no admission calculation
reads them.

The sidecar contract test is updated to cite A7 rather than silently retaining
the falsified A1.1 field tuple.

## A7.2 — Fixture-leak finding (node-count drift)

`tests/integration/test_hydradb_compat.py` wrote twelve fixture nodes (ids
`100101`-`100602`, one a `Test` node) directly against the live graph, and they
persisted through A6-bis, Item 8, and Item 10a's Arm B run before the graph
suite's count assertions caught the drift. The suite now tears its own fixture
ids down before and after the module (`tests/integration/test_hydradb_compat.py`).
A future session that sees `Symbol`/`Test` node counts drift from
`symbols.jsonl` should check for this class of leak before assuming ingest or
graph corruption.
