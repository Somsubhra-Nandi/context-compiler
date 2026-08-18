"""Item 8 seed resolution tests. No HydraDB."""
from __future__ import annotations

import pytest

from context_compiler.graph.sidecar import SymbolMeta
from context_compiler.graph.budget import is_closed
from context_compiler.graph.closure import L3, closure
from context_compiler.graph.expand import CachingExpander
from context_compiler.mcp.seeds import (
    CONNECTIVITY_CAP,
    rerank_connectivity,
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
    "django.db.models.sql.query.Query": 6,
}


def _meta(
    node: int,
    fqn: str,
    *,
    file: str = "x.py",
    start_line: int = 1,
    end_line: int = 10,
    kind: str = "method",
) -> SymbolMeta:
    return SymbolMeta(
        fqn=fqn,
        kind=kind,
        file=file,
        repr_L2_tokens=1,
        repr_L3_tokens=1,
        repr_L2_refs=(),
        repr_L3_refs=(),
        identity_tokens=1,
        provenance_tokens=1,
        evaluable=None,
        start_line=start_line,
        end_line=end_line,
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


def test_traceback_frames_are_returned_innermost_first_and_recursive_frames_dedupe():
    sidecar = {
        10: _meta(10, "pkg.query.outer", file="pkg/query.py", start_line=1, end_line=30),
        11: _meta(11, "pkg.query.inner", file="pkg/query.py", start_line=31, end_line=45),
    }
    diagnostics: list[str] = []
    task = '''Traceback (most recent call last):
  File "/checkout/pkg/query.py", line 5, in outer
  File "/checkout/pkg/query.py", line 35, in inner
  File "/not/indexed.py", line 8, in missing
  File "/checkout/pkg/query.py", line 35, in inner
'''
    assert resolve_task(task, sidecar, diagnostics=diagnostics) == [11, 10]
    assert any("file is not indexed" in message for message in diagnostics)


def test_traceback_frame_inside_indexed_file_but_no_symbol_is_recorded_and_not_an_error():
    sidecar = {10: _meta(10, "pkg.query.outer", file="pkg/query.py", start_line=1, end_line=10)}
    diagnostics: list[str] = []
    task = '  File "/checkout/pkg/query.py", line 99, in outer\n'
    assert resolve_task(task, sidecar, diagnostics=diagnostics) == []
    assert any("no known symbol" in message for message in diagnostics)


class _Reverse:
    def __init__(self, callers):
        self.callers = callers
        self.calls: list[tuple[str, int]] = []

    def read(self, edge_type: str, node: int) -> list[int]:
        self.calls.append((edge_type, node))
        return list(self.callers.get(node, ()))


def test_connectivity_rerank_prefers_two_hop_candidate_component_and_is_bounded():
    sidecar = {
        node: _meta(node, f"pkg.symbol{node}") for node in range(1, 23)
    }
    reverse = _Reverse({1: [2], 2: [3], 3: [4]})
    ordered = rerank_connectivity(
        list(range(1, 23)),
        sidecar=sidecar,
        reverse=reverse,
        in_degrees={},
    )
    assert len(ordered) == CONNECTIVITY_CAP
    assert ordered[:4] == [2, 3, 1, 4]
    assert len(reverse.calls) == CONNECTIVITY_CAP


def test_connectivity_rerank_uses_the_shared_hub_policy():
    sidecar = {1: _meta(1, "pkg.one"), 2: _meta(2, "pkg.two")}
    reverse = _Reverse({1: [2]})
    ordered = rerank_connectivity(
        [1, 2], sidecar=sidecar, reverse=reverse, in_degrees={1: 501}
    )
    assert ordered == [1, 2]
    assert reverse.calls == [("CALLS", 2)]


def test_connectivity_rerank_is_deterministic_on_ties():
    sidecar = {1: _meta(1, "pkg.zed"), 2: _meta(2, "pkg.aaa")}
    graph = {1: [], 2: []}
    assert rerank_connectivity([1, 2], sidecar=sidecar, graph=graph) == [2, 1]
    assert rerank_connectivity([1, 2], sidecar=sidecar, graph=graph) == [2, 1]


def test_class_seed_query_has_bounded_closed_zero_call_closure():
    query = resolve_seed("Query", BY_FQN)
    cache = CachingExpander(lambda frontier: [(n, "CALLS", 999) for n in frontier if n != query])
    result = closure({query: L3}, cache)
    assert result.levels == {query: L3}
    assert len(result) <= 1
    assert is_closed(result.levels, cache)
