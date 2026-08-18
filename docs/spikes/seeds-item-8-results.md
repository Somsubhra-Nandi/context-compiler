# Item 8 — Scoped seed resolution results

Item 8 shipped two seed mechanisms and kept the public contracts unchanged:

```text
resolve_task(task, sidecar, top_k) -> list[int]
resolve_seeds(queries, by_fqn) -> list[int]
```

Explicit names still use the application-side FQN map. Task resolution first
parses CPython traceback frames. A frame is matched against the sidecar's
relative file path and inclusive `start_line`/`end_line` range; when nested
symbols overlap, the frame function name and then the smallest range select
the symbol. Results are reversed to innermost-first order and repeated node ids
are removed while preserving first occurrence. Unindexed files and indexed
files with no symbol at the reported line are skipped with a diagnostic when a
caller supplies the optional `diagnostics` list. They are not errors.

If no traceback frames parse, the resolver falls through to deterministic FQN
token overlap. With an explicitly supplied graph handle, its top 20 candidates
are connectivity-reranked. Each candidate scores one point per other candidate
reachable in either direction within two `CALLS` hops; ties use `(score, fqn)`.
The server passes its already-open forward and reverse handles explicitly, so a
baseline arm can make the same call with the same graph state and obtain the
same seeds. No module-level connection is consulted.

## Worked Django traceback

This is a real path and real line-range lookup against the corrected Django
sidecar (`/home/.../django` is shortened only in the traceback display):

```text
Traceback (most recent call last):
  File "/srv/django/django/db/models/query.py", line 1682, in filter
  File "/srv/django/django/db/models/query.py", line 1699, in _filter_or_exclude
  File "/srv/django/django/db/models/sql/query.py", line 1510, in build_filter
```

The returned innermost-first seeds were:

```text
6734709972156213732  django.db.models.sql.query.Query.build_filter
8580647764743840513  django.db.models.query.QuerySet._filter_or_exclude
5938239568010518293  django.db.models.query.QuerySet.filter
```

The sidecar ranges are `1488–1658`, `1695–1704`, and `1679–1685`,
respectively. The absolute traceback prefix does not matter: path matching
accepts the indexed repository-relative suffix. A frame from an unindexed
application file would be retained in diagnostics and omitted from the seed
list.

## What was cut

BM25, embedding top-k, and LLM proposal were deliberately cut for this session.
Embedding search was not wired because the baseline embedding directory was
not part of the workspace. Connectivity reranking is enough to demonstrate the
graph-native part without introducing another similarity system. Similarity is
used for entry only and is rejected for structural expansion: closure follows
the graph propagation rules, not the resolver's ranking.

## The 20-candidate cap

Connectivity reranking caps its input at **20 candidates**, even though
`graph/pack.py` admits a separate 150-candidate packing pool. The two caps serve
different loops. Item 8's connectivity check uses serial reverse reads because
A3 established that HydraDB has no batched reverse-read form. Twenty bounds that
I/O and filters the demonstrable garbage in the candidate pool that A5 observed;
reusing the packer's 150 would make the seed resolver pay for a much larger,
lower-precision rerank. The reranker also reuses `HUB_SKIP_DEGREE = 500` and the
sidecar-loaded `in_degrees` table; it does not invent a second hub policy.

## Tests

The MCP seed tests cover exact and suffix resolution, recursive traceback
deduplication, an unindexed file, an indexed file with no symbol at the line,
connectivity ordering, deterministic ties, the 20-candidate bound, and hub
skipping. The direct `Query` class-seed regression uses the corrected graph
fact that its outgoing `CALLS` count is zero: the fixture closure stays at one
L3 node and passes the independent closed check.

### Numbered discrepancy

1. The five-mechanism list in §6.4 remains a complete target for a later item,
   not an Item 8 implementation checklist. This session intentionally ships
   traceback parsing and connectivity reranking only; BM25, embeddings, and
   LLM proposal remain unimplemented.
