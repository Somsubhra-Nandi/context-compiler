"""Budget admission: the Sec 6.2 profile scan wired to Sec 6.3 packing.

``compile_context()`` is the end of Item 5. It returns a level map, provenance
and a truncatable hint index -- **no rendered text**. Emission is Item 6.

    hint_reserve = 5% of budget, held back for L1-hints
    effective    = budget - hint_reserve
    for profile in (P3, P2, P1, P0):
        if cost(closure(seeds, profile)) <= effective:
            pack the remainder, build the hints, assert I6 and I4, return
    return CLOSURE_BUDGET_EXCEEDED with the deficit

``CLOSURE_BUDGET_EXCEEDED`` is a first-class return value, never an exception.
It is also the product feature no top-k system can offer: *your task's
mandatory dependency floor is 22k tokens; it is too broad for one shot.*

**Both assertions run in production, not only in CI.** The point of I6 is that
the header stops lying, and a check that only runs under pytest cannot deliver
that.

Round trips, for the acceptance gate:

    12   mandatory closure of the seeds (Sec 5.1, two productive hops)
     6   reverse CALLS, one per seed -- no batched form exists (A3.1)
     6   candidate envelope, one hop, because an L2 candidate propagates only
          to L1 and L1 is terminal
    ---
    24   total, at six seeds and a candidate pool under B

The envelope obeys the same Sec 5.1 chunking as any other frontier read, so it
is `6 * ceil(|candidates|/B)`, not a flat 6. On 200 Django trials that is 6 on
199 of them; the one trial with a 784-candidate pool paid 12 and totalled 30.
The gate is a median, and the median is exactly 24.

Demotion is free: every profile below P3 has a pointwise-smaller node set, so
its closure is served entirely from the expansion cache.

**Known non-optimality, accepted (Sec 6.3).** The scan takes the first profile
whose *mandatory floor* fits and then packs the remainder. It never asks
whether a lower profile with a richer optional set would have scored better --
a P2 floor leaves more room for bundles than a P3 floor does, and on some
tasks that trade would win. Sec 6.2 does not search that space, and neither
does this. Documented, not fixed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .budget import (
    HINT_RESERVE,
    CostState,
    HintIndex,
    cost,
    identity_hints,
    is_closed,
    mandatory_identities,
    unclosed_edges,
)
from .closure import L2, L3, ClosureResult, Level, Reason, closure
from .expand import CachingExpander, ReverseReader
from .pack import (
    CANDIDATE_SOURCES,
    Candidate,
    CandidateSource,
    DiscoveryContext,
    PackReport,
    build_candidates,
    pack,
)
from .profiles import PROFILES, Profile
from .sidecar import SymbolMeta

OK = "OK"
EXCEEDED = "CLOSURE_BUDGET_EXCEEDED"


@dataclass
class CompileStats:
    """Everything the acceptance gate and the results doc ask to be reported."""

    round_trips: int = 0
    closure_round_trips: int = 0
    discovery_round_trips: int = 0
    envelope_round_trips: int = 0
    profiles_tried: int = 0
    candidates: int = 0
    bundles_evaluated: int = 0
    admitted: int = 0
    floor_cost: int = 0
    packed_cost: int = 0
    floor_symbols: int = 0
    floor_emitted: int = 0
    mandatory_identities: int = 0
    mandatory_identity_tokens: int = 0
    seconds: float = 0.0


@dataclass
class Context:
    """The Item 5 deliverable: a level map plus hints. No rendered text."""

    status: str
    budget: int
    levels: dict[int, Level] = field(default_factory=dict)
    provenance: dict[int, list[Reason]] = field(default_factory=dict)
    seeds: dict[int, Level] = field(default_factory=dict)
    profile: Profile | None = None
    hints: HintIndex | None = None
    cost: int = 0
    hint_tokens: int = 0
    deficit: int = 0
    suggestion: str = ""
    pack_report: PackReport | None = None
    stats: CompileStats = field(default_factory=CompileStats)

    @property
    def ok(self) -> bool:
        return self.status != EXCEEDED

    @property
    def demoted(self) -> bool:
        return self.status.startswith("DEMOTED")

    def emitted(self) -> set[int]:
        return {n for n, lv in self.levels.items() if lv >= L2}

    def total_tokens(self) -> int:
        return self.cost + self.hint_tokens

    def utilisation(self) -> float:
        return self.total_tokens() / self.budget if self.budget else 0.0

    def __str__(self) -> str:  # pragma: no cover - reporting only
        if not self.ok:
            return f"{self.status}  deficit {self.deficit:,} tokens"
        return (
            f"{self.status}  {len(self.emitted())} emitted / {len(self.levels)} closure  "
            f"{self.total_tokens():,}/{self.budget:,} tokens  "
            f"{self.stats.round_trips} round trips"
        )


@dataclass
class Compiler:
    """Binds the graph, the sidecar and the candidate sources for one repo.

    Constructed once and reused; ``compile_context`` is stateless across calls
    apart from the round-trip counters on the readers it is handed.
    """

    sidecar: Mapping[int, SymbolMeta]
    expander: object
    reverse: object | None = None
    degrees: Mapping[int, int] = field(default_factory=dict)
    sources: Sequence[CandidateSource] = CANDIDATE_SOURCES
    profiles: Sequence[Profile] = PROFILES

    @property
    def n_symbols(self) -> int:
        return len(self.sidecar)

    def compile_context(self, task_seeds, budget: int) -> Context:
        """Sec 6.2's admission scan. Returns a level map plus hints."""
        t0 = time.perf_counter()
        seeds = list(task_seeds)
        hint_reserve = int(budget * HINT_RESERVE)
        effective = budget - hint_reserve

        cache = CachingExpander(self.expander)
        stats = CompileStats()
        before_expand = _round_trips(self.expander)
        before_reverse = _round_trips(self.reverse)

        floors: dict[str, ClosureResult] = {}
        for profile in self.profiles:
            stats.profiles_tried += 1
            result = closure({s: profile.seed_level for s in seeds}, cache, profile)
            floors[profile.name] = result
            floor = cost(result.levels, self.sidecar)
            stats.closure_round_trips = _round_trips(self.expander) - before_expand

            if floor > effective:
                continue

            return self._pack_and_finish(
                seeds, budget, effective, floor, profile, result, cache,
                stats, before_expand, before_reverse, t0,
            )

        # Nothing fits, not even the floor profile.
        last = floors[self.profiles[-1].name]
        floor = cost(last.levels, self.sidecar)
        stats.round_trips = (_round_trips(self.expander) - before_expand) + (
            _round_trips(self.reverse) - before_reverse
        )
        stats.floor_cost = floor
        stats.floor_symbols = len(last.levels)
        stats.floor_emitted = len({n for n, lv in last.levels.items() if lv >= L2})
        stats.seconds = time.perf_counter() - t0
        return Context(
            status=EXCEEDED,
            budget=budget,
            levels=dict(last.levels),
            provenance=dict(last.provenance),
            seeds={s: self.profiles[-1].seed_level for s in seeds},
            profile=self.profiles[-1],
            cost=floor,
            deficit=floor - effective,
            suggestion="narrow the task or raise the budget",
            stats=stats,
        )

    # -- the admitted path -----------------------------------------------

    def _pack_and_finish(
        self, seeds, budget, effective, floor, profile, result, cache,
        stats, before_expand, before_reverse, t0,
    ) -> Context:
        levels = dict(result.levels)
        provenance = {n: list(v) for n, v in result.provenance.items()}
        state = CostState(levels, self.sidecar)
        assert state.total() == floor, (state.total(), floor)

        stats.floor_cost = floor
        stats.floor_symbols = len(levels)
        stats.floor_emitted = len({n for n, lv in levels.items() if lv >= L2})

        candidates = self._discover(seeds, cache, levels)
        stats.discovery_round_trips = _round_trips(self.reverse) - before_reverse
        stats.candidates = len(candidates)

        # The envelope: one batched pass over every candidate, after which
        # packing touches the database zero times.
        envelope_before = _round_trips(self.expander)
        self._envelope(candidates, cache)
        stats.envelope_round_trips = _round_trips(self.expander) - envelope_before

        report = pack(
            remaining=effective - floor,
            levels=levels,
            provenance=provenance,
            candidates=candidates,
            state=state,
            expand=cache.frozen(),
            profile=profile,
        )
        stats.bundles_evaluated = report.evaluated
        stats.admitted = report.admitted_count

        merged_cost = state.total()
        assert merged_cost == cost(levels, self.sidecar), "incremental cost drifted"

        admitted_nodes = {a.node for a in report.admitted}
        hints = identity_hints(
            levels,
            self.sidecar,
            cap=budget - effective,  # the 5% reserve, and only hints may use it
            extra=[c.node for c in candidates if c.node not in admitted_nodes],
            rank={c.node: c.score for c in candidates},
        )

        # I6 and I4, in production. Not CI-only assertions.
        assert is_closed(levels, cache.frozen(), profile), unclosed_edges(
            levels, cache.frozen(), profile
        )[:5]
        assert merged_cost + hints.tokens <= budget, (merged_cost, hints.tokens, budget)

        mandatory = mandatory_identities(levels, self.sidecar)
        stats.mandatory_identities = len(mandatory)
        stats.mandatory_identity_tokens = sum(
            self.sidecar[n].identity_tokens for n in mandatory
        )
        stats.packed_cost = merged_cost
        stats.round_trips = (_round_trips(self.expander) - before_expand) + (
            _round_trips(self.reverse) - before_reverse
        )
        stats.seconds = time.perf_counter() - t0

        return Context(
            status=OK if profile is self.profiles[0] else f"DEMOTED:{profile.name}",
            budget=budget,
            levels=levels,
            provenance=provenance,
            seeds={s: profile.seed_level for s in seeds},
            profile=profile,
            hints=hints,
            cost=merged_cost,
            hint_tokens=hints.tokens,
            pack_report=report,
            stats=stats,
        )

    # -- candidate discovery and envelope --------------------------------

    def _discover(self, seeds, cache, levels) -> list[Candidate]:
        if self.reverse is None:
            return []
        ctx = DiscoveryContext(reverse=self.reverse, edges=cache.edges, sidecar=self.sidecar)
        emitted = {n for n, lv in levels.items() if lv >= L2}
        return build_candidates(
            seeds=seeds,
            sources=self.sources,
            ctx=ctx,
            degrees=self.degrees,
            n_symbols=self.n_symbols,
            exclude=emitted,
        )

    def _envelope(self, candidates: Sequence[Candidate], cache: CachingExpander) -> None:
        """Fetch every candidate's mandatory neighbourhood in one pass.

        Depth is per admission level: an L2 candidate propagates to L1 and
        stops, so one hop suffices; an L3 candidate (Item 9's covering tests)
        needs two. Anything the packer can reach is therefore in the cache, and
        ``FrozenExpander`` can safely raise on a miss.
        """
        for depth_level in (L2, L3):
            nodes = [c.node for c in candidates if c.level == depth_level]
            if not nodes:
                continue
            hop1 = cache(nodes)
            if depth_level >= L3:
                onward = sorted({dst for _s, _e, dst in hop1})
                if onward:
                    cache(onward)


def _round_trips(reader: object) -> int:
    if reader is None:
        return 0
    stats = getattr(reader, "stats", None)
    if stats is not None:
        return stats.round_trips
    return getattr(reader, "round_trips", 0)


def compile_context(compiler: Compiler, task_seeds, budget: int) -> Context:
    """Module-level convenience mirroring the Sec 6.2 signature."""
    return compiler.compile_context(task_seeds, budget)
