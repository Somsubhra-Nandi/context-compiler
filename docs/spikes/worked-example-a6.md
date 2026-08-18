## A6-bis worked examples

The six-seed example is selected by a rule fixed before inspecting its
rendered output: choose the first trial whose mandatory closure size is
closest to the corrected 200-trial median. That is trial 5, with closure size
36 and these six seeds. The seeds are:

- `django.db.migrations.autodetector.MigrationAutodetector.generate_altered_db_table_comment`
- `django.db.migrations.operations.models.CreateModel.references_model`
- `django.contrib.admin.views.main.ChangeList.get_results`
- `django.db.models.sql.compiler.SQLCompiler.get_related_selections`
- `django.core.management.commands.compilemessages.Command.handle`
- `django.template.defaulttags.do_if`

The complete emitted context follows.

### Six-seed representative (trial 5)

Compiled context · 32 symbols · 7,732 / 8,000 tokens
26 declarations · 6 bodies · 25 identities
Structural closure: complete (P3 FULL)

# --- seeds (6) ---
# django/contrib/admin/views/main.py
from django.contrib.admin.options import (
    IS_FACETS_VAR,
    IS_POPUP_VAR,
    SOURCE_MODEL_VAR,
    TO_FIELD_VAR,
    IncorrectLookupParameters,
    ShowFacets,
)
from django.core.paginator import InvalidPage

class ChangeList:
# get_results  [L3, 409t]
    def get_results(self, request):
        paginator = self.model_admin.get_paginator(
            request, self.queryset, self.list_per_page
        )
        # Get the number of objects, with admin filters applied.
        result_count = paginator.count

        # Get the total number of objects, with no admin filters applied.
        # Note this isn't necessarily the same as result_count in the case of
        # no filtering. Filters defined in list_filters may still apply some
        # default filtering which may be removed with query parameters.
        if self.model_admin.show_full_result_count:
            full_result_count = self.root_queryset.count()
        else:
            full_result_count = None
        can_show_all = result_count <= self.list_max_show_all
        multi_page = result_count > self.list_per_page

        # Get the list of objects to display on this page.
        if (self.show_all and can_show_all) or not multi_page:
            result_list = self.queryset._clone()
        else:
            try:
                result_list = paginator.page(self.page_num).object_list
            except InvalidPage:
                raise IncorrectLookupParameters

        self.result_count = result_count
        self.show_full_result_count = self.model_admin.show_full_result_count
        # Admin actions are shown if there is at least one entry
        # or if entries are not counted because show_full_result_count is
        # disabled
        self.show_admin_actions = not self.show_full_result_count or bool(
            full_result_count
        )
        self.full_result_count = full_result_count
        self.result_list = result_list
        self.can_show_all = can_show_all
        self.multi_page = multi_page
        self.paginator = paginator

# django/core/management/commands/compilemessages.py
from django.core.management.base import BaseCommand, CommandError
from django.core.management.utils import find_command, is_ignored_path, popen_wrapper
import glob
import os

class Command:
# handle  [L3, 574t]
    def handle(self, **options):
        locale = options["locale"]
        exclude = options["exclude"]
        ignore_patterns = set(options["ignore_patterns"])
        self.verbosity = options["verbosity"]
        if options["fuzzy"]:
            self.program_options = [*self.program_options, "-f"]

        if find_command(self.program) is None:
            raise CommandError(
                f"Can't find {self.program}. Make sure you have GNU gettext "
                "tools 0.19 or newer installed."
            )

        basedirs = [os.path.join("conf", "locale"), "locale"]
        if os.environ.get("DJANGO_SETTINGS_MODULE"):
            from django.conf import settings

            basedirs.extend(settings.LOCALE_PATHS)

        # Walk entire tree, looking for locale directories
        for dirpath, dirnames, filenames in os.walk(".", topdown=True):
            # As we may modify dirnames, iterate through a copy of it instead
            for dirname in list(dirnames):
                if is_ignored_path(
                    os.path.normpath(os.path.join(dirpath, dirname)), ignore_patterns
                ):
                    dirnames.remove(dirname)
                elif dirname == "locale":
                    basedirs.append(os.path.join(dirpath, dirname))

        # Gather existing directories.
        basedirs = set(map(os.path.abspath, filter(os.path.isdir, basedirs)))

        if not basedirs:
            raise CommandError(
                "This script should be run from the Django Git "
                "checkout or your project or app tree, or with "
                "the settings module specified."
            )

        # Build locale list
        all_locales = []
        for basedir in basedirs:
            locale_dirs = filter(os.path.isdir, glob.glob("%s/*" % basedir))
            all_locales.extend(map(os.path.basename, locale_dirs))

        # Account for excluded locales
        locales = locale or all_locales
        locales = set(locales).difference(exclude)

        self.has_errors = False
        for basedir in basedirs:
            if locales:
                dirs = [
                    os.path.join(basedir, locale, "LC_MESSAGES") for locale in locales
                ]
            else:
                dirs = [basedir]
            locations = []
            for ldir in dirs:
                for dirpath, dirnames, filenames in os.walk(ldir):
                    locations.extend(
                        (dirpath, f) for f in filenames if f.endswith(".po")
                    )
            if locations:
                self.compile_messages(locations)

        if self.has_errors:
            raise CommandError("compilemessages generated one or more errors.")

