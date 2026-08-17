"""In-memory scalar table loaded from ``symbols.jsonl`` (Amendment A1.1).

The graph stores topology; the application stores scalars. Every cost, kind and
refs lookup made by ``closure()`` and the cost model reads ``SIDECAR`` -- none
of them hit HydraDB. ``repr_L2_text`` / ``repr_L3_text`` are deliberately
excluded: bulk text never enters this table.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, NamedTuple


class SymbolMeta(NamedTuple):
    """Per-symbol scalars. Field order matches Amendment A1.1."""

    fqn: str
    kind: str
    repr_L2_tokens: int
    repr_L3_tokens: int
    repr_L2_refs: tuple[int, ...]
    repr_L3_refs: tuple[int, ...]
    identity_tokens: int
    provenance_tokens: int
    evaluable: bool | None


@dataclass
class TextOffset:
    """Byte offset of a symbol's line in ``symbols.jsonl``.

    Supports the A1.1 sidecar principle taken one step further: when the repr
    blobs are too large for graph properties, emission seeks to this offset and
    reads the single line it needs. See the results doc for why this is the
    recommended mode for Django.
    """

    offset: int
    length: int


EMPTY: tuple[int, ...] = ()


def _refs(raw: object) -> tuple[int, ...]:
    if not raw:
        return EMPTY
    return tuple(raw)  # type: ignore[arg-type]


def iter_symbols(path: str | Path) -> Iterator[tuple[dict, int, int]]:
    """Yield ``(record, byte_offset, byte_length)`` for each JSONL line."""
    offset = 0
    with open(path, "rb") as fh:
        for raw in fh:
            length = len(raw)
            yield json.loads(raw), offset, length
            offset += length


def load_sidecar(
    path: str | Path, offsets: dict[int, TextOffset] | None = None
) -> dict[int, SymbolMeta]:
    """Load the scalar table, keyed by integer node id.

    If ``offsets`` is a dict it is populated with the byte offset of each
    symbol's line, giving emission a zero-copy path to ``repr_*_text``.
    """
    table: dict[int, SymbolMeta] = {}
    for rec, off, length in iter_symbols(path):
        nid = rec["id"]
        table[nid] = SymbolMeta(
            fqn=rec["fqn"],
            kind=sys.intern(rec["kind"]),
            repr_L2_tokens=rec["repr_L2_tokens"],
            repr_L3_tokens=rec["repr_L3_tokens"],
            repr_L2_refs=_refs(rec["repr_L2_refs"]),
            repr_L3_refs=_refs(rec["repr_L3_refs"]),
            identity_tokens=rec["identity_tokens"],
            provenance_tokens=rec["provenance_tokens"],
            evaluable=rec["evaluable"],
        )
        if offsets is not None:
            offsets[nid] = TextOffset(off, length)
    return table


def read_repr_text(path: str | Path, offset: TextOffset) -> dict:
    """Read one symbol record back from disk by byte offset."""
    with open(path, "rb") as fh:
        fh.seek(offset.offset)
        return json.loads(fh.read(offset.length))


def sidecar_bytes(table: dict[int, SymbolMeta]) -> int:
    """Deep size of the sidecar in bytes, counting shared strings once."""
    seen: set[int] = set()
    total = sys.getsizeof(table)

    def add(obj: object) -> int:
        if id(obj) in seen:
            return 0
        seen.add(id(obj))
        return sys.getsizeof(obj)

    for key, meta in table.items():
        total += add(key) + add(meta)
        total += add(meta.fqn) + add(meta.kind)
        for refs in (meta.repr_L2_refs, meta.repr_L3_refs):
            total += add(refs)
            for r in refs:
                total += add(r)
    return total
