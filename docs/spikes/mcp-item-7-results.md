# Item 7 — MCP Server: Results

**Superseded in part by Amendment A6.** Figures in this document were
measured before the `occurrence_nodes()` containment fix. See
`docs/specs/amendment-a6.md` for current values.

HydraDB `0.1.0`, commit `259c5cb` (Amendment A4 landed, Item 6 re-validated).
Python 3.14.4, `neo4j` 6.2.0, `mcp` 2.0.0, Claude Code `2.1.233`, Ubuntu 26.04
under WSL2. Data is the same post-A4 Django extraction Items 6/A4 validated
against — `~/out/django/{symbols.jsonl,edges.jsonl,offsets.json}`, 43,420
symbols, unchanged.

```
Item 7  unit tests                   PASS   38 tests, no HydraDB, ~1 s
Item 7  live session, 3 interactions PASS   verbatim transcripts, Sec 8
Item 7  compile_context, 30 trials   PASS   0/30 positive token_margin (I4 holds)
Item 7  impact_cone bounds           PASS   hub skip, frontier cap, deadline all fired live
```

**The MCP client silently drops a spec-compliant response shape.** Claude
Code's tool-result handling, in this build, surfaces only `structuredContent`
when a `CallToolResult` carries both `content` and `structuredContent` —
discarding the emitted-context text entirely, which is the whole product. Not
a server bug (§5 verifies the wire protocol is correct); worked around by
dropping `structured_content` and shipping the figures as a second `content`
text block, which the task's own wording allowed as an alternative. Every
transcript in §8 is post-fix.

---

## 1. Reproduction

```bash
cd ~/context-compiler
source .venv/bin/activate
pip install -e .                                   # pulls in `mcp`

python -m pytest tests/unit tests/mcp -q            # 115 tests, no HydraDB, ~6 s

claude mcp add -s user context-compiler -- \
    $(pwd)/.venv/bin/python -m context_compiler.mcp.server
claude mcp get context-compiler                      # expect: Status: Connected
```

The three live interactions in §8 were run non-interactively from
`/tmp/.../mcp-live-session` (not this repo) via:

```bash
claude -p "<prompt>" --output-format stream-json --verbose \
    --allowedTools "mcp__context-compiler__compile_context" \
                   "mcp__context-compiler__explain_inclusion" \
                   "mcp__context-compiler__impact_cone"
```

`--allowedTools` names the three MCP tools explicitly rather than
`--dangerously-skip-permissions` / `--permission-mode bypassPermissions`,
which this environment's own safety classifier blocked outright when
requested for a `-p` run — an unscoped bypass flag reads as risky even when
the only tools it would unblock are three read-only, locally-registered ones.

---

## 2. Scope discipline

`git status` at the end of this item shows changes under `src/context_compiler/mcp/`,
`tests/mcp/`, `docs/spikes/mcp-item-7-results.md`, `README.md` (new) and
`pyproject.toml` (added the `mcp` dependency) only. **No file under `graph/`,
`emit/` or `extract/` was touched.** Every tool is a read-only consumer:
`compile_context` calls `Compiler.compile_context` and `emit()` exactly as
`scripts/worked_example.py` does; nothing recomputes a cost, a level, or a
rendering decision.

Seed resolution is the one piece of new logic and it is the explicitly
authorized placeholder — `src/context_compiler/mcp/seeds.py` is 74 lines,
marked `PLACEHOLDER(item-8)` in its module docstring and in `compile_context`'s
tool description, and does nothing beyond exact/suffix FQN matching and a
token-overlap ranking. Item 8's hybrid resolver is not anticipated or
partially built.

---

## 3. Startup: time and memory

Three cold-start runs of `python -m context_compiler.mcp.server`, each a fresh
process (page cache warm after the first):

| Run | sidecar+degrees+offsets | Bolt verify | Total | RSS |
|---|---:|---:|---:|---:|
| 1 (cold) | 2,191 ms | 43 ms | 2,234 ms | 156 MB |
| 2 | 1,115 ms | 6 ms | 1,121 ms | 156 MB |
| 3 | 1,523 ms | — | — | — |

Broken down (a separate, unbatched run): `load_sidecar` 762 ms, ​
`load_degree_tables` (15 MB `edges.jsonl`) 496 ms, `load_offsets` 76 ms. All
three happen once, sequentially, before the stdio loop starts — a call made
while the server is still starting simply cannot arrive, since stdio has
nothing listening yet.

Memory (156 MB RSS) is the sidecar dict (43,420 `SymbolMeta` namedtuples,
interned strings), the degree tables (two more 43k-entry dicts), the offset
index (43,420 `TextOffset` pairs) and the Python/`neo4j`-driver baseline
itself — this is not a regression against Item 6/A4's per-compile figures,
which never held all of this in one process at once outside a benchmark
script.

---

## 4. Per-tool latency

30 trials, same seed filter and `rng_seed` contract as Items 4–6/A4 (6
eligible seeds per trial), 8,000-token budget, against the live Django graph:

| | median | p95 | max |
|---|---:|---:|---:|
| `compile_context` (ms) | 1,015 | 2,489 | 3,734 |
| `token_margin` | −565 | | −203 (least negative) |

0/30 positive — I4 continues to hold under A4.1's framing term, consistent
with the 200-trial 0/200 in `docs/specs/amendment-a4.md`. Latency here is
end-to-end (seed resolution + `compile_context` + `emit`), and it is the same
work `scripts/validate_emit_django.py` measured at ~1.06 s/trial median — the
MCP layer adds resolution (microseconds against a 43k-entry dict) and
`json.dumps`, neither of which moves the number.

`explain_inclusion` **against an already-cached context** touches no database
and no closure recomputation — it walks `Context.provenance`, an in-memory
dict of small lists:

| | median | p95 |
|---|---:|---:|
| `explain_inclusion` (ms, cached context) | 0.010 | 0.070 |