# django/db/migrations/autodetector.py
from django.db.migrations import operations

class MigrationAutodetector:
# generate_altered_db_table_comment  [L3, 189t]
    def generate_altered_db_table_comment(self):
        models_to_check = self.kept_model_keys.union(self.kept_proxy_keys)
        for app_label, model_name in sorted(models_to_check):
            old_model_name = self.renamed_models.get(
                (app_label, model_name), model_name
            )
            old_model_state = self.from_state.models[app_label, old_model_name]
            new_model_state = self.to_state.models[app_label, model_name]

            old_db_table_comment = old_model_state.options.get("db_table_comment")
            new_db_table_comment = new_model_state.options.get("db_table_comment")
            if old_db_table_comment != new_db_table_comment:
                self.add_operation(
                    app_label,
                    operations.AlterModelTableComment(
                        name=model_name,
                        table_comment=new_db_table_comment,
                    ),
                )

# django/db/migrations/operations/models.py
from django.db import models
from django.db.migrations.utils import field_references, resolve_relation

class CreateModel:
# references_model  [L3, 182t]
    def references_model(self, name, app_label):
        name_lower = name.lower()
        if name_lower == self.name_lower:
            return True

        # Check we didn't inherit from the model
        reference_model_tuple = (app_label, name_lower)
        for base in self.bases:
            if (
                base is not models.Model
                and isinstance(base, (models.base.ModelBase, str))
                and resolve_relation(base, app_label) == reference_model_tuple
            ):
                return True

        # Check we have no FKs/M2Ms with it
        for _name, field in self.fields:
            if field_references(
                (app_label, self.name_lower), field, reference_model_tuple
            ):
                return True
        return False

# django/db/models/sql/compiler.py
from django.core.exceptions import EmptyResultSet, FieldError, FullResultSet
from django.db.models.query_utils import select_related_descend
from functools import partial
from itertools import chain

