"""Baseline Arm B: graph-ranked top-k without structural closure.

Arm B deliberately shares the graph's edge reader, IDF scorer and cost model
with the compiler, but has its own entry point. It reads one undirected graph
hop from the seeds (forward hard edges plus reverse reads over those same edge
types), ranks the resulting neighbours by the same ``idf`` used by
``impact_cone``, and greedily admits L2 declarations until the budget fills.
It never calls ``closure()`` and never applies a profile's level propagation.

The resulting context is useful precisely because it is *not* certified
closed: the same emitter and cost model make the unresolved/identity-only
references visible in a controlled comparison.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..emit.render import ContextLike
from ..graph.budget import HintIndex, cost, mandatory_identities
from ..graph.closure import L2, L3, Level, PROPAGATION, Reason
from ..graph.expand import HARD_EDGES
from ..graph.pack import idf
from ..graph.sidecar import SymbolMeta

OK = "OK"
EXCEEDED = "CLOSURE_BUDGET_EXCEEDED"


@dataclass(frozen=True)
class ArmBProfile:
    """Display metadata consumed by ``emit()``'s ContextLike protocol."""

    name: str = "ARM_B"
    label: str = "GRAPH TOP-K, NO CLOSURE"


@dataclass
class ArmBStats:
    """Measurements needed for the side-by-side report."""

    round_trips: int = 0
    expansion_round_trips: int = 0
    candidates: int = 0
    admitted: int = 0
    skipped_too_large: int = 0
    floor_cost: int = 0
    seconds: float = 0.0


@dataclass
class ArmBContext(ContextLike):
    """The Arm B context, intentionally compatible with ``emit()``."""

    status: str
    budget: int
    levels: dict[int, Level] = field(default_factory=dict)
    provenance: dict[int, list[Reason]] = field(default_factory=dict)
    seeds: dict[int, Level] = field(default_factory=dict)
    profile: ArmBProfile = field(default_factory=ArmBProfile)
    hints: HintIndex | None = None
    cost: int = 0
    hint_tokens: int = 0
    deficit: int = 0
    suggestion: str = ""
    stats: ArmBStats = field(default_factory=ArmBStats)

    @property
    def ok(self) -> bool:
        return self.status != EXCEEDED

    def emitted(self) -> set[int]:
        return {node for node, level in self.levels.items() if level >= L2}

    def total_tokens(self) -> int:
        return self.cost + self.hint_tokens

    def utilisation(self) -> float:
        return self.total_tokens() / self.budget if self.budget else 0.0


def _round_trips(reader: object) -> int:
    stats = getattr(reader, "stats", None)
    if stats is not None:
        return int(getattr(stats, "round_trips", 0))
    return int(getattr(reader, "round_trips", 0))


def _ranked_neighbours(
    seeds: Sequence[int],
    rows: Sequence[tuple[int, str, int]],
    sidecar: Mapping[int, SymbolMeta],
    degrees: Mapping[int, int],
) -> list[tuple[int, tuple[tuple[int, str], ...], float]]:
    """Deduplicate one-hop neighbours and rank them by the shared IDF score."""
    seed_set = set(seeds)
    by_node: dict[int, list[tuple[int, str]]] = {}
    propagation_edges = set(PROPAGATION)
    for src, edge_type, dst in rows:
        # The production expander returns hard edges, but fixture/custom
        # expanders may include display-only relations. Evidence and
        # INHERITS_FROM edges must not leak into the arm.
        if edge_type not in HARD_EDGES or edge_type not in propagation_edges or src not in seed_set:
            continue
        if dst in seed_set or dst not in sidecar:
            continue
        by_node.setdefault(dst, []).append((src, edge_type))

    ranked = []
    for node, links in by_node.items():
        # The graph ranking comparator deliberately has no closure/profile
        # term: IDF is the same relevance signal used by impact_cone.
        ranked.append((node, tuple(links), idf(node, degrees, len(sidecar))))
    return sorted(ranked, key=lambda item: (-item[2], item[0]))


