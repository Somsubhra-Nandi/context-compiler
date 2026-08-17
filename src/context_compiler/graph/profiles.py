"""The monotone profile family (spec Sec 6.1).

Four profiles, each assigning a level map pointwise <= the previous one. That
ordering is what makes the linear scan in Sec 6.2 valid: token cost is monotone
along the family, so the first profile that fits is the richest that fits, and
no search is needed.

    Profile        Seeds   1st hop                          2nd hop
    P3 FULL        L3      L2                               L1
    P2 COMPACT     L3      L2 direct callees/types, else L1 L0
    P1 MINIMAL     L3      L1                               L0
    P0 FLOOR       L2      L1                               L0

``adjust(edge_type, required)`` caps what the Sec 4 propagation table asked
for. The table itself is never modified -- profiles only ever lower a required
level, never raise one, which is what preserves monotonicity.

**The hop a rule belongs to is recoverable from ``required`` alone.** Every row
of the propagation table maps L3 -> L2 and L2 -> L1, so ``required == L2``
implies the source was at L3 and ``required == L1`` implies the source was at
L2. The caps below are therefore indexed by source level, and ``adjust`` reads
the source level back off ``required``.

P0 is the one place worth pausing on: its seeds are L2, so its *first* hop is a
source-L2 hop and its cap for source-L2 is L1, matching P3's second hop rather
than P2/P1's. This does not break monotonicity, because P1 never places any
node at L2 (its first hop caps at L1), so a source-L2 rule can never fire under
P1 and the two profiles are never compared on it. ``test_profile_monotonicity``
checks the resulting level maps directly rather than trusting this argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .closure import L0, L1, L2, L3, Level
from .expand import HARD_EDGES

#: Sec 6.1's "direct callees/types" -- the two edge kinds P2 keeps at L2.
DIRECT_EDGES = ("CALLS", "REFERENCES_TYPE")


@dataclass(frozen=True)
class Profile:
    """One member of the monotone family.

    ``rank`` orders the family (3 = richest). ``seed_level`` is the level task
    seeds enter the closure at. ``cap_from_L3`` caps hop-1 targets per edge
    type; ``cap_from_L2`` caps hop-2 targets uniformly. ``label`` is Sec 6.1's
    display word, which Sec 7's header prints as ``(P3 FULL)`` -- it lives here
    rather than in emission so there is one spelling of it.
    """

    name: str
    rank: int
    seed_level: Level
    cap_from_L3: Mapping[str, Level]
    cap_from_L2: Level
    label: str = ""

    def adjust(self, edge_type: str, required: Level) -> Level:
        """Lower ``required`` to this profile's cap. Never raises it."""
        if required >= L2:
            cap = self.cap_from_L3.get(edge_type, L2)
        elif required == L1:
            cap = self.cap_from_L2
        else:
            return required
        return required if required <= cap else cap

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return self.name


def _uniform(level: Level) -> dict[str, Level]:
    return {et: level for et in HARD_EDGES}


P3 = Profile(
    name="P3",
    label="FULL",
    rank=3,
    seed_level=L3,
    cap_from_L3=_uniform(L2),
    cap_from_L2=L1,
)

P2 = Profile(
    name="P2",
    label="COMPACT",
    rank=2,
    seed_level=L3,
    cap_from_L3={et: (L2 if et in DIRECT_EDGES else L1) for et in HARD_EDGES},
    cap_from_L2=L0,
)

P1 = Profile(
    name="P1",
    label="MINIMAL",
    rank=1,
    seed_level=L3,
    cap_from_L3=_uniform(L1),
    cap_from_L2=L0,
)

P0 = Profile(
    name="P0",
    label="FLOOR",
    rank=0,
    seed_level=L2,
    cap_from_L3=_uniform(L1),
    cap_from_L2=L1,
)

#: Sec 6.2's scan order: richest first, demote until one fits.
PROFILES: tuple[Profile, ...] = (P3, P2, P1, P0)

BY_NAME = {p.name: p for p in PROFILES}
