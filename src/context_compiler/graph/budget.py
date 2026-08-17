"""The Sec 6.2 cost model and the I6 closure check.

``cost()`` charges everything the model will see, per I4:

    src    canonical emitted text for every node at L2 or L3
    prov   the provenance trailer on every emitted node (Sec 7.4)
    ident  L1-mandatory identity lines -- FQNs that appear textually in
           emitted text but are not themselves emitted. Never truncated.
    header 40 tokens of context header

The three L1 tiers of Sec 1.1 are what v1.2 got wrong, so they are spelled out
here rather than left implicit:

    L1-lattice     every node the fixpoint assigns L1. Costs 0. Never emitted.
    L1-mandatory   the subset of *anything* textually referenced by emitted
                   text that is not itself emitted. Budgeted, never truncated.
                   Note this is not a subset of the lattice: a reference to a
                   symbol the closure never reached (L0) is still a name the
                   model can see and cannot resolve, so it is charged too.
    L1-hints       everything else worth listing. Uses the 5% reserve and is
                   the only tier that may be truncated.

L1-mandatory is pure set algebra over the precomputed ``repr_L2_refs`` /
``repr_L3_refs`` lists in the sidecar: no round trips and no source reads. It
is an upper bound, because emission-time deduplication can only shrink it.

``CostState`` is the same model maintained incrementally. Sec 6.3's packer
evaluates a bundle per candidate per iteration, and recomputing the dangling
set from scratch each time is the difference between a compile that takes
milliseconds and one that takes seconds. ``test_cost_state_agrees_with_cost``
pins the two implementations against each other.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from .closure import PROPAGATION, L0, L1, L2, L3, Level
from .expand import EnvelopeMiss
from .sidecar import SymbolMeta

#: Fraction of the budget held back for truncatable L1-hints (Sec 6.2).
HINT_RESERVE = 0.05

#: Cost of the context header itself. Charged once, per I4 -- the model sees it.
HEADER_TOKENS = 40

EMPTY: tuple[int, ...] = ()


def refs_at(meta: SymbolMeta, level: Level) -> tuple[int, ...]:
    """Symbols named textually by ``meta``'s canonical text at ``level``.

    L1 and L0 emit no text, so they name nothing. There is deliberately no
    assumption that ``repr_L3_refs`` contains ``repr_L2_refs``.
    """
    if level >= L3:
        return meta.repr_L3_refs
    if level == L2:
        return meta.repr_L2_refs
    return EMPTY


def source_tokens(meta: SymbolMeta, level: Level) -> int:
    """The ``src`` term for one node. L1 and L0 cost nothing."""
    if level >= L3:
        return meta.repr_L3_tokens
    if level == L2:
        return meta.repr_L2_tokens
    return 0


def cost(levels: Mapping[int, Level], sidecar: Mapping[int, SymbolMeta]) -> int:
    """Sec 6.2's ``cost()``, computed from scratch. The reference implementation."""
    src = 0
    prov = 0
    emitted: set[int] = set()
    referenced: set[int] = set()
    for node, level in levels.items():
        meta = sidecar.get(node)
        if meta is None:
            continue
        src += source_tokens(meta, level)
        if level >= L2:
            prov += meta.provenance_tokens
            emitted.add(node)
        referenced.update(refs_at(meta, level))
    ident = 0
    for node in referenced - emitted:
        meta = sidecar.get(node)
        if meta is not None:
            ident += meta.identity_tokens
    return src + prov + ident + HEADER_TOKENS


def mandatory_identities(
    levels: Mapping[int, Level], sidecar: Mapping[int, SymbolMeta]
) -> set[int]:
    """The L1-mandatory tier: referenced textually, not itself emitted."""
    emitted: set[int] = set()
    referenced: set[int] = set()
    for node, level in levels.items():
        meta = sidecar.get(node)
        if meta is None:
            continue
        if level >= L2:
            emitted.add(node)
        referenced.update(refs_at(meta, level))
    return {n for n in referenced - emitted if n in sidecar}