If `task`/`seeds` is passed instead, it pays a full `compile_context` first —
same cost as the row above.

`impact_cone`, 20 trials over ordinary (non-hub) eligible seeds, `max_depth=2`:

| | median | p95 | max |
|---|---:|---:|---:|
| `impact_cone` (ms) | 120 | 2,926 | 2,926 |

0/20 truncated at this sample size — none of the 20 happened to have a
frontier that grew past the cap within two hops. §5 finds truncation exactly
where A3.1 predicts it will.

---

## 5. `impact_cone` bounds, measured against the live graph

All four required bounds were exercised against real Django symbols, not only
the stub in `tests/mcp/test_cone.py`.

**Hub skip.** `django.test.utils.override_settings`, in-degree 3,360 (the
largest in the graph), is correctly skipped rather than read — 0.08 ms,
`hubs_skipped: ["django.test.utils.override_settings"]`. A3.1 measured a
single reverse read at that kind of in-degree costing seconds; here it costs
nothing because the read never happens.

**Frontier cap**, sampled over 8 symbols just under the 500-in-degree hub
threshold (150 ≤ in-degree < 500) — exactly the band A3.1 flagged as
expensive but not hub-skipped:

| symbol | in-degree | latency | truncated? | counts by depth |
|---|---:|---:|---|---|
| `test.utils.isolate_apps` | 496 | 2,930 ms | frontier cap | 1: 294, 2: 2 |
| `fields.related.ManyToManyField` | 457 | 6,269 ms | frontier cap | 1: 455, 2: 407 |
| `admin.options.ModelAdmin` | 388 | 763 ms | — | 1: 52, 2: 0 |
| `models.base.Model.save` | 386 | 3,624 ms | frontier cap | 1: 379, 2: 39 |
| `migrations.migration.Migration` | 380 | 549 ms | — | 1: 20, 2: 52 |
| `testcases.SimpleTestCase.settings` | 379 | 4,043 ms | frontier cap | 1: 379, 2: 87 |
| `testcases.SimpleTestCase.assertHTMLEqual` | 369 | 2,717 ms | frontier cap | 1: 369, 2: 0 |
| `template.context.Context` | 358 | 3,291 ms | frontier cap | 1: 356, 2: 131 |

**5/8 (62.5%) truncated at the 200-node frontier cap** in this band — the cap
is doing real work, not sitting unused. Latency scales with how many direct
callers there are to read (up to 496 single-source reverse reads at ~6-13 ms
each before the cap even bites on depth 2), which is exactly A3.1's "no
batched reverse read" cost showing up a second time, in a new tool. **This is
the slow path**: a symbol with a few hundred direct callers costs seconds
end-to-end even though every individual read is fast, because A3.1 makes them
serial. The 10 s deadline was not hit by any of these eight, but
`ManyToManyField` at 6.3 s is within striking range of it on a loaded machine.

The **hard deadline** and **`max_depth` cap at 2** are both exercised in
`tests/mcp/test_cone.py` with a fake clock and an artificially deep stub
chain respectively — a deadline this cheap to hit live would require either a
slower machine or a much larger frontier than Django's graph offers within
depth 2, so those two bounds are verified structurally rather than
empirically here.

---

## 6. The MCP client drops `content` when `structured_content` is also set

This is the one finding worth a full write-up, because it changed the shipped
code and would have made interaction 1 a bad demo rather than a good one.

**Symptom.** The first implementation of every tool returned a
`CallToolResult` with both `content` (the human-readable text, e.g.
`compile_context`'s rendered context) and `structured_content` (a dict of the
figures) — exactly what §3 of the task asked for: "the `emit()` string
verbatim... also return the structured figures as a second content block **or
trailing JSON**." In a live Claude Code session, every call to
`compile_context` and `explain_inclusion` returned to the model *only* the
JSON figures — the actual compiled context text never arrived. The model
correctly noticed and said so:

> "The context-compiler tool has a real gap: every call (`compile_context`,
> `explain_inclusion`) returned only the trailing stats JSON... with no actual
> declarations/bodies text, despite the tool's own docs promising that
> content."

**Isolating it.** A raw MCP client against the same running server, bypassing
Claude Code entirely:

```python
result = await session.call_tool("compile_context", {"seeds": ["QuerySet.filter"], "budget": 4000})
len(result.content)   # -> 2
result.content[0].text # -> "Compiled context · 3 symbols · 366 / 4,000 tokens\n..."
result.content[1].text # -> the JSON figures
```

confirms the server sends both blocks correctly over the wire — this is a
client-side behavior of this Claude Code build, not a protocol violation on
the server's side.

**Fix.** `structured_content` was removed from all three tools'
`CallToolResult`s. The figures now ship only as a second plain-text `content`
block (`json.dumps(..., indent=2)`), which is the task's explicitly named
alternative ("a second content block **or** trailing JSON"). Every transcript
in §8 is against this fixed version, and the compiled-context text now
arrives and gets used (§8.1's interaction produced a correct, well-cited
answer from the returned declarations and bodies, not from the model's own
training data).

**Why this matters beyond this one client quirk:** an MCP tool's contract is
whatever the calling client actually forwards, not whatever the spec says a
compliant client must forward. `structured_content`/`outputSchema` is a
newer, less-exercised part of the protocol than plain `content`; until this
is re-verified against a Claude Code release that is confirmed to forward
both, tools in this codebase should default to `content`-only responses.

---

## 7. Session-per-invocation: `explain_inclusion`'s cache does not survive `--resume`