class SQLCompiler:
# get_related_selections  [L3, 1683t]
    def get_related_selections(
        self,
        select,
        select_mask,
        opts=None,
        root_alias=None,
        cur_depth=1,
        requested=None,
        restricted=None,
    ):
        """
        Fill in the information needed for a select_related query. The current
        depth is measured as the number of connections away from the root model
        (for example, cur_depth=1 means we are looking at models with direct
        connections to the root model).
        """

        def _get_field_choices():
            direct_choices = (f.name for f in opts.fields if f.is_relation)
            reverse_choices = (
                f.field.related_query_name()
                for f in opts.related_objects
                if f.field.unique
            )
            return chain(
                direct_choices, reverse_choices, self.query._filtered_relations
            )

        related_klass_infos = []
        if not restricted and cur_depth > self.query.max_depth:
            # We've recursed far enough; bail out.
            return related_klass_infos

        if not opts:
            opts = self.query.get_meta()
            root_alias = self.query.get_initial_alias()

        # Setup for the case when only particular related fields should be
        # included in the related selection.
        fields_found = set()
        if requested is None:
            restricted = isinstance(self.query.select_related, dict)
            if restricted:
                requested = self.query.select_related

        def get_related_klass_infos(klass_info, related_klass_infos):
            klass_info["related_klass_infos"] = related_klass_infos

        for f in opts.fields:
            fields_found.add(f.name)

            if restricted:
                next = requested.get(f.name, {})
                if not f.is_relation:
                    # If a non-related field is used like a relation,
                    # or if a single non-relational field is given.
                    if next or f.name in requested:
                        raise FieldError(
                            "Non-relational field given in select_related: '%s'. "
                            "Choices are: %s"
                            % (
                                f.name,
                                ", ".join(_get_field_choices()) or "(none)",
                            )
                        )
            else:
                next = False

            if not select_related_descend(f, restricted, requested, select_mask):
                continue
            related_select_mask = select_mask.get(f) or {}
            klass_info = {
                "model": f.remote_field.model,
                "field": f,
                "reverse": False,
                "local_setter": f.set_cached_value,
                "remote_setter": (
                    f.remote_field.set_cached_value if f.unique else lambda x, y: None
                ),
                "from_parent": False,
            }
            related_klass_infos.append(klass_info)
            select_fields = []
            _, _, _, joins, _, _ = self.query.setup_joins([f.name], opts, root_alias)
            alias = joins[-1]
            columns = self.get_default_columns(
                related_select_mask, start_alias=alias, opts=f.remote_field.model._meta
            )
            for col in columns:
                select_fields.append(len(select))
                select.append((col, None))
            klass_info["select_fields"] = select_fields
            next_klass_infos = self.get_related_selections(
                select,
                related_select_mask,
                f.remote_field.model._meta,
                alias,
                cur_depth + 1,
                next,
                restricted,
            )
            get_related_klass_infos(klass_info, next_klass_infos)

        if restricted:
            related_fields = [
                (o, o.field, o.related_model)
                for o in opts.related_objects
                if o.field.unique and not o.many_to_many
            ]
            for related_object, related_field, model in related_fields:
                if not select_related_descend(
                    related_object,
                    restricted,
                    requested,
                    select_mask,
                ):
                    continue

                related_select_mask = select_mask.get(related_object) or {}
                related_field_name = related_field.related_query_name()
                fields_found.add(related_field_name)

                join_info = self.query.setup_joins(
                    [related_field_name], opts, root_alias
                )
                alias = join_info.joins[-1]
                from_parent = issubclass(model, opts.model) and model is not opts.model
                klass_info = {
                    "model": model,
                    "field": related_field,
                    "reverse": True,
                    "local_setter": related_object.set_cached_value,
                    "remote_setter": related_field.set_cached_value,
                    "from_parent": from_parent,
                }
                related_klass_infos.append(klass_info)
                select_fields = []
                columns = self.get_default_columns(
                    related_select_mask,
                    start_alias=alias,
                    opts=model._meta,
                    from_parent=opts.model,
                )
                for col in columns:
                    select_fields.append(len(select))
                    select.append((col, None))
                klass_info["select_fields"] = select_fields
                next = requested.get(related_field_name, {})
                next_klass_infos = self.get_related_selections(
                    select,
                    related_select_mask,
                    model._meta,
                    alias,
                    cur_depth + 1,
                    next,
                    restricted,
                )
                get_related_klass_infos(klass_info, next_klass_infos)

            def local_setter(final_field, obj, from_obj):
                # Set a reverse fk object when relation is non-empty.
                if from_obj:
                    final_field.remote_field.set_cached_value(from_obj, obj)

            def local_setter_noop(obj, from_obj):
                pass

            def remote_setter(name, obj, from_obj):
                setattr(from_obj, name, obj)

            for name in list(requested):
                # Filtered relations work only on the topmost level.
                if cur_depth > 1:
                    break
                if name in self.query._filtered_relations:
                    fields_found.add(name)
                    final_field, _, join_opts, joins, _, _ = self.query.setup_joins(
                        [name], opts, root_alias
                    )
                    model = join_opts.model
                    alias = joins[-1]
                    from_parent = (
                        issubclass(model, opts.model) and model is not opts.model
                    )
                    klass_info = {
                        "model": model,
                        "field": final_field,
                        "reverse": True,
                        "local_setter": (
                            partial(local_setter, final_field)
                            if len(joins) <= 2
                            else local_setter_noop
                        ),
                        "remote_setter": partial(remote_setter, name),
                        "from_parent": from_parent,
                    }
                    related_klass_infos.append(klass_info)
                    select_fields = []
                    field_select_mask = select_mask.get((name, final_field)) or {}
                    columns = self.get_default_columns(
                        field_select_mask,
                        start_alias=alias,
                        opts=model._meta,
                        from_parent=opts.model,
                    )
                    for col in columns:
                        select_fields.append(len(select))
                        select.append((col, None))
                    klass_info["select_fields"] = select_fields
                    next_requested = requested.get(name, {})
                    next_klass_infos = self.get_related_selections(
                        select,
                        field_select_mask,
                        opts=model._meta,
                        root_alias=alias,
                        cur_depth=cur_depth + 1,
                        requested=next_requested,
                        restricted=restricted,
                    )
                    get_related_klass_infos(klass_info, next_klass_infos)
            fields_not_found = set(requested).difference(fields_found)
            if fields_not_found:
                invalid_fields = ("'%s'" % s for s in fields_not_found)
                raise FieldError(
                    "Invalid field name(s) given in select_related: %s. "
                    "Choices are: %s"
                    % (
                        ", ".join(invalid_fields),
                        ", ".join(_get_field_choices()) or "(none)",
                    )
                )
        return related_klass_infos

