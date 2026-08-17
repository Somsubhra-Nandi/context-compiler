"""Item 7 Sec 2: load once at startup, never per call.

``load_state`` does the sidecar load, the offset-index load and the HydraDB
connect, then opens ``Expander``/``ReverseReader`` **once** and keeps their
session open for the process lifetime -- "Bolt driver: one connection,
reused" is a property of *this module*, not of the tools that call it, so a
tool body should never construct a client, an expander or a reader itself.

Fails fast and legibly: a missing path or an unreachable database raises
``StartupError`` with the exact path or URI named, before the stdio loop ever
starts. A server that starts and then fails every call is worse than one that
refuses to start.
"""
from __future__ import annotations

import resource
import time
from dataclasses import dataclass, field
from typing import Optional

from ..emit.source import OffsetTextSource, load_offsets
from ..graph.client import GraphClient
from ..graph.compile import Compiler, Context
from ..graph.expand import HARD_EDGES, Expander, ReverseReader
from ..graph.sidecar import SymbolMeta, load_degree_tables, load_sidecar
from .config import Config


class StartupError(RuntimeError):
    """One-line, actionable: the exact path or URI that failed."""


@dataclass
class StartupStats:
    sidecar_ms: float = 0.0
    bolt_ms: float = 0.0
    total_ms: float = 0.0
    memory_kb: int = 0
    n_symbols: int = 0


@dataclass
class ServerState:
    config: Config
    sidecar: dict[int, SymbolMeta]
    by_fqn: dict[str, int]
    degrees: dict[int, int]
    in_degrees: dict[int, int]
    source: OffsetTextSource
    client: GraphClient
    expander: Expander
    reverse: ReverseReader
    compiler: Compiler
    startup: StartupStats
    last_context: Optional[Context] = field(default=None, repr=False)
    last_seeds: list[int] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.expander.__exit__(None, None, None)
        finally:
            try:
                self.reverse.__exit__(None, None, None)
            finally:
                self.client.close()


def load_state(config: Config) -> ServerState:
    t0 = time.perf_counter()

    if not config.symbols.exists():
        raise StartupError(f"symbols file not found: {config.symbols} (set CC_SYMBOLS)")
    if not config.offsets.exists():
        raise StartupError(f"offset index not found: {config.offsets} (set CC_OFFSETS)")
    if not config.edges.exists():
        raise StartupError(f"edges file not found: {config.edges} (set CC_EDGES)")

    sidecar = load_sidecar(config.symbols)
    by_fqn = {meta.fqn: nid for nid, meta in sidecar.items()}
    degrees, in_degrees = load_degree_tables(config.edges, tuple(HARD_EDGES))
    source_path, offsets = load_offsets(config.offsets)
    if source_path != config.symbols:
        raise StartupError(
            f"offset index {config.offsets} was built against {source_path}, "
            f"not {config.symbols} -- re-run ingest with --offset-index, or fix CC_SYMBOLS/CC_OFFSETS"
        )
    source = OffsetTextSource(source_path, offsets)
    sidecar_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    try:
        client = GraphClient(uri=config.bolt_uri)
        client.verify()
    except Exception as exc:  # noqa: BLE001 -- fail-fast diagnosis, not a handler
        raise StartupError(f"cannot reach HydraDB at {config.bolt_uri}: {exc}") from exc
    bolt_ms = (time.perf_counter() - t1) * 1000

    expander = Expander(client, membership=sidecar)
    expander.__enter__()
    reverse = ReverseReader(client, membership=sidecar)
    reverse.__enter__()

    compiler = Compiler(
        sidecar=sidecar,
        expander=expander,
        reverse=reverse,
        degrees=degrees,
        in_degrees=in_degrees,
    )

    stats = StartupStats(
        sidecar_ms=round(sidecar_ms, 1),
        bolt_ms=round(bolt_ms, 1),
        total_ms=round((time.perf_counter() - t0) * 1000, 1),
        memory_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        n_symbols=len(sidecar),
    )

    return ServerState(
        config=config,
        sidecar=sidecar,
        by_fqn=by_fqn,
        degrees=degrees,
        in_degrees=in_degrees,
        source=source,
        client=client,
        expander=expander,
        reverse=reverse,
        compiler=compiler,
        startup=stats,
    )