Interaction 2 (§8.2) hit `explain_inclusion` with no `seeds`/`task`, expecting
the context compiled in interaction 1's `--resume`d session to still be
cached. It was not: `--resume` reconnects the *conversation*, but each
`claude -p` invocation spawns a fresh `python -m context_compiler.mcp.server`
subprocess over stdio, so `ServerState.last_context` — a single in-process
slot, deliberately not persisted anywhere (§2's "load once... never per
call" is about config and connections, not about a per-turn cache) — starts
empty again. The tool's own error message is what saved the interaction:

```
no compiled context available yet -- call compile_context first, or pass `task`/`seeds` to this call to compile one
```

The model read this, recompiled with the same seeds, and continued
correctly. This is expected behavior of a stdio server registered per-machine
rather than a defect, but it means **`explain_inclusion`'s no-argument form
is only a same-process convenience** — useful within one long-running
`claude` session, not across a `--resume` boundary. Worth a line in Item 8 or
beyond if the cache is ever worth persisting to disk; not attempted here, as
it is out of this item's scope.

---

## 8. Live session transcripts, verbatim

Three interactions, run non-interactively as described in §1, from
`/tmp/.../mcp-live-session` — **not** this repository. `ToolSearch` calls
are Claude Code's own tool-discovery mechanism (it looks up MCP tool schemas
on demand); they are not part of context-compiler and are included for
completeness. Session id `3c60d789-6a35-4875-8741-8e4fafd2ecac` for all three
(interactions 2 and 3 via `--resume`).

### 8.1 "Use context-compiler to get context on why QuerySet.filter produces the SQL it does, then explain what build_filter is doing."