# django/template/defaulttags.py
from .base import (
    BLOCK_TAG_END,
    BLOCK_TAG_START,
    COMMENT_TAG_END,
    COMMENT_TAG_START,
    FILTER_SEPARATOR,
    SINGLE_BRACE_END,
    SINGLE_BRACE_START,
    VARIABLE_ATTRIBUTE_SEPARATOR,
    VARIABLE_TAG_END,
    VARIABLE_TAG_START,
    Node,
    NodeList,
    PartialTemplate,
    TemplateSyntaxError,
    VariableDoesNotExist,
    kwarg_re,
    render_value_in_context,
    token_kwargs,
)

# do_if  [L3, 781t]
def do_if(parser, token):
    """
    Evaluate a variable, and if that variable is "true" (i.e., exists, is not
    empty, and is not a false boolean value), output the contents of the block:

    ::

        {% if athlete_list %}
            Number of athletes: {{ athlete_list|count }}
        {% elif athlete_in_locker_room_list %}
            Athletes should be out of the locker room soon!
        {% else %}
            No athletes.
        {% endif %}

    In the above, if ``athlete_list`` is not empty, the number of athletes will
    be displayed by the ``{{ athlete_list|count }}`` variable.

    The ``if`` tag may take one or several `` {% elif %}`` clauses, as well as
    an ``{% else %}`` clause that will be displayed if all previous conditions
    fail. These clauses are optional.

    ``if`` tags may use ``or``, ``and`` or ``not`` to test a number of
    variables or to negate a given variable::

        {% if not athlete_list %}
            There are no athletes.
        {% endif %}

        {% if athlete_list or coach_list %}
            There are some athletes or some coaches.
        {% endif %}

        {% if athlete_list and coach_list %}
            Both athletes and coaches are available.
        {% endif %}

        {% if not athlete_list or coach_list %}
            There are no athletes, or there are some coaches.
        {% endif %}

        {% if athlete_list and not coach_list %}
            There are some athletes and absolutely no coaches.
        {% endif %}

    Comparison operators are also available, and the use of filters is also
    allowed, for example::

        {% if articles|length >= 5 %}...{% endif %}

    Arguments and operators _must_ have a space between them, so
    ``{% if 1>2 %}`` is not a valid if tag.

    All supported operators are: ``or``, ``and``, ``in``, ``not in``
    ``==``, ``!=``, ``>``, ``>=``, ``<`` and ``<=``.

    Operator precedence follows Python.
    """
    # {% if ... %}
    bits = token.split_contents()[1:]
    condition = TemplateIfParser(parser, bits).parse()
    nodelist = parser.parse(("elif", "else", "endif"))
    conditions_nodelists = [(condition, nodelist)]
    token = parser.next_token()

    # {% elif ... %} (repeatable)
    while token.contents.startswith("elif"):
        bits = token.split_contents()[1:]
        condition = TemplateIfParser(parser, bits).parse()
        nodelist = parser.parse(("elif", "else", "endif"))
        conditions_nodelists.append((condition, nodelist))
        token = parser.next_token()

    # {% else %} (optional)
    if token.contents == "else":
        nodelist = parser.parse(("endif",))
        conditions_nodelists.append((None, nodelist))
        token = parser.next_token()

    # {% endif %}
    if token.contents != "endif":
        raise TemplateSyntaxError(
            'Malformed template tag at line {}: "{}"'.format(
                token.lineno, token.contents
            )
        )

    return IfNode(conditions_nodelists)

