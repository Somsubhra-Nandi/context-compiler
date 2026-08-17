"""The propagation table (spec Sec 4) and the least-fixpoint closure (Sec 5).

Least fixpoint over the finite lattice ``Symbol -> Level``, pointwise ordered.
Monotone rules plus a finite lattice give convergence (Kleene); strict decrease
along every propagation row bounds mandatory depth at two productive hops from
an L3 seed (I1).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable, Mapping, NamedTuple

from .expand import HARD_EDGES


class Level(IntEnum):
    """``L0 < L1 < L2 < L3`` (spec Sec 1)."""

    L0 = 0  # absent
    L1 = 1  # identity: FQN + file:line. Lattice member, not an emitted tier.
    L2 = 2  # declaration
    L3 = 3  # body

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return self.name


L0, L1, L2, L3 = Level.L0, Level.L1, Level.L2, Level.L3

#: Spec Sec 4. ``PROPAGATION[edge_type][source_level] -> required target level``.
#:
#: Every row strictly decreases; there are no exceptions and this table is
#: derived from Python semantics, never tuned on results (Sec 9.4).
#: ``INHERITS_FROM`` is absent by design -- it was consumed by MRO flattening
#: at ingest (Sec 3.2) and must not be traversed.
PROPAGATION: dict[str, dict[Level, Level]] = {
    et: {L3: L2, L2: L1, L1: L0, L0: L0} for et in HARD_EDGES
}


class Reason(NamedTuple):
    """One provenance record: why ``dst`` was pulled in, and by what rule."""

    via: int
    edge: str
    rule: str

    def render(self) -> str:  # pragma: no cover - reporting only
        return f"<- {self.via}  {self.edge}  (rule: {self.rule})"


@dataclass
class ClosureResult:
    """Level map plus the provenance the product surface depends on.

    ``provenance`` carries an entry for every non-seed member of ``levels``;
    Sec 7.4 and ``explain_inclusion`` read it, and it is the demo.
    """

    levels: dict[int, Level] = field(default_factory=dict)
    provenance: dict[int, list[Reason]] = field(default_factory=dict)
    seeds: dict[int, Level] = field(default_factory=dict)
    hops_run: int = 0
    edges_examined: int = 0

    def __len__(self) -> int:
        return len(self.levels)

    def at(self, level: Level) -> set[int]:
        return {n for n, lv in self.levels.items() if lv == level}

    def emitted(self) -> set[int]:
        """Nodes at L2 or above -- the ones that produce text (Sec 6.2)."""
        return {n for n, lv in self.levels.items() if lv >= L2}

    def non_seeds(self) -> set[int]:
        return set(self.levels) - set(self.seeds)

    def explain(self, node: int) -> list[Reason]:
        return self.provenance.get(node, [])


ExpandFn = Callable[[list[int]], Iterable[tuple[int, str, int]]]

MAX_HOPS = 2  # structural bound from I1, not a cutoff


def closure(
    seeds: Mapping[int, Level],
    expand: ExpandFn,
    profile: object | None = None,
) -> ClosureResult:
    """Least fixpoint of the propagation rules over ``seeds``.

    ``profile`` is accepted and ignored -- profile-adjusted levels are Item 5.
    TODO(item-5): apply ``profile.adjust(edge_type, required)`` here once
    Sec 6.1's monotone profile family exists.
    """
    level: dict[int, Level] = {n: Level(lv) for n, lv in seeds.items()}
    provenance: dict[int, list[Reason]] = defaultdict(list)
    frontier = [n for n, lv in level.items() if lv > L1]

    hops = 0
    examined = 0
    for _hop in range(MAX_HOPS):
        if not frontier:
            break
        hops += 1
        next_frontier: list[int] = []
        for src, edge_type, dst in expand(frontier):
            examined += 1
            rules = PROPAGATION.get(edge_type)
            if rules is None:
                # INHERITS_FROM and evidence relations never propagate (Sec 4).
                continue
            required = rules[level[src]]
            if required > level.get(dst, L0):
                level[dst] = required  # levels only ever rise
                provenance[dst].append(
                    Reason(
                        via=src,
                        edge=edge_type,
                        rule=f"{edge_type}({level[src].name})->{required.name}",
                    )
                )
                if required > L1:
                    next_frontier.append(dst)
        frontier = next_frontier

    return ClosureResult(
        levels=level,
        provenance=dict(provenance),
        seeds={n: Level(lv) for n, lv in seeds.items()},
        hops_run=hops,
        edges_examined=examined,
    )


def source_cost(result: ClosureResult, sidecar: Mapping[int, object]) -> int:
    """``sum(tok_L3 | tok_L2)`` over the closure -- the Sec 6.2 ``src`` term.

    This is the source half of ``cost()`` only. Provenance trailers, mandatory
    identities and the header are Item 5's budget admission and are not
    computed here.
    """
    total = 0
    for node, lv in result.levels.items():
        meta = sidecar.get(node)
        if meta is None:
            continue
        if lv == L3:
            total += meta.repr_L3_tokens  # type: ignore[attr-defined]
        elif lv == L2:
            total += meta.repr_L2_tokens  # type: ignore[attr-defined]
    return total
