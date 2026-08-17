# Item 6 — Worked Example: one real Django task, compiled

The verbatim output of `emit()` for the task *"why does `QuerySet.filter()` with a
related lookup produce this SQL?"*. Nothing below is edited, reordered or
abbreviated — it is the string a model would receive.

Figures and commentary are in
[`emit-item-6-results.md`](emit-item-6-results.md) §8.

```
seeds       django.db.models.query.QuerySet.filter
            django.db.models.query.QuerySet._filter_or_exclude
            django.db.models.sql.query.Query.add_q
            django.db.models.sql.query.Query.build_filter
            django.db.models.query_utils.Q._combine
            django.db.models.sql.query.Query.names_to_path
status      OK  (P3 FULL)
closure     92 symbols, 46 emitted across 17 files
tokens      7,724 emitted / 7,954 budgeted / 8,000 budget   margin -230
identities  3 mandatory, 23 hints
dedup       604 tokens over 57 lines
cost        24 round trips, 1562 ms compile, 72 offset seeks in 2.96 ms
```

---

````text
Compiled context · 46 symbols · 7,954 / 8,000 tokens
40 declarations · 6 bodies · 26 identities
Structural closure: complete (P3 FULL)

# --- seeds (6) ---
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

# django/db/models/query_utils.py

class Q:
# _combine  [L3, 84t]
    def _combine(self, other, conn):
        if getattr(other, "conditional", False) is False:
            raise TypeError(other)
        if not self:
            return other.copy()
        if not other and isinstance(other, Q):
            return self.copy()

        obj = self.create(connector=conn)
        obj.add(self, conn)
        obj.add(other, conn)
        return obj

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
from django.db.models.constants import LOOKUP_SEP

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
# add_q  [L3, 269t]
    def add_q(self, q_object, reuse_all=False):
        """
        A preprocessor for the internal _add_q(). Responsible for doing final
        join promotion.
        """
        # For join promotion this case is doing an AND for the added q_object
        # and existing conditions. So, any existing inner join forces the join
        # type to remain inner. Existing outer joins can however be demoted.
        # (Consider case where rel_a is LOUTER and rel_a__col=1 is added - if
        # rel_a doesn't produce any rows, then the whole condition must fail.
        # So, demotion is OK.
        existing_inner = {
            a for a in self.alias_map if self.alias_map[a].join_type == INNER
        }
        if reuse_all:
            can_reuse = set(self.alias_map)
        else:
            can_reuse = self.used_aliases
        clause, _ = self._add_q(q_object, can_reuse)
        if clause:
            self.where.add(clause, AND)
        self.demote_joins(existing_inner)