def run_arm_b(
    seeds: list[int],
    sidecar: Mapping[int, SymbolMeta],
    expander: object,
    degrees: Mapping[int, int],
    reverse: object | None = None,
    budget: int = 8_000,
) -> ArmBContext:
    """Run Arm B for ``seeds`` against the supplied graph expander.

    ``expander`` has the same callable shape as ``Expander`` and ``reverse``
    has the ``ReverseReader.read(edge_type, node)`` shape. Together they read
    exactly one undirected hop. If ``reverse`` is omitted, the fixture-friendly
    forward-only form remains available. The greedy loop continues after an
    overflow so a later, smaller declaration can still be admitted.
    """
    if not isinstance(seeds, list):
        raise TypeError("Arm B seeds must be a list[int]")
    if any(not isinstance(node, int) for node in seeds):
        raise TypeError("Arm B seeds must be a list[int]")

    t0 = time.perf_counter()
    unique_seeds = list(dict.fromkeys(seeds))
    missing = [node for node in unique_seeds if node not in sidecar]
    if missing:
        raise KeyError(f"seed ids not in sidecar: {missing}")

    before_trips = _round_trips(expander)
    before_reverse_trips = _round_trips(reverse) if reverse is not None else 0
    rows = list(expander(unique_seeds)) if unique_seeds else []
    after_trips = _round_trips(expander)
    if reverse is not None:
        reverse_rows: list[tuple[int, str, int]] = []
        for seed in unique_seeds:
            for edge_type in HARD_EDGES:
                for neighbour in reverse.read(edge_type, seed):
                    reverse_rows.append((seed, edge_type, neighbour))
        rows.extend(reverse_rows)
    after_reverse_trips = _round_trips(reverse) if reverse is not None else 0
    graph_trips = (after_trips - before_trips) + (
        after_reverse_trips - before_reverse_trips
    )

    # Arm B's defining invariant. There is no call to closure(), no
    # induced_delta(), and no profile adjustment anywhere in this module.
    fixpoint_entered = False
    assert not fixpoint_entered, "Arm B must never enter the closure fixpoint"

    levels: dict[int, Level] = {node: L3 for node in unique_seeds}
    provenance: dict[int, list[Reason]] = {}
    ranked = _ranked_neighbours(unique_seeds, rows, sidecar, degrees)
    # A plain top-k retriever has no separate, truncatable identity-hint tier.
    # Arm B therefore excludes the compiler's HINT_RESERVE and gives the full
    # budget to retrieved blocks. Mandatory identity references remain charged
    # by cost() and rendered by emit(); this is the controlled difference.
    effective = budget
    floor = cost(levels, sidecar)
    stats = ArmBStats(
        expansion_round_trips=graph_trips,
        candidates=len(ranked),
        floor_cost=floor,
    )

    if floor > effective:
        stats.seconds = time.perf_counter() - t0
        stats.round_trips = graph_trips
        return ArmBContext(
            status=EXCEEDED,
            budget=budget,
            levels=levels,
            provenance=provenance,
            seeds={node: L3 for node in unique_seeds},
            cost=floor,
            deficit=floor - effective,
            suggestion="reduce the seed count or raise the budget",
            stats=stats,
        )

    for node, links, _score in ranked:
        trial = {node: L2}
        trial_levels = dict(levels)
        trial_levels.update(trial)
        trial_cost = cost(trial_levels, sidecar)
        if trial_cost > effective:
            stats.skipped_too_large += 1
            continue
        levels[node] = L2
        reasons = provenance.setdefault(node, [])
        for via, edge_type in links:
            reasons.append(
                Reason(via=via, edge=edge_type, rule="graph_top_k(L3-seed)->L2")
            )
        stats.admitted += 1

    final_cost = cost(levels, sidecar)
    hints = None
    assert final_cost <= budget
    stats.round_trips = graph_trips
    stats.seconds = time.perf_counter() - t0
    return ArmBContext(
        status=OK,
        budget=budget,
        levels=levels,
        provenance=provenance,
        seeds={node: L3 for node in unique_seeds},
        hints=hints,
        cost=final_cost,
        hint_tokens=0,
        stats=stats,
    )


def dangling_references(
    context: ArmBContext, sidecar: Mapping[int, SymbolMeta]
) -> set[int]:
    """Return identity-only references in Arm B's emitted text.

    ``mandatory_identities`` is the shared metric: it is valid for Arm B's
    non-closed level map and identifies references with no emitted declaration
    or body. The emitter renders these as identity lines; they remain dangling
    definitions, rather than silently being treated as closure members.
    """
    return mandatory_identities(context.levels, sidecar)


__all__ = [
    "ArmBContext",
    "ArmBProfile",
    "ArmBStats",
    "EXCEEDED",
    "OK",
    "dangling_references",
    "run_arm_b",
]