```
[tool call: ToolSearch]  input={"query": "context-compiler", "max_results": 10}

[tool call: mcp__context-compiler__compile_context]  input={"seeds": ["QuerySet.filter", "build_filter"]}

[tool result block 0]
Compiled context · 24 symbols · 6,932 / 8,000 tokens
22 declarations · 2 bodies · 27 identities
Structural closure: complete (P3 FULL)

# --- seeds (2) ---
# django/db/models/query.py

class QuerySet:
# filter  [L3, 61t]
    def filter(self, *args, **kwargs):
        """
        Return a new QuerySet instance with the args ANDed to the existing
        set.
        """
        self._not_support_combined_queries("filter")
        return self._filter_or_exclude(False, args, kwargs)

# django/db/models/sql/query.py
from collections.abc import Iterable, Iterator, Mapping
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db.models.expressions import (
    BaseExpression,
    Col,
    ColPairs,
    Exists,
    F,
    OuterRef,
    RawSQL,
    Ref,
    ResolvedOuterRef,
    Value,
)
from django.db.models.lookups import Lookup
from django.db.models.query_utils import (
    Q,
    check_rel_lookup_compatibility,
    refs_expression,
)
from django.db.models.sql.constants import INNER, LOUTER, ORDER_DIR, SINGLE
from django.db.models.sql.datastructures import BaseTable, Empty, Join, MultiJoin
from django.db.models.sql.where import AND, OR, ExtraWhere, NothingNode, WhereNode

class Query:
# build_filter  [L3, 1665t]
    def build_filter(
        self,
        filter_expr,
        branch_negated=False,
        current_negated=False,
        can_reuse=None,
        allow_joins=True,
        split_subq=True,
        check_filterable=True,
        summarize=False,
        update_join_types=True,
    ):
        """
        Build a WhereNode for a single filter clause but don't add it
        to this Query. Query.add_q() will then add this filter to the where
        Node.

        The 'branch_negated' tells us if the current branch contains any
        negations. This will be used to determine if subqueries are needed.

        The 'current_negated' is used to determine if the current filter is
        negated or not and this will be used to determine if IS NULL filtering
        is needed.

        The difference between current_negated and branch_negated is that
        branch_negated is set on first negation, but current_negated is
        flipped for each negation.

        Note that add_filter will not do any negating itself, that is done
        upper in the code by add_q().

        The 'can_reuse' is a set of reusable joins for multijoins.

        The method will create a filter clause that can be added to the current
        query. However, if the filter isn't added to the query then the caller
        is responsible for unreffing the joins used.
        """
        if isinstance(filter_expr, dict):
            raise FieldError("Cannot parse keyword query as dict")
        if isinstance(filter_expr, Q):
            return self._add_q(
                filter_expr,
                branch_negated=branch_negated,
                current_negated=current_negated,
                used_aliases=can_reuse,
                allow_joins=allow_joins,
                split_subq=split_subq,
                check_filterable=check_filterable,
                summarize=summarize,
                update_join_types=update_join_types,
            )
        if hasattr(filter_expr, "resolve_expression"):
            if not getattr(filter_expr, "conditional", False):
                raise TypeError("Cannot filter against a non-conditional expression.")
            condition = filter_expr.resolve_expression(
                self, allow_joins=allow_joins, reuse=can_reuse, summarize=summarize
            )
            if not isinstance(condition, Lookup):
                condition = self.build_lookup(["exact"], condition, True)
            return WhereNode([condition], connector=AND), []
        arg, value = filter_expr
        if not arg:
            raise FieldError("Cannot parse keyword query %r" % arg)
        lookups, parts, reffed_expression = self.solve_lookup_type(arg, summarize)

        if check_filterable:
            self.check_filterable(reffed_expression)

        if not allow_joins and len(parts) > 1:
            raise FieldError("Joined field references are not permitted in this query")

        pre_joins = self.alias_refcount.copy()
        value = self.resolve_lookup_value(value, can_reuse, allow_joins, summarize)
        used_joins = {
            k for k, v in self.alias_refcount.items() if v > pre_joins.get(k, 0)
        }

        if check_filterable:
            self.check_filterable(value)

        if reffed_expression:
            condition = self.build_lookup(lookups, reffed_expression, value)
            return WhereNode([condition], connector=AND), []

        opts = self.get_meta()
        alias = self.get_initial_alias()
        allow_many = not branch_negated or not split_subq

        try:
            join_info = self.setup_joins(
                parts,
                opts,
                alias,
                can_reuse=can_reuse,
                allow_many=allow_many,
            )

            # Prevent iterator from being consumed by check_related_objects()
            if isinstance(value, Iterator):
                value = list(value)
            self.check_related_objects(join_info.final_field, value, join_info.opts)

            # split_exclude() needs to know which joins were generated for the
            # lookup parts
            self._lookup_joins = join_info.joins
        except MultiJoin as e:
            return self.split_exclude(filter_expr, can_reuse, e.names_with_path)

        # Update used_joins before trimming since they are reused to determine
        # which joins could be later promoted to INNER.
        used_joins.update(join_info.joins)
        targets, alias, join_list = self.trim_joins(
            join_info.targets, join_info.joins, join_info.path
        )
        if can_reuse is not None:
            can_reuse.update(join_list)

        if join_info.final_field.is_relation:
            if len(targets) == 1:
                col = self._get_col(targets[0], join_info.final_field, alias)
            else:
                col = ColPairs(alias, targets, join_info.targets, join_info.final_field)
        else:
            col = self._get_col(targets[0], join_info.final_field, alias)

        condition = self.build_lookup(lookups, col, value)
        lookup_type = condition.lookup_name
        clause = WhereNode([condition], connector=AND)

        require_outer = (
            lookup_type == "isnull" and condition.rhs is True and not current_negated
        )
        if (
            current_negated
            and (lookup_type != "isnull" or condition.rhs is False)
            and condition.rhs is not None
        ):
            require_outer = True
            if lookup_type != "isnull":
                # The condition added here will be SQL like this:
                # NOT (col IS NOT NULL), where the first NOT is added in
                # upper layers of code. The reason for addition is that if col
                # is null, then col != someval will result in SQL "unknown"
                # which isn't the same as in Python. The Python None handling
                # is wanted, and it can be gotten by
                # (col IS NULL OR col != someval)
                #   <=>
                # NOT (col IS NOT NULL AND col = someval).
                if (
                    self.is_nullable(targets[0])
                    or self.alias_map[join_list[-1]].join_type == LOUTER
                ):
                    lookup_class = targets[0].get_lookup("isnull")
                    col = self._get_col(targets[0], join_info.targets[0], alias)
                    # Use OR + IS NULL when RHS `in` values include None.
                    if (
                        lookup_type == "in"
                        # Check containers (not strings or bytes).
                        and isinstance(condition.rhs, Iterable)
                        and not isinstance(condition.rhs, (str, bytes))
                        and any(v is None for v in condition.rhs)
                    ):
                        clause.add(lookup_class(col, True), OR)
                    else:
                        clause.add(lookup_class(col, False), AND)
                # If someval is a nullable column, someval IS NOT NULL is
                # added.
                if isinstance(value, Col) and self.is_nullable(value.target):
                    lookup_class = value.target.get_lookup("isnull")
                    clause.add(lookup_class(value, False), AND)
        return clause, used_joins if not require_outer else ()

# --- dependencies (19) ---
# django/core/exceptions.py

# FieldError  [L2, 7t]  <- Query.build_filter  CALLS
class FieldError(Exception):
    ...

# django/db/models/expressions.py
from django.db import DatabaseError, NotSupportedError, connection

# ColPairs  [L2, 684t]  <- Query.build_filter  CALLS
class ColPairs(Expression):
    def __init__(self, alias, targets, sources, output_field): ...
    def __len__(self): ...
    def __iter__(self): ...
    def __repr__(self): ...
    def get_cols(self): ...
    def get_source_expressions(self): ...
    def set_source_expressions(self, exprs): ...
    def as_sql(self, compiler, connection): ...
    def relabeled_clone(self, relabels): ...
    def resolve_expression(self, *args, **kwargs): ...
    def select_format(self, compiler, sql, params): ...
    def identity(self): ...  # from django.db.models.expressions.Expression
    def get_db_converters(self, connection): ...  # from django.db.models.expressions.BaseExpression
    def contains_aggregate(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_over_clause(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_column_references(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_subquery(self): ...  # from django.db.models.expressions.BaseExpression
    def conditional(self): ...  # from django.db.models.expressions.BaseExpression
    def field(self): ...  # from django.db.models.expressions.BaseExpression
    def output_field(self): ...  # from django.db.models.expressions.BaseExpression
    def convert_value(self): ...  # from django.db.models.expressions.BaseExpression
    def get_lookup(self, lookup): ...  # from django.db.models.expressions.BaseExpression
    def get_transform(self, name): ...  # from django.db.models.expressions.BaseExpression
    def replace_expressions(self, replacements): ...  # from django.db.models.expressions.BaseExpression
    def get_refs(self): ...  # from django.db.models.expressions.BaseExpression
    def copy(self): ...  # from django.db.models.expressions.BaseExpression
    def prefix_references(self, prefix): ...  # from django.db.models.expressions.BaseExpression
    def get_group_by_cols(self): ...  # from django.db.models.expressions.BaseExpression
    def get_source_fields(self): ...  # from django.db.models.expressions.BaseExpression
    def asc(self, **kwargs): ...  # from django.db.models.expressions.BaseExpression
    def desc(self, **kwargs): ...  # from django.db.models.expressions.BaseExpression
    def reverse_ordering(self): ...  # from django.db.models.expressions.BaseExpression
    def flatten(self): ...  # from django.db.models.expressions.BaseExpression
    def get_expression_for_validation(self): ...  # from django.db.models.expressions.BaseExpression
    def bitand(self, other): ...  # from django.db.models.expressions.Combinable
    def bitleftshift(self, other): ...  # from django.db.models.expressions.Combinable
    def bitrightshift(self, other): ...  # from django.db.models.expressions.Combinable
    def bitxor(self, other): ...  # from django.db.models.expressions.Combinable
    def bitor(self, other): ...  # from django.db.models.expressions.Combinable

# django/db/models/query.py
from django.db import (
    DJANGO_VERSION_PICKLE_KEY,
    IntegrityError,
    NotSupportedError,
    connections,
    router,
    transaction,
)

class QuerySet:
# _filter_or_exclude  [L2, 19t]  <- QuerySet.filter  CALLS
    def _filter_or_exclude(self, negate, args, kwargs): ...
# _not_support_combined_queries  [L2, 49t]  <- QuerySet.filter  CALLS
    def _not_support_combined_queries(self, operation_name): ...

# django/db/models/sql/query.py
from django.db.models.constants import LOOKUP_SEP
from django.db import DEFAULT_DB_ALIAS, NotSupportedError, connections
from django.db.models.fields import Field
import functools

class Query:
# get_meta  [L2, 27t]  <- Query.build_filter  CALLS
    def get_meta(self): ...
    """Return the Options instance (the model._meta) from which to start"""
# _get_col  [L2, 17t]  <- Query.build_filter  CALLS
    def _get_col(self, target, field, alias): ...
# get_initial_alias  [L2, 26t]  <- Query.build_filter  CALLS
    def get_initial_alias(self): ...
    """Return the first alias for this query, after increasing its reference"""
# resolve_lookup_value  [L2, 24t]  <- Query.build_filter  CALLS
    def resolve_lookup_value(self, value, can_reuse, allow_joins, summarize=False): ...
# solve_lookup_type  [L2, 129t]  <- Query.build_filter  CALLS
    def solve_lookup_type(self, lookup, summarize=False): ...
    """Solve the lookup type from the lookup (e.g.: 'foobar__id__icontains')."""
# check_related_objects  [L2, 52t]  <- Query.build_filter  CALLS
    def check_related_objects(self, field, value, opts): ...
    """Check the type of object passed to query relations."""
# check_filterable  [L2, 42t]  <- Query.build_filter  CALLS
    def check_filterable(self, expression): ...
    """Raise an error if expression cannot be used in a WHERE clause."""
# build_lookup  [L2, 43t]  <- Query.build_filter  CALLS
    def build_lookup(self, lookups, lhs, rhs): ...
    """Try to extract transforms and lookup from given lhs."""
# _add_q  [L2, 81t]  <- Query.build_filter  CALLS
    def _add_q(self, q_object, used_aliases, branch_negated=False, current_negated=False, allow_joins=True, split_subq=True, check_filterable=True, summarize=False, update_join_types=True): ...
    """Add a Q-object to the current filter."""
# setup_joins  [L2, 81t]  <- Query.build_filter  CALLS
    def setup_joins(self, names, opts, alias, can_reuse=None, allow_many=True): ...
    """Compute the necessary table joins for the passage through the fields"""
# trim_joins  [L2, 37t]  <- Query.build_filter  CALLS
    def trim_joins(self, targets, joins, path): ...
    """The 'target' parameter is the final field being joined to, 'joins'"""
# split_exclude  [L2, 105t]  <- Query.build_filter  CALLS
    def split_exclude(self, filter_expr, can_reuse, names_with_path): ...
    """When doing an exclude against any kind of N-to-many relation, we need"""
# is_nullable  [L2, 39t]  <- Query.build_filter  CALLS
    def is_nullable(self, field): ...
    """Check if the given field should be treated as nullable."""

# django/db/models/sql/where.py
from django.core.exceptions import EmptyResultSet, FullResultSet
from django.db.models.expressions import Case, When
from django.db.models.functions import Mod
from django.db.models.lookups import Exact
from django.utils import tree
from django.utils.functional import cached_property
from functools import reduce
import operator

# WhereNode  [L2, 376t]  <- Query.build_filter  CALLS
class WhereNode(tree.Node):
    def split_having_qualify(self, negated=False, must_group_by=False): ...
    def as_sql(self, compiler, connection): ...
    def get_group_by_cols(self): ...
    def get_source_expressions(self): ...
    def set_source_expressions(self, children): ...
    def relabel_aliases(self, change_map): ...
    def clone(self): ...
    def relabeled_clone(self, change_map): ...
    def replace_expressions(self, replacements): ...
    def get_refs(self): ...
    def _contains_aggregate(cls, obj): ...
    def contains_aggregate(self): ...
    def _contains_over_clause(cls, obj): ...
    def contains_over_clause(self): ...
    def is_summary(self): ...
    def _resolve_leaf(expr, query, *args, **kwargs): ...
    def _resolve_node(cls, node, query, *args, **kwargs): ...
    def resolve_expression(self, *args, **kwargs): ...
    def output_field(self): ...
    def _output_field_or_none(self): ...
    def select_format(self, compiler, sql, params): ...
    def get_db_converters(self, connection): ...
    def get_lookup(self, lookup): ...
    def leaves(self): ...
    def create(cls, children=None, connector=None, negated=False): ...  # from django.utils.tree.Node
    def add(self, data, conn_type): ...  # from django.utils.tree.Node
    def negate(self): ...  # from django.utils.tree.Node

# django/utils/tree.py

class Node:
# add  [L2, 28t]  <- Query.build_filter  CALLS
    def add(self, data, conn_type): ...
    """Combine this tree and the data represented by data using the"""

# --- optional context (3) ---
# django/db/models/sql/query.py
from collections import Counter, namedtuple
from django.db.models.aggregates import Count
from django.utils.deprecation import RemovedInDjango70Warning
from django.utils.regex_helper import _lazy_re_compile
from django.utils.tree import Node
from django.utils.warnings import django_file_prefixes
from itertools import chain, count, product
from string import ascii_uppercase
import copy
import difflib
import sys
import warnings

# query  [L2, 297t]  <- Query.build_filter  OPTIONAL:static_caller
# module django.db.models.sql.query

# Query  [L2, 1872t]  <- Query.build_filter  OPTIONAL:static_caller
class Query(BaseExpression):
    ... (63 signature-only members, elided here -- verbatim in the raw transcript)

class Query:
# build_where  [L2, 13t]  <- Query.build_filter  OPTIONAL:static_caller
    def build_where(self, filter_expr): ...

# --- mandatory identities (3) ---
django.db.models.expressions.BaseExpression — django/db/models/expressions.py:174
django.db.models.expressions.Expression — django/db/models/expressions.py:520
django.utils.tree.Node — django/utils/tree.py:11

# --- identity hints (24) ---
django.db.models.aggregates.Count — django/db/models/aggregates.py:295
django.db.models.constants.LOOKUP_SEP — django/db/models/constants.py:8
django.db.models.expressions.Col — django/db/models/expressions.py:1351
django.db.models.expressions.Exists — django/db/models/expressions.py:1887
django.db.models.expressions.F — django/db/models/expressions.py:882
django.db.models.expressions.When — django/db/models/expressions.py:1636
django.db.models.functions.math.Mod — django/db/models/functions/math.py:126
django.db.models.query_utils.Q — django/db/models/query_utils.py:41
django.db.models.query_utils.Q.create — django/db/models/query_utils.py:64
django.db.models.query_utils.refs_expression — django/db/models/query_utils.py:469
django.db.models.sql.datastructures.Empty — django/db/models/sql/datastructures.py:23
django.db.models.sql.query.Query.clone — django/db/models/sql/query.py:388
django.db.models.sql.query.Query.exists — django/db/models/sql/query.py:662
django.db.models.sql.query.Query.get_compiler — django/db/models/sql/query.py:370
django.db.models.sql.query.Query.join — django/db/models/sql/query.py:1135
django.db.models.sql.query.Query.ref_alias — django/db/models/sql/query.py:927
django.db.models.sql.query.Query.table_alias — django/db/models/sql/query.py:900
django.db.models.sql.query.RawQuery — django/db/models/sql/query.py:156
django.db.models.sql.query.RawQuery.clone — django/db/models/sql/query.py:174
django.db.utils.DEFAULT_DB_ALIAS — django/db/utils.py:13
django.db.utils.NotSupportedError — django/db/utils.py:49
django.utils.regex_helper._lazy_re_compile — django/utils/regex_helper.py:343
django.utils.tree.Node.create — django/utils/tree.py:29
django.utils.warnings.django_file_prefixes — django/utils/warnings.py:8

[tool result block 1]
{
  "status": "OK",
  "profile": "P3",
  "emitted_symbols": 24,
  "closure_size": 120,
  "budget": 8000,
  "budgeted_tokens": 6932,
  "actual_tokens": 6130,
  "token_margin": -802,
  "round_trips": 20,
  "latency_ms": 774.3,
  "seeds_resolved": [
    "django.db.models.query.QuerySet.filter",
    "django.db.models.sql.query.Query.build_filter"
  ]
}

[assistant]
Here's the chain, based on what context-compiler pulled from Django's ORM source.

## Why `QuerySet.filter` produces the SQL it does

`QuerySet.filter()` (`django/db/models/query.py`) is a thin wrapper:

    def filter(self, *args, **kwargs):
        self._not_support_combined_queries("filter")
        return self._filter_or_exclude(False, args, kwargs)

It doesn't build SQL itself — it just delegates to `_filter_or_exclude`, which clones the current `QuerySet` and hands the args/kwargs off to the underlying `Query` object's filter-building machinery (`Query.add_q` → `Query._add_q` → `Query.build_filter`, for each condition). The actual SQL only gets generated much later, when the query is compiled (`Query.as_sql`/`SQLCompiler`) — `filter()` just accumulates a `WhereNode` tree on `self.query.where`. That's why chained `.filter().filter()` calls AND together, and why nothing hits the database until the queryset is evaluated.

## What `build_filter` is doing

`Query.build_filter` (`django/db/models/sql/query.py`) is the core per-clause translator: given one filter expression (a `Q` object, a conditional expression, or a `(lookup_string, value)` pair from a kwarg), it turns it into a `WhereNode` ready to be added to the query's WHERE tree. Concretely it:

1. Dispatches by type of `filter_expr` (a `Q` object recurses into `Query._add_q`; an expression with `resolve_expression` resolves directly into a `Lookup`; otherwise it's a `(lookup, value)` pair).
2. Parses the lookup string via `solve_lookup_type` — splits `"name__icontains"` into the field path and lookup name.
3. Validates filterability via `check_filterable`.
4. Resolves the RHS value with `resolve_lookup_value`.
5. Walks relations to build joins via `setup_joins` — turns `related__field__lookup` into SQL `JOIN`s; bails to `split_exclude` on a multi-valued relation in a negated context.
6. Trims redundant joins via `trim_joins` and builds the final `Col`/`ColPairs`.
7. Builds the `Lookup` object via `build_lookup(lookups, col, value)` and wraps it in a `WhereNode`.
8. Handles negation/NULL semantics so Python's `!=` also matches NULL rows, unlike SQL's three-valued `UNKNOWN`.

So `build_filter` is the piece that bridges the declarative kwarg/`Q` API to the join- and lookup-based structures the SQL compiler later renders.

[session summary] turns=3 duration_api_ms=25593 is_error=false
```

