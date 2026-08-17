"""Optional packing as closure bundles (spec Sec 6.3, invariant I6).

**Nothing is emitted at L2 or L3 without its induced mandatory closure.** An
optional item is admitted as a *bundle* -- itself plus every node the
propagation rules then require -- or not at all. That is the whole of I6, and
it is why ``value = score / delta_cost`` divides by the bundle's cost rather
than the candidate's own token count, which is what v1.1 got wrong.

Two things keep this affordable:

* **The candidate envelope.** Every candidate's out-edges are fetched in one
  batched pass before packing starts, so each of the thousands of bundle
  evaluations in a compile is in-memory set algebra against a
  ``FrozenExpander`` that raises rather than touching the database. A candidate
  admitted at L2 needs exactly *one* hop of edges, because the Sec 4 table
  sends an L2 source to L1 and L1 is terminal -- so the envelope costs 6
  requests, not the 12 Sec 6.3 budgeted for it.

* **Incremental cost.** ``CostState.delta_cost`` is O(|delta|) rather than
  O(|context|).

The lazy-greedy fallback Sec 6.3 keeps in reserve is not needed at Django's
observed pool sizes and is not implemented; see the results doc.

Ranking is kept strictly separate from inclusion. A score never forces a node
into the context; a mandatory rule never reads a score. A bundle's members are
included by *rule*, triggered by a candidate that scoring merely proposed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .budget import CostState
from .closure import L0, L2, L3, Level, Reason, induced_delta
from .sidecar import SymbolMeta


# -- candidate sources ---------------------------------------------------


@dataclass(frozen=True)
class CandidateSource:
    """One way of proposing optional context.

    Sec 6.3 names four. Two are static and available now; two need Item 9's
    runtime evidence and are declared here with ``available=False`` so the
    pool is a list to append to rather than a shape to restructure.

    ``level`` is the type-specific admission level -- a covering test at L2 is
    useless, so it is admitted at L3. ``confidence`` reads the Sec 2.4 evidence
    state; static structure is certain, so it is 1.0.
    """

    name: str
    level: Level
    base_weight: float
    confidence: float
    available: bool = True

    def find(self, seeds, ctx) -> dict[int, tuple[int, ...]]:
        """Map candidate id -> the seeds it is linked to. Zero for unavailable."""
        raise NotImplementedError


@dataclass(frozen=True)
class StaticCallerSource(CandidateSource):
    """Callers of a seed, reached by reverse ``CALLS``. Admitted at L2.

    Ranked by path proximity: a direct caller of a seed is as close as static
    structure gets, so ``base_weight`` is 1.0 and a caller of several seeds
    scores higher through the multiplicity term in ``relevance()``.

    **This is the source the engine constraint bites.** There is no batched
    reverse read (A3.1), so discovery costs one round trip per seed.
    """

    name: str = "static_caller"
    level: Level = L2
    base_weight: float = 1.0
    confidence: float = 1.0

    def find(self, seeds, ctx) -> dict[int, tuple[int, ...]]:
        out: dict[int, list[int]] = {}
        for seed in seeds:
            for caller in ctx.reverse.read("CALLS", seed):
                out.setdefault(caller, []).append(seed)
        return {n: tuple(v) for n, v in out.items()}


@dataclass(frozen=True)
class SiblingImplementationSource(CandidateSource):
    """Other implementations of an interface a seed implements. Admitted at L2.

    Ranked by shared-interface proximity, which is weaker evidence than being
    a direct caller, hence the lower ``base_weight``.

    The forward ``IMPLEMENTS`` edges out of the seeds are already in the
    closure's edge cache, so only the reverse hop off each interface costs a
    round trip. On Django's eligible seed pool this source is dormant: no
    function or method has an ``IMPLEMENTS`` out-edge, since the extraction
    layer emits ``IMPLEMENTS`` for class-to-ABC relations only.
    """

    name: str = "sibling_implementation"
    level: Level = L2
    base_weight: float = 0.7
    confidence: float = 1.0

    def find(self, seeds, ctx) -> dict[int, tuple[int, ...]]:
        interfaces: dict[int, list[int]] = {}
        for seed in seeds:
            for edge_type, dst in ctx.edges.get(seed, ()):
                if edge_type == "IMPLEMENTS":
                    interfaces.setdefault(dst, []).append(seed)
        out: dict[int, list[int]] = {}
        for iface, via in interfaces.items():
            for sibling in ctx.reverse.read("IMPLEMENTS", iface):
                if sibling in via:
                    continue
                out.setdefault(sibling, []).extend(via)
        return {n: tuple(dict.fromkeys(v)) for n, v in out.items()}


@dataclass(frozen=True)
class CoveringTestSource(CandidateSource):
    """Tests that cover a seed, via ``COVERS``. **Item 9 -- not yet available.**

    Admitted at L3: a test's assertions are the content, and its body is what
    makes it worth the tokens. This is also the case that motivated I6 -- an
    admitted test drags in its fixtures, and v1.1 emitted it without them.
    """

    name: str = "covering_test"
    level: Level = L3
    base_weight: float = 1.2
    confidence: float = 1.0
    available: bool = False

    def find(self, seeds, ctx) -> dict[int, tuple[int, ...]]:
        return {}


@dataclass(frozen=True)
class ObservedCallerSource(CandidateSource):
    """Callers seen at runtime, via ``OBSERVED_CALLS``. **Item 9 -- not yet.**

    ``confidence`` will read the Sec 2.4 evidence state rather than being
    constant: current evidence at 1.0, historical-but-intact at 0.6.
    """

    name: str = "observed_caller"
    level: Level = L2
    base_weight: float = 1.3
    confidence: float = 1.0
    available: bool = False

    def find(self, seeds, ctx) -> dict[int, tuple[int, ...]]:
        return {}


#: The pool, in the order Sec 6.3 tables it. Item 9 flips two ``available``
#: flags and implements two ``find()`` bodies; nothing else moves.
CANDIDATE_SOURCES: tuple[CandidateSource, ...] = (
    StaticCallerSource(),
    SiblingImplementationSource(),
    CoveringTestSource(),
    ObservedCallerSource(),
)


@dataclass
class DiscoveryContext:
    """What a ``CandidateSource.find()`` is allowed to read."""

    reverse: object
    edges: Mapping[int, list[tuple[str, int]]]
    sidecar: Mapping[int, SymbolMeta]


# -- scoring -------------------------------------------------------------


def idf(node: int, degrees: Mapping[int, int], n_symbols: int) -> float:
    """``log(N / (1 + degree))`` -- hub suppression (Sec 6.3).

    ``degree`` is out-degree over the hard edges. For the candidate kinds that
    exist today this is the right direction: a caller that calls 175 things
    tells you almost nothing about which of them you are looking at, while a
    caller with two callees is strong evidence about both. Django's maximum
    out-degree is 175 against 43,420 symbols, so the term spans roughly
    log(43420/1) = 10.7 down to log(43420/176) = 5.5.
    """
    return math.log(n_symbols / (1.0 + degrees.get(node, 0)))


def relevance(source: CandidateSource, links: Sequence[int]) -> float:
    """Path proximity, scaled by how many seeds the candidate touches.

    ``log(links)`` is zero at one link, so a single-seed candidate scores its
    source's base weight exactly and multiplicity is a bonus, never a penalty.
    """
    return source.base_weight * (1.0 + math.log(max(1, len(links))))


@dataclass
class Candidate:
    """A proposal. Carries its score; carries no claim to be included."""

    node: int
    source: CandidateSource
    links: tuple[int, ...]
    score: float

    @property
    def level(self) -> Level:
        return self.source.level


def build_candidates(
    seeds: Sequence[int],
    sources: Sequence[CandidateSource],
    ctx: DiscoveryContext,
    degrees: Mapping[int, int],
    n_symbols: int,
    exclude: set[int] | None = None,
) -> list[Candidate]:
    """Discover and score the candidate pool.

    A candidate already emitted in the mandatory floor is dropped: it is
    included by rule, so proposing it is a no-op. Seeds are dropped for the
    same reason.
    """
    exclude = exclude or set()
    best: dict[int, Candidate] = {}
    for source in sources:
        if not source.available:
            continue
        for node, links in source.find(seeds, ctx).items():
            if node in exclude or node in seeds or node not in ctx.sidecar:
                continue
            score = relevance(source, links) * idf(node, degrees, n_symbols) * source.confidence
            cand = Candidate(node=node, source=source, links=links, score=score)
            current = best.get(node)
            if current is None or cand.score > current.score:
                best[node] = cand
    return sorted(best.values(), key=lambda c: (-c.score, c.node))


# -- the packing loop ----------------------------------------------------


@dataclass
class Admission:
    """One admitted bundle, for the provenance trail and the results doc."""

    node: int
    source: str
    level: Level
    score: float
    delta_cost: int
    bundle_size: int
    value: float


@dataclass
class PackReport:
    candidates: int = 0
    evaluated: int = 0
    admitted: list[Admission] = field(default_factory=list)
    spent: int = 0
    remaining: int = 0
    skipped_too_large: int = 0
    iterations: int = 0

    @property
    def admitted_count(self) -> int:
        return len(self.admitted)


def pack(
    remaining: int,
    levels: dict[int, Level],
    provenance: dict[int, list[Reason]],
    candidates: Sequence[Candidate],
    state: CostState,
    expand: object,
    profile: object,
) -> PackReport:
    """Sec 6.3's greedy loop. Mutates ``levels``/``provenance``/``state``.

    Each iteration re-evaluates every remaining candidate, because admitting
    one bundle changes every other bundle's cost -- shared dependencies make
    later candidates cheaper. That is a feature: the packer clusters context
    around a coherent region of the graph rather than scattering it.

    A bundle whose ``delta_cost`` is zero or negative is admitted immediately.
    That is rare but real: raising a node from L1 to L2 removes it from the
    L1-mandatory set, and if its declaration is cheaper than the identity line
    it was already being charged for, the context gets *smaller*.
    """
    report = PackReport(candidates=len(candidates), remaining=remaining)
    pool = list(candidates)

    while pool:
        report.iterations += 1
        best: Candidate | None = None
        best_delta: dict[int, Level] | None = None
        best_prov: dict[int, list[Reason]] = {}
        best_cost = 0
        best_value = float("-inf")
        still: list[Candidate] = []

        for cand in pool:
            if levels.get(cand.node, L0) >= cand.level:
                continue  # already included by rule; nothing to propose
            report.evaluated += 1
            delta, prov = induced_delta(levels, {cand.node: cand.level}, expand, profile)
            dcost = state.delta_cost(delta)
            if dcost > remaining:
                report.skipped_too_large += 1
                still.append(cand)
                continue
            still.append(cand)
            value = float("inf") if dcost <= 0 else cand.score / dcost
            if value > best_value:
                best, best_delta, best_prov, best_cost, best_value = (
                    cand,
                    delta,
                    prov,
                    dcost,
                    value,
                )

        if best is None:
            report.remaining = remaining
            return report

        # I6: the bundle goes in whole, or not at all.
        state.apply(best_delta)
        levels.update(best_delta)
        for node, reasons in best_prov.items():
            provenance.setdefault(node, []).extend(reasons)
        provenance.setdefault(best.node, []).append(
            Reason(
                via=best.links[0] if best.links else best.node,
                edge=f"OPTIONAL:{best.source.name}",
                rule=f"packed({best.source.name})->{Level(best.level).name}",
            )
        )
        remaining -= best_cost
        report.spent += best_cost
        report.admitted.append(
            Admission(
                node=best.node,
                source=best.source.name,
                level=best.level,
                score=best.score,
                delta_cost=best_cost,
                bundle_size=len(best_delta),
                value=best_value,
            )
        )
        pool = [c for c in still if c.node != best.node]

    report.remaining = remaining
    return report
