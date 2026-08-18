# Item 10a -- Demo side-by-side: traceback seeds, compiler vs Arm B

**This artifact reports a much smaller contrast than the differentiator
statement in Task 3 might suggest for emitted symbol counts, but a large one on
the identity tier.** Emitted symbol counts, tokens and level composition are
close between the two arms on this causal-chain seed set, just as they were on
the weak random-seed example in
[`baseline-arm-b-example.md`](baseline-arm-b-example.md). The separation that
survives on *this* traceback is the same one that survived on the random-seed
trial: the compiler's closure names 24 identities the
model cannot afford to emit (2 mandatory +
22 hints) against Arm B's 2. That
number is stated plainly here rather than after the fact, per the instruction
to report the difference as measured.

## The traceback and resolved seeds

```text
Traceback (most recent call last):
  File "django/db/models/query.py", line 1682, in filter
  File "django/db/models/query.py", line 1699, in _filter_or_exclude
  File "django/db/models/sql/query.py", line 1510, in build_filter
```

Resolved through `resolve_task` (Item 8's traceback resolver), innermost frame
first. Both arms received this identical list; `assert set(seeds_a) ==
set(seeds_b)` ran in `scripts/demo_side_by_side.py` and passed.

```text
6734709972156213732  django.db.models.sql.query.Query.build_filter
8580647764743840513  django.db.models.query.QuerySet._filter_or_exclude
5938239568010518293  django.db.models.query.QuerySet.filter
```

## Selection rule (fixed before inspecting output)

This traceback is chosen because it is the canonical `QuerySet.filter` ->
SQL-construction path in Django and because A5/A6 already characterised its
neighbourhood in detail, so the graph structure around it is independently
documented. It is not chosen by comparing arm outputs.

## Comparison

Same seeds, same 8,000-token budget, same `cost()`, same emitter.

| measure | compiler | Arm B |
|---|---:|---:|
| status | OK | OK |
| emitted symbols | 26 | 27 |
| emitted tokens | 4,674 | 4,271 |
| budgeted tokens | 5,048 | 4,716 |
| utilisation | 0.6310 | 0.5895 |
| level composition (L3 / L2) | 3 / 23 | 3 / 24 |
| mandatory identities (L1, dangling) | 2 | 2 |
| identity hints (L1, budget-filled) | 22 | 0 |
| identities named but not emitted (total) | 24 | 2 |
| `is_closed()` | True | False |
| graph round trips | 21 | 24 |
| compile latency (ms) | 559.91 | 221.66 |

Repeated runs of `scripts/demo_side_by_side.py` (this run plus two prior
untracked runs) showed compile latency varying 559.91-2,788.89 ms for the
compiler and 221.66-411.94 ms for Arm B, while round trips stayed fixed at 21
and 24 respectively across every run. Latency is reported here as noisy wall
clock; round trips are the stable, reproducible figure and match the numbers
above.

## Named set differences

**Emitted-only** (in one arm's rendered blocks, not the other's):

- compiler emits 0 symbols Arm B does not: (none)
- Arm B emits 1 symbol(s) the compiler does not:

```text
tests.custom_managers.models.CustomQuerySet.filter
```

**Named-only** (emitted or referenced as an identity line in one arm, not the
other -- this is where the two arms actually differ):

Compiler names 22 symbols Arm B never mentions in
any form:

```text
django.db.models.constants.LOOKUP_SEP
django.db.models.expressions.Exists
django.db.models.expressions.OuterRef
django.db.models.expressions.Ref
django.db.models.expressions.ResolvedOuterRef
django.db.models.query.QuerySet._clone
django.db.models.query_utils.Q
django.db.models.query_utils.check_rel_lookup_compatibility
django.db.models.query_utils.refs_expression
django.db.models.sql.query.JoinPromoter
django.db.models.sql.query.Query.add_filter
django.db.models.sql.query.Query.add_q
django.db.models.sql.query.Query.bump_prefix
django.db.models.sql.query.Query.check_query_object_type
django.db.models.sql.query.Query.clear_ordering
django.db.models.sql.query.Query.join
django.db.models.sql.query.Query.names_to_path
django.db.models.sql.query.Query.ref_alias
django.db.models.sql.query.Query.trim_start
django.db.models.sql.query.Query.try_transform
django.db.models.sql.query.Query.unref_alias
django.db.utils.NotSupportedError
```

Arm B names 1 symbol(s) the compiler never mentions:

```text
tests.custom_managers.models.CustomQuerySet.filter
```

## Hop 2 from `build_filter`

Per A5/A6, hop 2 from `build_filter` reaches `WhereNode`, `ColPairs`,
`JoinPromoter`, `OuterRef`, `Exists`, `ResolvedOuterRef`. Two of the six are
reached by *both* arms' one-hop admission already -- that is a finding, not a
failure, since `build_filter` calls them directly. The other four are named by
the compiler's identity tier and not mentioned by Arm B at all:

| symbol | compiler | Arm B |
|---|---|---|
| `django.db.models.sql.where.WhereNode` | yes | yes |
| `django.db.models.expressions.ColPairs` | yes | yes |
| `django.db.models.sql.query.JoinPromoter` | named | no |
| `django.db.models.expressions.OuterRef` | named | no |
| `django.db.models.expressions.Exists` | named | no |
| `django.db.models.expressions.ResolvedOuterRef` | named | no |

"yes" means emitted as a declaration/body; "named" means referenced only as an
identity line; "no" means absent from the output in every form.

## The differentiator

**The compiler's closure named 24 identities
(2 mandatory + 22 hints) that Arm B's
one-hop ranking left entirely unmentioned; Arm B named only
2 identities of its own (2 mandatory
+ 0 hints) -- a 22-vs-0 hint
gap for a few hundred tokens -- while emitted symbol counts, tokens and level
composition stayed within a handful of each other.** This matches the weak
random-seed example's finding: closure buys the model knowledge of symbols it
cannot afford to emit, and undirected one-hop ranking does not, even on a seed
set chosen for causal structure rather than for a flattering contrast.

Note for the record: the dangling-reference metric (mandatory identities
alone, not counting hints) did **not** separate the arms in the 200-trial run
(median 4 vs 4, p90 9 vs 9, compiler worse in the tail at max 56 vs 20; see
`baseline-arm-b-results.md`). It is not decisive here either -- both arms show
exactly 2 mandatory identities. The identity-hint tier, which Arm B has no
mechanism for at all, is what separates the arms, not the dangling-reference
count.

## Why the random-seed and traceback cases are both kept

`baseline-arm-b-example.md`'s random six-seed trial is the corpus-representative
case: an unrelated admin/migration/SQL/template sample drawn the same way as
the 200-trial validation. This traceback case is the causal-structure case: a
single real call chain through Django's filter path. Both being present is
stronger than either alone -- the contrast between a corpus-representative
sample and a causal chain is itself the argument for why seed selection
matters, and both land on the same conclusion: the identity tier, not emitted
symbol count, is where closure shows up.

## Compiler output

```python
Compiled context · 26 symbols · 5,048 / 8,000 tokens
23 declarations · 3 bodies · 24 identities
Structural closure: complete (P3 FULL)

# --- seeds (3) ---
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
# _filter_or_exclude  [L3, 107t]
    def _filter_or_exclude(self, negate, args, kwargs):
        if (args or kwargs) and self.query.is_sliced:
            raise TypeError("Cannot filter a query once a slice has been taken.")
        clone = self._chain()
        if self._defer_next_filter:
            self._defer_next_filter = False
            clone._deferred_filter = negate, args, kwargs
        else:
            clone._filter_or_exclude_inplace(negate, args, kwargs)
        return clone

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

# --- dependencies (20) ---
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
from django.db.models.query_utils import PROHIBITED_FILTER_KWARGS, FilteredRelation, Q
from django.db import (
    DJANGO_VERSION_PICKLE_KEY,
    IntegrityError,
    NotSupportedError,
    connections,
    router,
    transaction,
)

class QuerySet:
# _filter_or_exclude_inplace  [L2, 43t]  <- QuerySet._filter_or_exclude  CALLS
    def _filter_or_exclude_inplace(self, negate, args, kwargs): ...
# _chain  [L2, 27t]  <- QuerySet._filter_or_exclude  CALLS
    def _chain(self): ...
    """Return a copy of the current QuerySet that's ready for another"""
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
# django/db/models/query.py

class QuerySet:
# exclude  [L2, 35t]  <- QuerySet._filter_or_exclude  OPTIONAL:static_caller
    def exclude(self, *args, **kwargs): ...
    """Return a new QuerySet instance with NOT (args) ANDed to the existing"""
# complex_filter  [L2, 52t]  <- QuerySet._filter_or_exclude  OPTIONAL:static_caller
    def complex_filter(self, filter_obj): ...
    """Return a new QuerySet instance with filter_obj added to the filters."""

# django/db/models/sql/query.py

class Query:
# build_where  [L2, 13t]  <- Query.build_filter  OPTIONAL:static_caller
    def build_where(self, filter_expr): ...

# --- mandatory identities (2) ---
django.db.models.expressions.Expression — django/db/models/expressions.py:520
django.utils.tree.Node — django/utils/tree.py:11

# --- identity hints (22) ---
django.db.models.constants.LOOKUP_SEP — django/db/models/constants.py:8
django.db.models.expressions.Exists — django/db/models/expressions.py:1887
django.db.models.expressions.OuterRef — django/db/models/expressions.py:983
django.db.models.expressions.Ref — django/db/models/expressions.py:1447
django.db.models.expressions.ResolvedOuterRef — django/db/models/expressions.py:946
django.db.models.query.QuerySet._clone — django/db/models/query.py:2262
django.db.models.query_utils.Q — django/db/models/query_utils.py:41
django.db.models.query_utils.check_rel_lookup_compatibility — django/db/models/query_utils.py:482
django.db.models.query_utils.refs_expression — django/db/models/query_utils.py:469
django.db.models.sql.query.JoinPromoter — django/db/models/sql/query.py:2809
django.db.models.sql.query.Query.add_filter — django/db/models/sql/query.py:1660
django.db.models.sql.query.Query.add_q — django/db/models/sql/query.py:1663
django.db.models.sql.query.Query.bump_prefix — django/db/models/sql/query.py:1058
django.db.models.sql.query.Query.check_query_object_type — django/db/models/sql/query.py:1367
django.db.models.sql.query.Query.clear_ordering — django/db/models/sql/query.py:2391
django.db.models.sql.query.Query.join — django/db/models/sql/query.py:1135
django.db.models.sql.query.Query.names_to_path — django/db/models/sql/query.py:1775
django.db.models.sql.query.Query.ref_alias — django/db/models/sql/query.py:927
django.db.models.sql.query.Query.trim_start — django/db/models/sql/query.py:2697
django.db.models.sql.query.Query.try_transform — django/db/models/sql/query.py:1461
django.db.models.sql.query.Query.unref_alias — django/db/models/sql/query.py:931
django.db.utils.NotSupportedError — django/db/utils.py:49
```

## Arm B output

```python
Compiled context · 27 symbols · 4,716 / 8,000 tokens
24 declarations · 3 bodies · 2 identities
Structural closure: complete (ARM_B GRAPH TOP-K, NO CLOSURE)

# --- seeds (3) ---
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
# _filter_or_exclude  [L3, 107t]
    def _filter_or_exclude(self, negate, args, kwargs):
        if (args or kwargs) and self.query.is_sliced:
            raise TypeError("Cannot filter a query once a slice has been taken.")
        clone = self._chain()
        if self._defer_next_filter:
            self._defer_next_filter = False
            clone._deferred_filter = negate, args, kwargs
        else:
            clone._filter_or_exclude_inplace(negate, args, kwargs)
        return clone

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

# --- dependencies (24) ---
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
from django.db.models.query_utils import PROHIBITED_FILTER_KWARGS, FilteredRelation, Q
from django.db import (
    DJANGO_VERSION_PICKLE_KEY,
    IntegrityError,
    NotSupportedError,
    connections,
    router,
    transaction,
)

class QuerySet:
# exclude  [L2, 35t]  <- QuerySet._filter_or_exclude  CALLS
    def exclude(self, *args, **kwargs): ...
    """Return a new QuerySet instance with NOT (args) ANDed to the existing"""
# _filter_or_exclude_inplace  [L2, 43t]  <- QuerySet._filter_or_exclude  CALLS
    def _filter_or_exclude_inplace(self, negate, args, kwargs): ...
# complex_filter  [L2, 52t]  <- QuerySet._filter_or_exclude  CALLS
    def complex_filter(self, filter_obj): ...
    """Return a new QuerySet instance with filter_obj added to the filters."""
# _chain  [L2, 27t]  <- QuerySet._filter_or_exclude  CALLS
    def _chain(self): ...
    """Return a copy of the current QuerySet that's ready for another"""
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
# build_where  [L2, 13t]  <- Query.build_filter  CALLS
    def build_where(self, filter_expr): ...
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

# tests/custom_managers/models.py

# filter  [L2, 17t]  <- QuerySet.filter  IMPLEMENTS
class CustomQuerySet:
    def filter(self, *args, **kwargs): ...

# --- mandatory identities (2) ---
django.db.models.expressions.Expression — django/db/models/expressions.py:520
django.utils.tree.Node — django/utils/tree.py:11
```