# names_to_path  [L3, 1062t]
    def names_to_path(self, names, opts, allow_many=True, fail_on_missing=False):
        """
        Walk the list of names and turns them into PathInfo tuples. A single
        name in 'names' can generate multiple PathInfos (m2m, for example).

        'names' is the path of names to travel, 'opts' is the model Options we
        start the name resolving from, 'allow_many' is as for setup_joins().
        If fail_on_missing is set to True, then a name that can't be resolved
        will generate a FieldError.

        Return a list of PathInfo tuples. In addition return the final field
        (the last used join field) and target (which is a field guaranteed to
        contain the same value as the final field). Finally, return those names
        that weren't found (which are likely transforms and the final lookup).
        """
        path, names_with_path = [], []
        for pos, name in enumerate(names):
            cur_names_with_path = (name, [])
            if name == "pk" and opts is not None:
                name = opts.pk.name

            field = None
            filtered_relation = None
            try:
                if opts is None:
                    raise FieldDoesNotExist
                field = opts.get_field(name)
            except FieldDoesNotExist:
                if name in self.annotation_select:
                    field = self.annotation_select[name].output_field
                elif name in self._filtered_relations and pos == 0:
                    filtered_relation = self._filtered_relations[name]
                    if LOOKUP_SEP in filtered_relation.relation_name:
                        parts = filtered_relation.relation_name.split(LOOKUP_SEP)
                        filtered_relation_path, field, _, _ = self.names_to_path(
                            parts,
                            opts,
                            allow_many,
                            fail_on_missing,
                        )
                        path.extend(filtered_relation_path[:-1])
                    else:
                        field = opts.get_field(filtered_relation.relation_name)
            if field is not None:
                # Fields that contain one-to-many relations with a generic
                # model (like a GenericForeignKey) cannot generate reverse
                # relations and therefore cannot be used for reverse querying.
                if field.is_relation and not field.related_model:
                    raise FieldError(
                        "Field %r does not generate an automatic reverse "
                        "relation and therefore cannot be used for reverse "
                        "querying. If it is a GenericForeignKey, consider "
                        "adding a GenericRelation." % name
                    )
                try:
                    model = field.model._meta.concrete_model
                except AttributeError:
                    # QuerySet.annotate() may introduce fields that aren't
                    # attached to a model.
                    model = None
            else:
                # We didn't find the current field, so move position back
                # one step.
                pos -= 1
                if pos == -1 or fail_on_missing:
                    available = sorted(
                        [
                            *get_field_names_from_opts(opts),
                            *self.annotations,
                            *self._filtered_relations,
                        ]
                    )
                    raise FieldError(
                        "Cannot resolve keyword '%s' into field. "
                        "Choices are: %s" % (name, ", ".join(available))
                    )
                break
            # Check if we need any joins for concrete inheritance cases (the
            # field lives in parent, but we are currently in one of its
            # children)
            if opts is not None and model is not opts.model:
                path_to_parent = opts.get_path_to_parent(model)
                if path_to_parent:
                    path.extend(path_to_parent)
                    cur_names_with_path[1].extend(path_to_parent)
                    opts = path_to_parent[-1].to_opts
            if hasattr(field, "path_infos"):
                if filtered_relation:
                    pathinfos = field.get_path_info(filtered_relation)
                else:
                    pathinfos = field.path_infos
                if not allow_many:
                    for inner_pos, p in enumerate(pathinfos):
                        if p.m2m:
                            cur_names_with_path[1].extend(pathinfos[0 : inner_pos + 1])
                            names_with_path.append(cur_names_with_path)
                            raise MultiJoin(pos + 1, names_with_path)
                last = pathinfos[-1]
                path.extend(pathinfos)
                final_field = last.join_field
                opts = last.to_opts
                targets = last.target_fields
                cur_names_with_path[1].extend(pathinfos)
                names_with_path.append(cur_names_with_path)
            else:
                # Local non-relational field.
                final_field = field
                targets = (field,)
                if fail_on_missing and pos + 1 != len(names):
                    raise FieldError(
                        "Cannot resolve keyword %r into field. Join on '%s'"
                        " not permitted." % (names[pos + 1], name)
                    )
                break
        return path, final_field, targets, names[pos + 1 :]

# --- dependencies (25) ---
# django/core/exceptions.py

# FieldError  [L2, 7t]  <- Query.build_filter  CALLS
class FieldError(Exception):
    ...

# django/db/models/constants.py

# LOOKUP_SEP  [L2, 6t]  <- Query.names_to_path  CALLS
LOOKUP_SEP = '__'

# django/db/models/expressions.py
from django.db import DatabaseError, NotSupportedError, connection

