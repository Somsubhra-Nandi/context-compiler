"""PLACEHOLDER(item-8) seed resolution: exact, suffix, ambiguous, missing. No HydraDB."""
from __future__ import annotations

import pytest

from context_compiler.graph.sidecar import SymbolMeta
from context_compiler.mcp.seeds import (
    SeedResolutionError,
    resolve_seed,
    resolve_seeds,
    resolve_task,
)

BY_FQN = {
    "django.db.models.query.QuerySet.filter": 1,
    "django.db.models.query.QuerySet._filter_or_exclude": 2,
    "django.db.models.sql.query.Query.build_filter": 3,
    "django.contrib.admin.options.QuerySet.filter": 4,  # deliberate suffix collision
    "django.db.models.sql.query.Query.add_q": 5,
}


def _meta(node: int, fqn: str) -> SymbolMeta:
    return SymbolMeta(
        fqn=fqn,
        kind="method",
        file="x.py",
        repr_L2_tokens=1,
        repr_L3_tokens=1,
        repr_L2_refs=(),
        repr_L3_refs=(),
        identity_tokens=1,
        provenance_tokens=1,
        evaluable=None,
    )


SIDECAR = {node: _meta(node, fqn) for fqn, node in BY_FQN.items()}


# -- exact ----------------------------------------------------------------


def test_resolve_seed_exact_fqn():
    assert resolve_seed("django.db.models.sql.query.Query.add_q", BY_FQN) == 5


# -- unique suffix ----------------------------------------------------------


def test_resolve_seed_unique_suffix():
    assert resolve_seed("Query.build_filter", BY_FQN) == 3
    assert resolve_seed("build_filter", BY_FQN) == 3


def test_resolve_seed_suffix_must_be_dotted_boundary():
    # "ilter" is a substring of "filter" but not a dotted suffix -- must not match.
    with pytest.raises(SeedResolutionError):
        resolve_seed("ilter", BY_FQN)


# -- ambiguous --------------------------------------------------------------


def test_resolve_seed_ambiguous_names_the_matches():
    with pytest.raises(SeedResolutionError) as exc:
        resolve_seed("QuerySet.filter", BY_FQN)
    message = str(exc.value)
    assert "ambiguous" in message
    assert "django.db.models.query.QuerySet.filter" in message
    assert "django.contrib.admin.options.QuerySet.filter" in message


# -- missing ------------------------------------------------------------


def test_resolve_seed_missing_names_the_query():
    with pytest.raises(SeedResolutionError) as exc:
        resolve_seed("TokenPolicy.rotate", BY_FQN)
    assert "TokenPolicy.rotate" in str(exc.value)


# -- resolve_seeds: batches, reports every failure together ------------------


def test_resolve_seeds_all_succeed():
    ids = resolve_seeds(["Query.add_q", "build_filter"], BY_FQN)
    assert ids == [5, 3]


def test_resolve_seeds_reports_every_failure():
    with pytest.raises(SeedResolutionError) as exc:
        resolve_seeds(["nope", "QuerySet.filter", "Query.add_q"], BY_FQN)
    message = str(exc.value)
    assert "nope" in message
    assert "ambiguous" in message


# -- task resolution ----------------------------------------------------


def test_resolve_task_ranks_by_token_overlap():
    ids = resolve_task("how does QuerySet filter build the query", SIDECAR, top_k=6)
    assert ids
    # QuerySet.filter shares "queryset" and "filter" with the task -- top hit(s).
    top_fqns = {SIDECAR[n].fqn for n in ids[:2]}
    assert "django.db.models.query.QuerySet.filter" in top_fqns


def test_resolve_task_caps_at_top_k():
    ids = resolve_task("query", SIDECAR, top_k=2)
    assert len(ids) <= 2


def test_resolve_task_no_match_raises():
    with pytest.raises(SeedResolutionError):
        resolve_task("zzz_nonexistent_zzz", SIDECAR)


def test_resolve_task_empty_string_raises():
    with pytest.raises(SeedResolutionError):
        resolve_task("   ", SIDECAR)