# --- dependencies (18) ---
# django/core/exceptions.py

# FieldError  [L2, 7t]  <- SQLCompiler.get_related_selections  CALLS
class FieldError(Exception):
    ...

# django/core/management/base.py

# CommandError  [L2, 24t]  <- Command.handle  CALLS
class CommandError(Exception):
    def __init__(self, *args, returncode=1, **kwargs): ...

class BaseCommand:
# handle  [L2, 30t]  <- Command.handle  OVERRIDES
    def handle(self, *args, **options): ...
    """The actual logic of the command. Subclasses must implement"""

# django/core/management/commands/compilemessages.py
from pathlib import Path
import concurrent.futures

class Command:
# compile_messages  [L2, 57t]  <- Command.handle  CALLS
    def compile_messages(self, locations): ...
    """Locations is a list of tuples: [(directory, file), ...]"""

# django/core/management/utils.py
import fnmatch

# find_command  [L2, 16t]  <- Command.handle  CALLS
def find_command(cmd, path=None, pathext=None): ...

# is_ignored_path  [L2, 35t]  <- Command.handle  CALLS
def is_ignored_path(path, ignore_patterns): ...
"""Check if the given path should be ignored or not based on matching"""

# django/db/migrations/autodetector.py

class MigrationAutodetector:
# add_operation  [L2, 25t]  <- MigrationAutodetector.generate_altered_db_table_comment  CALLS
    def add_operation(self, app_label, operation, dependencies=None, beginning=False): ...

# django/db/migrations/operations/models.py

# AlterModelTableComment  [L2, 257t]  <- MigrationAutodetector.generate_altered_db_table_comment  CALLS
class AlterModelTableComment(ModelOptionOperation):
    def __init__(self, name, table_comment): ...
    def deconstruct(self): ...
    def state_forwards(self, app_label, state): ...
    def database_forwards(self, app_label, schema_editor, from_state, to_state): ...
    def database_backwards(self, app_label, schema_editor, from_state, to_state): ...
    def describe(self): ...
    def migration_name_fragment(self): ...
    def reduce(self, operation, app_label): ...  # from django.db.migrations.operations.models.ModelOptionOperation
    def name_lower(self): ...  # from django.db.migrations.operations.models.ModelOperation
    def references_model(self, name, app_label): ...  # from django.db.migrations.operations.models.ModelOperation
    def can_reduce_through(self, operation, app_label): ...  # from django.db.migrations.operations.models.ModelOperation
    def formatted_description(self): ...  # from django.db.migrations.operations.base.Operation
    def references_field(self, model_name, name, app_label): ...  # from django.db.migrations.operations.base.Operation
    def allow_migrate_model(self, connection_alias, model): ...  # from django.db.migrations.operations.base.Operation

class ModelOperation:
# references_model  [L2, 16t]  <- CreateModel.references_model  OVERRIDES
    def references_model(self, name, app_label): ...

# django/db/migrations/utils.py
from django.db.models.fields.related import RECURSIVE_RELATIONSHIP_CONSTANT

# resolve_relation  [L2, 45t]  <- CreateModel.references_model  CALLS
def resolve_relation(model, app_label=None, model_name=None): ...
"""Turn a model class or model reference string and return a model tuple."""

# field_references  [L2, 37t]  <- CreateModel.references_model  CALLS
def field_references(model_tuple, field, reference_model_tuple, reference_field_name=None, reference_field=None): ...
"""Return either False or a FieldReference if `field` references provided"""

# django/db/models/query_utils.py
from django.core.exceptions import FieldError

# select_related_descend  [L2, 37t]  <- SQLCompiler.get_related_selections  CALLS
def select_related_descend(field, restricted, requested, select_mask): ...
"""Return whether `field` should be used to descend deeper for"""

# django/db/models/sql/compiler.py
from django.db.models.fields import AutoField, composite

class SQLCompiler:
# get_default_columns  [L2, 51t]  <- SQLCompiler.get_related_selections  CALLS
    def get_default_columns(self, select_mask, start_alias=None, opts=None, from_parent=None): ...
    """Compute the default columns for selecting every field in the base"""

# django/template/defaulttags.py
from .smartif import IfParser, Literal