class CostState:
    """Sec 6.2's cost model, maintained incrementally under rising levels.

    Holds the running ``src``/``prov``/``ident`` terms plus a reference count
    per symbol, so ``delta_cost()`` touches only the nodes a bundle changes
    instead of re-unioning every ref list in the context.
    """

    __slots__ = ("sidecar", "levels", "_emitted", "_refcount", "_src", "_prov", "_ident")

    def __init__(
        self, levels: Mapping[int, Level], sidecar: Mapping[int, SymbolMeta]
    ) -> None:
        self.sidecar = sidecar
        self.levels: dict[int, Level] = {}
        self._emitted: set[int] = set()
        self._refcount: Counter[int] = Counter()
        self._src = 0
        self._prov = 0
        self._ident = 0
        if levels:
            self.apply(levels)

    # -- reading ---------------------------------------------------------

    def total(self) -> int:
        return self._src + self._prov + self._ident + HEADER_TOKENS

    @property
    def emitted(self) -> set[int]:
        return self._emitted

    def dangling(self) -> set[int]:
        """The current L1-mandatory set."""
        return {n for n, c in self._refcount.items() if c > 0 and n not in self._emitted}

    # -- delta arithmetic ------------------------------------------------

    def _terms(
        self, delta: Mapping[int, Level]
    ) -> tuple[int, int, int, Counter[int]]:
        d_src = 0
        d_prov = 0
        ref_delta: Counter[int] = Counter()
        rising: dict[int, Level] = {}

        for node, new in delta.items():
            new = Level(new)
            old = self.levels.get(node, L0)
            if new <= old:
                continue
            meta = self.sidecar.get(node)
            if meta is None:
                continue
            rising[node] = new
            d_src += source_tokens(meta, new) - source_tokens(meta, old)
            if new >= L2 and old < L2:
                d_prov += meta.provenance_tokens
            for r in refs_at(meta, old):
                ref_delta[r] -= 1
            for r in refs_at(meta, new):
                ref_delta[r] += 1

        # Only symbols whose ref count or emitted-ness changed can move in or
        # out of the dangling set, so the identity term is O(|delta| x refs).
        touched = set(ref_delta)
        touched.update(n for n, lv in rising.items() if lv >= L2)
        d_ident = 0
        for r in touched:
            meta = self.sidecar.get(r)
            if meta is None:
                continue
            before_count = self._refcount.get(r, 0)
            was = before_count > 0 and r not in self._emitted
            after_count = before_count + ref_delta.get(r, 0)
            now_emitted = r in self._emitted or rising.get(r, L0) >= L2
            now = after_count > 0 and not now_emitted
            if was and not now:
                d_ident -= meta.identity_tokens
            elif now and not was:
                d_ident += meta.identity_tokens
        return d_src, d_prov, d_ident, ref_delta

    def delta_cost(self, delta: Mapping[int, Level]) -> int:
        """Token cost of admitting ``delta``. Pure -- does not mutate."""
        d_src, d_prov, d_ident, _ = self._terms(delta)
        return d_src + d_prov + d_ident

    def apply(self, delta: Mapping[int, Level]) -> int:
        """Admit ``delta`` and return what it cost."""
        d_src, d_prov, d_ident, ref_delta = self._terms(delta)
        for node, new in delta.items():
            new = Level(new)
            old = self.levels.get(node, L0)
            if new <= old:
                continue
            if node not in self.sidecar:
                continue
            self.levels[node] = new
            if new >= L2:
                self._emitted.add(node)
        for r, n in ref_delta.items():
            if n:
                self._refcount[r] += n
        self._src += d_src
        self._prov += d_prov
        self._ident += d_ident
        return d_src + d_prov + d_ident


# -- I6 ------------------------------------------------------------------


def is_closed(
    levels: Mapping[int, Level],
    expand: object,
    profile: object | None = None,
) -> bool:
    """Invariant I6: ``levels`` satisfies every profile-adjusted rule it fires.

    ``expand`` is an edge oracle -- a ``CachingExpander`` or ``FrozenExpander``
    carrying the edges already read. A node at L2 or above whose out-edges were
    never read cannot be certified, so this returns False rather than assuming
    it is a leaf. That is the whole point: the header must stop lying.
    """
    sources = [n for n, lv in levels.items() if lv >= L2]
    if not sources:
        return True
    try:
        edges = expand(sources)
    except EnvelopeMiss:
        return False
    for src, edge_type, dst in edges:
        rules = PROPAGATION.get(edge_type)
        if rules is None:
            continue
        required = rules[levels[src]]
        if profile is not None:
            required = profile.adjust(edge_type, required)
        if required > levels.get(dst, L0):
            return False
    return True


def unclosed_edges(
    levels: Mapping[int, Level],
    expand: object,
    profile: object | None = None,
) -> list[tuple[int, str, int, Level, Level]]:
    """Every rule ``levels`` violates. Empty iff ``is_closed`` holds."""
    out = []
    sources = [n for n, lv in levels.items() if lv >= L2]
    if not sources:
        return out
    for src, edge_type, dst in expand(sources):
        rules = PROPAGATION.get(edge_type)
        if rules is None:
            continue
        required = rules[levels[src]]
        if profile is not None:
            required = profile.adjust(edge_type, required)
        have = levels.get(dst, L0)
        if required > have:
            out.append((src, edge_type, dst, required, have))
    return out


# -- L1-hints (Sec 1.1, Sec 7.3) -----------------------------------------


class HintIndex:
    """The truncatable identity index. The only tier allowed to lose entries."""

    __slots__ = ("nodes", "tokens", "truncated", "considered")

    def __init__(
        self, nodes: list[int], tokens: int, truncated: bool, considered: int
    ) -> None:
        self.nodes = nodes
        self.tokens = tokens
        self.truncated = truncated
        self.considered = considered

    def __len__(self) -> int:
        return len(self.nodes)


def identity_hints(
    levels: Mapping[int, Level],
    sidecar: Mapping[int, SymbolMeta],
    cap: int,
    extra: Iterable[int] = (),
    rank: Mapping[int, float] | None = None,
) -> HintIndex:
    """Build the L1-hints index, truncated to ``cap`` tokens.

    Eligible: L1-lattice members and ``extra`` (unadmitted packing candidates)
    that are not already charged as L1-mandatory and are not emitted. Anything
    already charged is not a hint -- charging it twice would double-count
    against I4.

    Ranked by ``rank`` where supplied, then by identity cost ascending so a cap
    buys as many names as possible.
    """
    emitted = {n for n, lv in levels.items() if lv >= L2}
    charged = mandatory_identities(levels, sidecar)
    pool: set[int] = {n for n, lv in levels.items() if lv == L1}
    pool.update(extra)
    pool -= emitted
    pool -= charged
    pool = {n for n in pool if n in sidecar}

    rank = rank or {}
    ordered = sorted(
        pool, key=lambda n: (-rank.get(n, 0.0), sidecar[n].identity_tokens, n)
    )

    chosen: list[int] = []
    used = 0
    truncated = False
    for node in ordered:
        need = sidecar[node].identity_tokens
        if used + need > cap:
            truncated = True
            continue
        chosen.append(node)
        used += need
    return HintIndex(chosen, used, truncated, len(ordered))