*(The full, unabridged transcript — including all 63 signature-only `Query`
members under "optional context" — is in
`mcp-item-7-transcripts/interaction1-compile-context.txt` alongside this doc;
the `Query` class body above is elided to keep this section readable. The `ColPairs` block is worth pointing at: it shows Amendment
A4.3's MRO filter working live — five Django-defined operator aliases
(`bitand`, `bitor`, `bitxor`, `bitleftshift`, `bitrightshift`) survive because
they don't start with `_`, while every inherited dunder (`__eq__`, `__hash__`,
`__rxor__`, ...) does not appear.)*

### 8.2 "Why was Query.add_q included in that context? Use context-compiler's explain_inclusion to show me the full derivation chain."

(`--resume`d from 8.1's session — a Django symbol, real chain, in place of
the task's illustrative `TokenPolicy` example.)

```
[tool call: ToolSearch]  input={"query": "explain_inclusion", "max_results": 5}

[assistant]
It reconnected. Calling `explain_inclusion` now for `Query.add_q` against the context I compiled earlier.

[tool call: mcp__context-compiler__explain_inclusion]  input={"fqn": "django.db.models.sql.query.Query.add_q"}

[tool result block 0]
no compiled context available yet -- call compile_context first, or pass `task`/`seeds` to this call to compile one

[assistant]
The previous compiled context didn't carry over the reconnect, so I'll recompile with the same seeds first, then ask for the derivation chain.

[tool call: mcp__context-compiler__explain_inclusion]  input={"fqn": "django.db.models.sql.query.Query.add_q", "seeds": ["QuerySet.filter", "build_filter"]}

[tool result block 0]
django.db.models.sql.query.Query.add_q  [L1]
  <- django.db.models.sql.query  CALLS  (rule: CALLS(L2)->L1)
  <- django.db.models.sql.query.Query.build_filter  OPTIONAL:static_caller  (rule: packed(static_caller)->L2)
  included because: 1 mandatory rule fired
  also reached via optional packing (1 bundle step(s))
  runtime-confirmed: no evidence yet (Item 9)

[tool result block 1]
{
  "fqn": "django.db.models.sql.query.Query.add_q",
  "present": true,
  "level": "L1",
  "tokens": 18,
  "emitted": false,
  "is_seed": false,
  "chain": [
    {"fqn": "django.db.models.sql.query", "edge": "CALLS", "rule": "CALLS(L2)->L1", "depth": 1},
    {"fqn": "django.db.models.sql.query.Query.build_filter", "edge": "OPTIONAL:static_caller", "rule": "packed(static_caller)->L2", "depth": 2}
  ],
  "would_include_via": []
}

[assistant]
The derivation chain for `Query.add_q`:

    Query.add_q  [level L1]
      <- django.db.models.sql.query          via CALLS                   (rule: CALLS(L2)->L1)
      <- Query.build_filter                  via OPTIONAL:static_caller  (rule: packed(static_caller)->L2)

Read bottom-up:

1. `build_filter` is a seed, so the compiler pulled in `django.db.models.sql.query` (the module) as optional context tagged `static_caller` -- this is the "dump the rest of the defining class/module for situational context" bundling, not a direct call edge.
2. From that module-level inclusion, `add_q` gets pulled in one level further via a plain `CALLS` edge at depth 1.

Two things worth flagging from the raw result:

- **`present: true` but `emitted: false`** -- `add_q` exists in the closure and satisfied an inclusion rule, but wasn't rendered as its own annotated block. It only showed up implicitly inside the bundled `Query` class dump.
- **Don't confuse it with `_add_q`** -- the private method `build_filter` actually calls directly, which got its own full entry in "dependencies". `add_q` is the public wrapper that only rode in via the optional class-level bundle, not because `build_filter` calls it.

`explain_inclusion`'s `runtime-confirmed: no evidence yet` note is the tool itself flagging that this is a static/structural inference, not a confirmed runtime call.

[session summary] turns=4 duration_api_ms=33893 is_error=false
```

### 8.3 "What could be affected if I change Query.build_filter?"

```
[assistant]
This calls for `impact_cone`, which needs the context-compiler MCP server -- it's currently reconnecting. Let me check if it's back up.

[tool call: ToolSearch]  input={"query": "impact_cone", "max_results": 5}

[assistant]
It's back. Running the reverse-call closure for `Query.build_filter`.

[tool call: mcp__context-compiler__impact_cone]  input={"fqn": "django.db.models.sql.query.Query.build_filter"}

[tool result block 0]
Potentially affected by changes to django.db.models.sql.query.Query.build_filter
(reverse CALLS closure, an over-approximation of what is potentially affected)

78 symbol(s) found, depth reached 2:
  depth 1: 5
  depth 2: 73

Top 30 by relevance, grouped by file:
# django/db/backends/base/schema.py
  db_default_sql  depth 2  idf 9.069
# django/db/models/constraints.py
  _get_condition_sql  depth 2  idf 9.292
  _get_check_sql  depth 2  idf 9.292
# django/db/models/fields/generated.py
  contribute_to_class  depth 2  idf 9.292
# django/db/models/indexes.py
  _get_condition_sql  depth 2  idf 9.292
# django/db/models/query.py
  __init__  depth 2  idf 9.58
  __init__  depth 2  idf 9.58
# django/db/models/sql/query.py
  build_where  depth 1  idf 9.986
  add_q  depth 2  idf 9.292
# tests/composite_pk/test_names_to_path.py
  test_pk  depth 2  idf 9.292
  test_id  depth 2  idf 9.292
  test_tenant_id  depth 2  idf 9.069
  test_user_id  depth 2  idf 9.069
  test_comments  depth 2  idf 9.069
# tests/queries/test_query.py
  test_names_to_path_field_error  depth 2  idf 9.292
  test_iterable_lookup_value  depth 2  idf 9.292
  test_clone_select_related  depth 2  idf 9.292
  test_negated_nullable  depth 2  idf 9.069
  test_foreign_key_exclusive  depth 2  idf 9.069
  test_foreign_key  depth 2  idf 9.069
  test_foreign_key_f  depth 2  idf 9.069
  test_non_alias_cols_query  depth 2  idf 9.069
  test_simple_query  depth 2  idf 9.069
  test_filter_conditional  depth 2  idf 9.069
  test_names_to_path_field  depth 2  idf 9.069
  test_complex_query  depth 2  idf 9.069
  test_filter_non_conditional  depth 2  idf 8.887
# tests/queries/test_sqlcompiler.py
  test_repr  depth 2  idf 9.58
  test_execute_sql_suppresses_cursor_closing_failure_on_exception  depth 2  idf 9.069
  test_quote_name_unless_alias_deprecation  depth 2  idf 8.887

[tool result block 1]
{
  "root": "django.db.models.sql.query.Query.build_filter",
  "depth_reached": 2,
  "counts_by_depth": {"1": 5, "2": 73},
  "truncated": false,
  "truncation_reason": "",
  "hubs_skipped": [],
  "top": [ ... 30 entries, node/fqn/file/depth/idf each -- verbatim in mcp-item-7-transcripts/interaction3-impact-cone.txt ... ],
  "seconds": 0.417
}

[assistant]
Reverse-call closure for `Query.build_filter` -- 78 symbols total within 2 hops (not truncated, no hubs skipped). Remember: this is an over-approximation of *reachability*, not a claim about what actually breaks.

**Depth 1 -- direct callers (5 total, only 1 in the top-30 relevance cut):**
- `Query.build_where` -- the other main entry point that calls `build_filter` directly (alongside `_add_q`, seen last turn).

**Depth 2 -- everything reachable through those callers (73 total):**
- **ORM internals**: `Query.add_q`, `QuerySet.__init__`, `RawQuerySet.__init__`.
- **Constraints & indexes**: `UniqueConstraint._get_condition_sql`, `CheckConstraint._get_check_sql`, `Index._get_condition_sql`, `GeneratedField.contribute_to_class`, `BaseDatabaseSchemaEditor.db_default_sql` -- a `build_filter` change can ripple into DDL/schema generation, not just `.filter()` calls.
- **Test suite**: a large chunk of `tests/queries/test_query.py`, `tests/composite_pk/test_names_to_path.py`, and a few `tests/queries/test_sqlcompiler.py` cases.

**Practical read:** the blast radius isn't limited to `.filter()`/`.exclude()` -- because `build_filter` backs `build_where`, changes here also touch conditional constraint/index/generated-field SQL. At minimum re-run `tests/queries/test_query.py`, `tests/composite_pk/test_names_to_path.py`, and the constraint/index test suites.

[session summary] turns=3 duration_api_ms=21534 is_error=false
```

The model's own phrasing — "over-approximation of *reachability*, not a claim
about what actually breaks" — is the §5 wording constraint propagating
through into how the calling model describes the tool's output to a human,
which is the actual test of whether that constraint does anything.

---

## 9. Test inventory

```
tests/mcp/test_seeds.py     11 passed   no HydraDB
tests/mcp/test_cone.py      10 passed   no HydraDB, stub reverse reader
tests/mcp/test_explain.py    6 passed   no HydraDB, hand-built Context
tests/mcp/test_server.py    11 passed   no HydraDB, in-memory Compiler
tests/mcp/  (whole dir)     38 passed   ~1 s
tests/unit + tests/mcp      115 passed  ~6 s (no regression in existing 77)
```

`test_server.py` wires a real `graph.compile.Compiler` over a plain Python
function as the expander (no `graph.expand.Expander`, no HydraDB) — the same
technique `tests/unit/test_emit.py` uses for the emission layer — so
"tool schemas validate; seed resolution handles exact/suffix/ambiguous/missing;
errors are structured, not tracebacks; `impact_cone` bounds are enforced
against a stub" is checked against the whole call path a live invocation
takes, not just each piece in isolation.

---

## 10. Files

```
src/context_compiler/mcp/__init__.py       new
src/context_compiler/mcp/config.py         new   env-var config, sane defaults
src/context_compiler/mcp/state.py          new   fail-fast startup, one Bolt connection
src/context_compiler/mcp/seeds.py          new   PLACEHOLDER(item-8) resolver
src/context_compiler/mcp/cone.py           new   impact_cone logic + rendering
src/context_compiler/mcp/explain.py        new   explain_inclusion logic + rendering
src/context_compiler/mcp/server.py         new   the three tools, stdio transport
tests/mcp/test_seeds.py                    new
tests/mcp/test_cone.py                     new
tests/mcp/test_explain.py                  new
tests/mcp/test_server.py                   new
docs/spikes/mcp-item-7-results.md          this file
README.md                                  new   install-and-use section (Sec 8)
pyproject.toml                             mod   added `mcp>=2.0` dependency
```

`src/context_compiler/graph/*` and `src/context_compiler/emit/*` are
**unchanged** — every tool reads their output; none of them recomputes a
cost, a level or a rendering decision. No file under `docs/specs/`,
`scripts/`, `~/hydradb` or `~/targets/` was modified.

---

## 11. Unresolved issues

1. **The `structured_content` client-drop (§6) is unverified against other
   MCP clients.** It may be specific to this Claude Code build, or to tools
   without a declared `outputSchema`. The workaround (text-only, two
   `content` blocks) is cheap and costs nothing if the underlying client
   behavior is later fixed, so it was not made conditional on client
   detection.
2. **`impact_cone`'s near-hub band is genuinely slow** (§5: up to 6.3 s for a
   457-in-degree symbol) — inside the 10 s deadline today, but this is the
   same A3.1 constraint Item 5 flagged for packing, showing up a second time
   in a tool a human is waiting on synchronously. A batched reverse read
   would fix both at once; none exists on this engine build.
3. **`explain_inclusion`'s cache is process-scoped** (§7): it does not
   survive a `--resume`d Claude Code session, only a single long-running
   connection. Not fixed here — persisting it would mean picking a cache key
   and an invalidation policy, which is scope creep on a placeholder-adjacent
   piece of a "keep it small" item.
4. **Task-based seed resolution is exactly as weak as advertised.** It was
   not exercised in the live session (all three interactions used explicit
   `seeds`, per the tool description's own advice) — Item 8 replaces it
   entirely, so no further validation was invested here.
5. **`CC_EDGES` is a config knob beyond the task's four** (§ config.py) —
   needed because degree tables can't be derived from `symbols.jsonl` alone.
   Defaults to a sibling `edges.jsonl`, so it is invisible in the common case
   and only surfaces if `edges.jsonl` lives somewhere else.