# ColPairs  [L2, 1362t]  <- Query.build_filter  CALLS
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
    def _constructor_signature(cls): ...  # from django.db.models.expressions.Expression
    def _identity(cls, value): ...  # from django.db.models.expressions.Expression
    def identity(self): ...  # from django.db.models.expressions.Expression
    def __eq__(self, other): ...  # from django.db.models.expressions.Expression
    def __hash__(self): ...  # from django.db.models.expressions.Expression
    def __getstate__(self): ...  # from django.db.models.expressions.BaseExpression
    def get_db_converters(self, connection): ...  # from django.db.models.expressions.BaseExpression
    def _parse_expressions(self, *expressions): ...  # from django.db.models.expressions.BaseExpression
    def contains_aggregate(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_over_clause(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_column_references(self): ...  # from django.db.models.expressions.BaseExpression
    def contains_subquery(self): ...  # from django.db.models.expressions.BaseExpression
    def conditional(self): ...  # from django.db.models.expressions.BaseExpression
    def field(self): ...  # from django.db.models.expressions.BaseExpression
    def output_field(self): ...  # from django.db.models.expressions.BaseExpression
    def _output_field_or_none(self): ...  # from django.db.models.expressions.BaseExpression
    def _resolve_output_field(self): ...  # from django.db.models.expressions.BaseExpression
    def _convert_value_noop(value, expression, connection): ...  # from django.db.models.expressions.BaseExpression
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
    def _combine(self, other, connector, reversed): ...  # from django.db.models.expressions.Combinable
    def __neg__(self): ...  # from django.db.models.expressions.Combinable
    def __add__(self, other): ...  # from django.db.models.expressions.Combinable
    def __sub__(self, other): ...  # from django.db.models.expressions.Combinable
    def __mul__(self, other): ...  # from django.db.models.expressions.Combinable
    def __truediv__(self, other): ...  # from django.db.models.expressions.Combinable
    def __mod__(self, other): ...  # from django.db.models.expressions.Combinable
    def __pow__(self, other): ...  # from django.db.models.expressions.Combinable
    def __and__(self, other): ...  # from django.db.models.expressions.Combinable
    def bitand(self, other): ...  # from django.db.models.expressions.Combinable
    def bitleftshift(self, other): ...  # from django.db.models.expressions.Combinable
    def bitrightshift(self, other): ...  # from django.db.models.expressions.Combinable
    def __xor__(self, other): ...  # from django.db.models.expressions.Combinable
    def bitxor(self, other): ...  # from django.db.models.expressions.Combinable
    def __or__(self, other): ...  # from django.db.models.expressions.Combinable
    def bitor(self, other): ...  # from django.db.models.expressions.Combinable
    def __radd__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rsub__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rmul__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rtruediv__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rmod__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rpow__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rand__(self, other): ...  # from django.db.models.expressions.Combinable
    def __ror__(self, other): ...  # from django.db.models.expressions.Combinable
    def __rxor__(self, other): ...  # from django.db.models.expressions.Combinable
    def __invert__(self): ...  # from django.db.models.expressions.Combinable

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

# django/db/models/query_utils.py

# create  [L2, 22t]  <- Q._combine  CALLS
@classmethod
class Q:
    def create(cls, children=None, connector=None, negated=False): ...

# django/db/models/sql/datastructures.py

# MultiJoin  [L2, 20t]  <- Query.names_to_path  CALLS
class MultiJoin(Exception):
    def __init__(self, names_pos, path_with_names): ...

# django/db/models/sql/query.py
from itertools import chain, count, product
from django.db import DEFAULT_DB_ALIAS, NotSupportedError, connections
from django.db.models.fields import Field
import functools

# get_field_names_from_opts  [L2, 18t]  <- Query.names_to_path  CALLS
def get_field_names_from_opts(opts): ...

class Query:
# get_meta  [L2, 27t]  <- Query.build_filter  CALLS
    def get_meta(self): ...
    """Return the Options instance (the model._meta) from which to start"""
# _get_col  [L2, 17t]  <- Query.build_filter  CALLS
    def _get_col(self, target, field, alias): ...
# demote_joins  [L2, 49t]  <- Query.add_q  CALLS
    def demote_joins(self, aliases): ...
    """Change join type from LOUTER to INNER for all joins in aliases."""
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
# _add_q  [L2, 81t]  <- Query.add_q  CALLS
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

# WhereNode  [L2, 555t]  <- Query.build_filter  CALLS
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
    def __init__(self, children=None, connector=None, negated=False): ...  # from django.utils.tree.Node
    def create(cls, children=None, connector=None, negated=False): ...  # from django.utils.tree.Node
    def __str__(self): ...  # from django.utils.tree.Node
    def __repr__(self): ...  # from django.utils.tree.Node
    def __copy__(self): ...  # from django.utils.tree.Node
    def __deepcopy__(self, memodict): ...  # from django.utils.tree.Node
    def __len__(self): ...  # from django.utils.tree.Node
    def __bool__(self): ...  # from django.utils.tree.Node
    def __contains__(self, other): ...  # from django.utils.tree.Node
    def __eq__(self, other): ...  # from django.utils.tree.Node
    def __hash__(self): ...  # from django.utils.tree.Node
    def add(self, data, conn_type): ...  # from django.utils.tree.Node
    def negate(self): ...  # from django.utils.tree.Node

# django/utils/tree.py

class Node:
# add  [L2, 28t]  <- Query.add_q  CALLS
    def add(self, data, conn_type): ...
    """Combine this tree and the data represented by data using the"""

# --- optional context (15) ---
# django/db/models/query.py

class QuerySet:
# exclude  [L2, 35t]  <- QuerySet._filter_or_exclude  OPTIONAL:static_caller
    def exclude(self, *args, **kwargs): ...
    """Return a new QuerySet instance with NOT (args) ANDed to the existing"""
# complex_filter  [L2, 52t]  <- QuerySet._filter_or_exclude  OPTIONAL:static_caller
    def complex_filter(self, filter_obj): ...
    """Return a new QuerySet instance with filter_obj added to the filters."""

# django/db/models/query_utils.py

class Q:
# __or__  [L2, 13t]  <- Q._combine  OPTIONAL:static_caller
    def __or__(self, other): ...
# __and__  [L2, 13t]  <- Q._combine  OPTIONAL:static_caller
    def __and__(self, other): ...
# __xor__  [L2, 13t]  <- Q._combine  OPTIONAL:static_caller
    def __xor__(self, other): ...

# django/db/models/sql/query.py

class Query:
# add_filter  [L2, 39t]  <- Query.split_exclude  CALLS
    def add_filter(self, filter_lhs, filter_rhs): ...
# build_where  [L2, 13t]  <- Query.build_filter  OPTIONAL:static_caller
    def build_where(self, filter_expr): ...
# add_ordering  [L2, 55t]  <- Query.names_to_path  OPTIONAL:static_caller
    def add_ordering(self, *ordering): ...
    'Add items from the \'ordering\' sequence to the query\'s "order by"'

# tests/composite_pk/test_names_to_path.py
from .models import Comment, Tenant, User
from django.db.models.query_utils import PathInfo
from django.db.models.sql import Query
from django.test import TestCase

# test_names_to_path  [L2, 45t]  <- Query.names_to_path  OPTIONAL:static_caller
# module tests.composite_pk.test_names_to_path

# test_id  [L2, 31t]  <- Query.names_to_path  OPTIONAL:static_caller
class NamesToPathTests:
    def test_id(self): ...

# test_pk  [L2, 31t]  <- Query.names_to_path  OPTIONAL:static_caller
class NamesToPathTests:
    def test_pk(self): ...

# test_tenant_id  [L2, 43t]  <- Query.names_to_path  OPTIONAL:static_caller
class NamesToPathTests:
    def test_tenant_id(self): ...

# test_user_id  [L2, 42t]  <- Query.names_to_path  OPTIONAL:static_caller
class NamesToPathTests:
    def test_user_id(self): ...

# test_comments  [L2, 41t]  <- Query.names_to_path  OPTIONAL:static_caller
class NamesToPathTests:
    def test_comments(self): ...

# tests/queries/test_query.py
from django.core.exceptions import FieldError
from django.db.models.sql.query import JoinPromoter, Query, get_field_names_from_opts

# test_names_to_path_field_error  [L2, 44t]  <- Query.names_to_path  OPTIONAL:static_caller
class TestQueryNoModel:
    def test_names_to_path_field_error(self): ...

# --- mandatory identities (3) ---
django.db.models.expressions.Expression — django/db/models/expressions.py:520
django.utils.tree.Node — django/utils/tree.py:11
django.utils.tree.Node.create — django/utils/tree.py:29

# --- identity hints (23) ---
django.db.models.expressions.Col — django/db/models/expressions.py:1351
django.db.models.expressions.Exists — django/db/models/expressions.py:1887
django.db.models.expressions.When — django/db/models/expressions.py:1636
django.db.models.functions.math.Mod — django/db/models/functions/math.py:126
django.db.models.query — django/db/models/query.py:1
django.db.models.query.QuerySet — django/db/models/query.py:339
django.db.models.query.QuerySet._clone — django/db/models/query.py:2262
django.db.models.query_utils — django/db/models/query_utils.py:1
django.db.models.query_utils.Q — django/db/models/query_utils.py:41
django.db.models.query_utils.Q.check — django/db/models/query_utils.py:171
django.db.models.query_utils.refs_expression — django/db/models/query_utils.py:469
django.db.models.sql.query — django/db/models/sql/query.py:1
django.db.models.sql.query.Query — django/db/models/sql/query.py:232
django.db.models.sql.query.Query.join — django/db/models/sql/query.py:1135
django.db.models.sql.query.Query.ref_alias — django/db/models/sql/query.py:927
django.db.models.sql.query.Query.set_values — django/db/models/sql/query.py:2588
django.db.utils.NotSupportedError — django/db/utils.py:49
django.test.testcases.SimpleTestCase.assertRaisesMessage — django/test/testcases.py:866
tests.composite_pk.test_names_to_path.NamesToPathTests — tests/composite_pk/test_names_to_path.py:8
tests.composite_pk.test_names_to_path.NamesToPathTests.test_user_tenant_id — tests/composite_pk/test_names_to_path.py:78
tests.queries.test_query — tests/queries/test_query.py:1
tests.queries.test_query.TestQueryNoModel — tests/queries/test_query.py:164
tests.queries.test_query.TestQueryNoModel.test_names_to_path_field — tests/queries/test_query.py:193
````