# IfNode  [L2, 180t]  <- defaulttags.do_if  CALLS
class IfNode(Node):
    def __init__(self, conditions_nodelists): ...
    def __repr__(self): ...
    def __iter__(self): ...
    def nodelist(self): ...
    def render(self, context): ...
    def render_annotated(self, context): ...  # from django.template.base.Node
    def get_nodes_by_type(self, nodetype): ...  # from django.template.base.Node

# TemplateIfParser  [L2, 208t]  <- defaulttags.do_if  CALLS
class TemplateIfParser(IfParser):
    def __init__(self, parser, *args, **kwargs): ...
    def create_var(self, value): ...
    def translate_token(self, token): ...  # from django.template.smartif.IfParser
    def next_token(self): ...  # from django.template.smartif.IfParser
    def parse(self): ...  # from django.template.smartif.IfParser
    def expression(self, rbp=0): ...  # from django.template.smartif.IfParser

# django/template/exceptions.py

# TemplateSyntaxError  [L2, 8t]  <- defaulttags.do_if  CALLS
class TemplateSyntaxError(Exception):
    ...

# django/template/library.py

class Library:
# tag  [L2, 16t]  <- defaulttags.do_if  CALLS
    def tag(self, name=None, compile_function=None): ...

# django/template/smartif.py

class IfParser:
# parse  [L2, 10t]  <- defaulttags.do_if  CALLS
    def parse(self): ...

# --- optional context (8) ---
# django/contrib/admin/views/main.py
from django.contrib import messages
from django.contrib.admin.exceptions import (
    DisallowedModelAdminLookup,
    DisallowedModelAdminToField,
)
from django.utils.translation import gettext

class ChangeList:
# __init__  [L2, 136t]  <- ChangeList.get_results  OPTIONAL:static_caller
    def __init__(self, request, model, list_display, list_display_links, list_filter, date_hierarchy, search_fields, list_select_related, list_per_page, list_max_show_all, list_editable, model_admin, sortable_by, search_help_text): ...

# django/db/migrations/autodetector.py

class MigrationAutodetector:
# _detect_changes  [L2, 35t]  <- MigrationAutodetector.generate_altered_db_table_comment  OPTIONAL:static_caller
    def _detect_changes(self, convert_apps=None, graph=None): ...
    """Return a dict of migration plans which will achieve the"""

# django/db/models/sql/compiler.py
from django.db.models.expressions import ColPairs, F, OrderBy, RawSQL, Ref, Value

class SQLCompiler:
# get_select  [L2, 59t]  <- SQLCompiler.get_related_selections  OPTIONAL:static_caller
    def get_select(self, with_col_aliases=False): ...
    """Return three values:"""

# tests/admin_changelist/tests.py
from .admin import (
    BandAdmin,
    ChildAdmin,
    ChordsBandAdmin,
    ConcertAdmin,
    CustomPaginationAdmin,
    CustomPaginator,
    DynamicListDisplayChildAdmin,
    DynamicListDisplayLinksChildAdmin,
    DynamicListFilterChildAdmin,
    DynamicSearchFieldsChildAdmin,
    EmptyValueChildAdmin,
    EventAdmin,
    FilteredChildAdmin,
    GrandChildAdmin,
    GroupAdmin,
    InvitationAdmin,
    NoListDisplayLinksParentAdmin,
    ParentAdmin,
    ParentAdminTwoSearchFields,
    QuartetAdmin,
    SwallowAdmin,
)
from .admin import site as custom_site
from .models import (
    Band,
    CharPK,
    Child,
    ChordsBand,
    ChordsMusician,
    Concert,
    CustomIdUser,
    Event,
    Genre,
    GrandChild,
    Group,
    Invitation,
    Membership,
    MixedFieldsModel,
    Musician,
    OrderedObject,
    Parent,
    Quartet,
    Swallow,
    SwallowOneToOne,
    UnorderedObject,
)

# test_custom_paginator  [L2, 232t]  <- ChangeList.get_results  OPTIONAL:static_caller
class ChangeListTests:
    def test_custom_paginator(self): ...

# test_distinct_for_m2m_in_list_filter  [L2, 257t]  <- ChangeList.get_results  OPTIONAL:static_caller
class ChangeListTests:
    def test_distinct_for_m2m_in_list_filter(self): ...
    """Regression test for #13902: When using a ManyToMany in list_filter,"""

