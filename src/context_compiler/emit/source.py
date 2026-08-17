"""Where emitted text comes from: the offset index, never the graph (A2.1).

Amendment A1.1 planned to fetch ``repr_L2_text`` / ``repr_L3_text`` back out of
node properties with a single-source query. **A2.1 withdrew that.** 164 Django
symbols exceed the engine's ~32 KiB string property cap -- ``tests.admin_views``
``.tests`` needs 347 KB -- so the property is not written at all in the default
ingest, and a controlled measurement showed graph-resident text slowing the
closure's *own* hot path by 56-82%. Bulk text is application-side data, exactly
like the rest of the sidecar.

``graph.sidecar.read_repr_text`` already implements the seek. This module reads
it and memoises the result per compile; it does not reimplement or modify it.

One seek yields the whole record, so ``file``, ``start_line`` and both repr
strings arrive together. That matters more than it looks: emission needs
``file`` to group by file (Sec 7.1) and ``file:line`` to render an identity line
(Sec 7.3), and neither field is in ``SymbolMeta``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from ..graph.closure import L2, L3, Level
from ..graph.sidecar import TextOffset, read_repr_text


@dataclass(frozen=True)
class SymbolRecord:
    """The fields emission needs out of one ``symbols.jsonl`` line."""

    id: int
    fqn: str
    kind: str
    file: str
    start_line: int
    repr_L2_text: str = ""
    repr_L3_text: str = ""

    def text(self, level: Level) -> str:
        """Canonical emitted text at ``level``. L1 and L0 emit nothing."""
        if level >= L3:
            return self.repr_L3_text
        if level == L2:
            return self.repr_L2_text
        return ""

    def identity(self) -> str:
        """The Sec 7.3 identity line.

        **Byte-identical to the string the extractor costed** as
        ``identity_tokens`` (``f"{fqn} — {path}:{lineno}"``, em dash included).
        The two must not drift: ``cost()`` charges this line at that figure, so
        a different rendering here would break I4 by exactly the difference.
        """
        return f"{self.fqn} — {self.file}:{self.start_line}"

    @classmethod
    def from_json(cls, rec: Mapping[str, object]) -> "SymbolRecord":
        return cls(
            id=int(rec["id"]),  # type: ignore[arg-type]
            fqn=str(rec["fqn"]),
            kind=str(rec["kind"]),
            file=str(rec["file"]),
            start_line=int(rec["start_line"]),  # type: ignore[arg-type]
            repr_L2_text=str(rec.get("repr_L2_text") or ""),
            repr_L3_text=str(rec.get("repr_L3_text") or ""),
        )


@dataclass
class SeekStats:
    """Offset-index cost, which the results doc asks to be reported."""

    seeks: int = 0
    cache_hits: int = 0
    bytes_read: int = 0
    seconds: float = 0.0

    @property
    def ms_per_seek(self) -> float:
        return (self.seconds * 1000 / self.seeks) if self.seeks else 0.0


class TextSource(Protocol):
    """Anything emission can ask for a symbol record."""

    stats: SeekStats

    def record(self, node: int) -> SymbolRecord | None: ...


@dataclass
class OffsetTextSource:
    """Real source: seek into ``symbols.jsonl`` by byte offset.

    Memoised per instance, because a compile asks for the same record more than
    once -- a symbol can be both an emitted block and the ``via`` of another
    node's provenance line.
    """

    path: Path
    offsets: Mapping[int, TextOffset]
    stats: SeekStats = field(default_factory=SeekStats)
    _cache: dict[int, SymbolRecord | None] = field(default_factory=dict, repr=False)

    def record(self, node: int) -> SymbolRecord | None:
        if node in self._cache:
            self.stats.cache_hits += 1
            return self._cache[node]
        offset = self.offsets.get(node)
        if offset is None:
            self._cache[node] = None
            return None
        t0 = time.perf_counter()
        raw = read_repr_text(self.path, offset)
        self.stats.seconds += time.perf_counter() - t0
        self.stats.seeks += 1
        self.stats.bytes_read += offset.length
        rec = SymbolRecord.from_json(raw)
        self._cache[node] = rec
        return rec


@dataclass
class MappingTextSource:
    """Fixture source: records held in memory. No I/O, no database."""

    records: Mapping[int, SymbolRecord]
    stats: SeekStats = field(default_factory=SeekStats)

    def record(self, node: int) -> SymbolRecord | None:
        rec = self.records.get(node)
        if rec is not None:
            self.stats.cache_hits += 1
        return rec


def load_offsets(path: str | Path) -> tuple[Path, dict[int, TextOffset]]:
    """Read an ingest ``--offset-index`` file into ``{id: TextOffset}``.

    Returns the ``symbols.jsonl`` path the index was built against alongside it,
    so a caller cannot accidentally pair an index with a re-extracted file.
    """
    blob = json.loads(Path(path).expanduser().read_text())
    offsets = {
        int(k): TextOffset(int(v[0]), int(v[1])) for k, v in blob["offsets"].items()
    }
    return Path(blob["source"]).expanduser(), offsets


def source_from_symbols(path: str | Path) -> OffsetTextSource:
    """Build a source by scanning ``symbols.jsonl`` for offsets directly.

    Equivalent to ``load_offsets`` on an ingest-written index, minus the
    dependency on having run an ingest with ``--offset-index``.
    """
    from ..graph.sidecar import iter_symbols

    resolved = Path(path).expanduser()
    offsets: dict[int, TextOffset] = {}
    for rec, off, length in iter_symbols(resolved):
        offsets[rec["id"]] = TextOffset(off, length)
    return OffsetTextSource(resolved, offsets)


__all__ = [
    "L2",
    "L3",
    "MappingTextSource",
    "OffsetTextSource",
    "SeekStats",
    "SymbolRecord",
    "TextSource",
    "load_offsets",
    "source_from_symbols",
]
