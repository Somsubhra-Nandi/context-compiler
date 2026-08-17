"""Graph layer: ingest (Item 3), sidecar, frontier expansion and fixpoint (Item 4)."""
from .client import BatchStats, GraphClient, connect
from .closure import (
    PROPAGATION,
    ClosureResult,
    Level,
    Reason,
    closure,
    source_cost,
    L0,
    L1,
    L2,
    L3,
)
from .expand import HARD_EDGES, ExpandStats, Expander, expected_round_trips
from .sidecar import SymbolMeta, TextOffset, load_sidecar, read_repr_text, sidecar_bytes

# `ingest` is deliberately NOT imported here: it is the `python -m` entry point,
# and importing it from the package __init__ makes runpy warn about executing a
# module that is already in sys.modules.

__all__ = [
    "BatchStats",
    "GraphClient",
    "connect",
    "PROPAGATION",
    "ClosureResult",
    "Level",
    "Reason",
    "closure",
    "source_cost",
    "L0",
    "L1",
    "L2",
    "L3",
    "HARD_EDGES",
    "ExpandStats",
    "Expander",
    "expected_round_trips",
    "SymbolMeta",
    "TextOffset",
    "load_sidecar",
    "read_repr_text",
    "sidecar_bytes",
]