# test_distinct_for_through_m2m_in_list_filter  [L2, 258t]  <- ChangeList.get_results  OPTIONAL:static_caller
class ChangeListTests:
    def test_distinct_for_through_m2m_in_list_filter(self): ...
    """Regression test for #13902: When using a ManyToMany in list_filter,"""

# test_distinct_for_inherited_m2m_in_list_filter  [L2, 259t]  <- ChangeList.get_results  OPTIONAL:static_caller
class ChangeListTests:
    def test_distinct_for_inherited_m2m_in_list_filter(self): ...
    """Regression test for #13902: When using a ManyToMany in list_filter,"""

# tests/admin_filters/tests.py
from .models import Book, Bookmark, Department, Employee, ImprovedBook, TaggedItem

# test_list_filter_queryset_filtered_by_default  [L2, 51t]  <- ChangeList.get_results  OPTIONAL:static_caller
class ListFiltersTests:
    def test_list_filter_queryset_filtered_by_default(self): ...
    """A list filter that filters the queryset by default gives the correct"""

# --- mandatory identities (4) ---
django.db.migrations.operations.base.Operation.references_model — django/db/migrations/operations/base.py:127
django.db.migrations.operations.models.ModelOptionOperation — django/db/migrations/operations/models.py:535
django.template.base.Node — django/template/base.py:1048
django.template.smartif.IfParser — django/template/smartif.py:161

# --- identity hints (21) ---
django.contrib.admin.options.TO_FIELD_VAR — django/contrib/admin/options.py:86
django.contrib.admin.views.main.ALL_VAR — django/contrib/admin/views/main.py:41
django.contrib.admin.views.main.ERROR_FLAG — django/contrib/admin/views/main.py:45
django.contrib.admin.views.main.SEARCH_VAR — django/contrib/admin/views/main.py:44
django.contrib.messages.api.add_message — django/contrib/messages/api.py:22
django.core.management.base.OutputWrapper.write — django/core/management/base.py:182
django.core.management.utils.normalize_path_patterns — django/core/management/utils.py:132
django.db.models.expressions.RawSQL — django/db/models/expressions.py:1268
django.db.models.expressions.Value — django/db/models/expressions.py:1148
django.db.models.sql.compiler.SQLCompiler.compile — django/db/models/sql/compiler.py:574
django.forms.forms.BaseForm.is_valid — django/forms/forms.py:204
django.template.library.Library.tag_function — django/template/library.py:53
django.template.smartif.IfParser.expression — django/template/smartif.py:209
django.template.smartif.TokenBase.display — django/template/smartif.py:34
django.test.client.RequestFactory.get — django/test/client.py:470
django.utils.translation.gettext — django/utils/translation/__init__.py:95
tests.admin_changelist.admin.GroupAdmin — tests/admin_changelist/admin.py:106
tests.admin_changelist.tests.ChangeListTests.test_distinct_for_m2m_to_inherited_in_list_filter — tests/admin_changelist/tests.py:627
tests.admin_changelist.tests.ChangeListTests.test_distinct_for_through_m2m_at_second_level_in_list_filter — tests/admin_changelist/tests.py:578
tests.admin_changelist.tests.ChangeListTests.test_pagination_page_range — tests/admin_changelist/tests.py:1624
tests.admin_changelist.tests.ChangeListTests.test_show_all — tests/admin_changelist/tests.py:1133
### Two-seed thin-pool case (A6.4 flagship)

This is retained as the thin-pool comparison: the two-seed pair admits little
optional context, so its lower utilisation is not representative of the
six-seed harness. The seeds are `django.db.models.query.QuerySet.filter` and
`django.db.models.sql.query.Query.build_filter`. The complete emitted context
follows.

Compiled context · 22 symbols · 4,715 / 8,000 tokens
20 declarations · 2 bodies · 24 identities
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

# --- optional context (1) ---
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
django.db.models.query.QuerySet._chain — django/db/models/query.py:2244
django.db.models.query_utils.check_rel_lookup_compatibility — django/db/models/query_utils.py:482
django.db.models.query_utils.refs_expression — django/db/models/query_utils.py:469
django.db.models.sql.query.JoinPromoter — django/db/models/sql/query.py:2809
django.db.models.sql.query.JoinPromoter.add_votes — django/db/models/sql/query.py:2836
django.db.models.sql.query.JoinPromoter.update_join_types — django/db/models/sql/query.py:2843
django.db.models.sql.query.Query.add_filter — django/db/models/sql/query.py:1660
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
