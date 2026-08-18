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
    file: str
    repr_L2_tokens: int
    repr_L3_tokens: int
    repr_L2_refs: tuple[int, ...]
    repr_L3_refs: tuple[int, ...]
    identity_tokens: int
    provenance_tokens: int
    evaluable: bool | None
    start_line: int = 0
    end_line: int = 0


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
            file=sys.intern(rec["file"]),
            repr_L2_tokens=rec["repr_L2_tokens"],
            repr_L3_tokens=rec["repr_L3_tokens"],
            repr_L2_refs=_refs(rec["repr_L2_refs"]),
            repr_L3_refs=_refs(rec["repr_L3_refs"]),
            identity_tokens=rec["identity_tokens"],
            provenance_tokens=rec["provenance_tokens"],
            evaluable=rec["evaluable"],
            start_line=rec.get("start_line", 0),
            end_line=rec.get("end_line", 0),
        )
        if offsets is not None:
            offsets[nid] = TextOffset(off, length)
    return table


def load_degree_tables(
    edges_path: str | Path, edge_types: tuple[str, ...] | None = None
) -> tuple[dict[int, int], dict[int, int]]:
    """``(out_degree, in_degree)`` per symbol, in one pass over the edge file.

    Two scalar tables, loaded application-side for the same reason as the rest
    of the sidecar: a whole-graph degree count does not survive the engine's
    deadline (A2.3), and every ranking decision needs one.

    * **out-degree** drives Sec 6.3's ``idf`` hub suppression. A caller that
      calls 175 things tells you almost nothing about which of them you are
      looking at.
    * **in-degree** drives A3.1's hub skip. Reverse discovery on a symbol with
      hundreds of callers costs seconds and yields nothing ``idf`` would rank.

    Both come from the same pass, so the hub skip is a dict lookup rather than
    new I/O. ``edge_types`` defaults to every relation in the file; passing
    ``HARD_EDGES`` restricts it to relations that actually propagate.
    """
    out_degree: dict[int, int] = {}
    in_degree: dict[int, int] = {}
    keep = set(edge_types) if edge_types else None
    with open(edges_path, "rb") as fh:
        for raw in fh:
            rec = json.loads(raw)
            if keep is not None and rec["type"] not in keep:
                continue
            src = rec["src"]
            dst = rec["dst"]
            out_degree[src] = out_degree.get(src, 0) + 1
            in_degree[dst] = in_degree.get(dst, 0) + 1
    return out_degree, in_degree


def load_degrees(
    edges_path: str | Path, edge_types: tuple[str, ...] | None = None
) -> dict[int, int]:
    """Out-degree per symbol. Thin wrapper over :func:`load_degree_tables`."""
    return load_degree_tables(edges_path, edge_types)[0]


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
        total += add(meta.fqn) + add(meta.kind) + add(meta.file)
        for refs in (meta.repr_L2_refs, meta.repr_L3_refs):
            total += add(refs)
            for r in refs:
                total += add(r)
    return total
