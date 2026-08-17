"""Item 3 -- load the extraction JSONL pair into HydraDB.

Everything here goes through the ``UNWIND`` batch path, which this engine
requires for all upserts (Amendment A1.2). Three engine constraints shape the
pass structure and none of them are optional:

1. ``UNWIND vertex upsert requires exactly one SET label`` -- dual labelling is
   two sequential single-label passes (A1.2).
2. Parameter values may only be boolean, signed integer, finite float or
   string. ``None`` is rejected at the parameter layer, so ``evaluable`` and
   ``static_value`` (null on 41,762 of 43,420 Django symbols) get their own
   passes over the non-null subset.
3. A string property value is capped just under 32 KiB and the engine rejects
   an oversized one with ``internal query execution error``. Django exceeds
   this in three places: ``repr_L3_text`` (163 symbols), ``repr_L2_text`` (1)
   and -- in the default no-text mode too -- ``static_value`` (1, a
   66,517-byte folded constant). Oversized values are skipped and reported,
   never truncated or written empty, because a silently emptied property is a
   false statement about the symbol. The value stays reachable through the
   ``symbols.jsonl`` byte-offset index (``--offset-index``).

CLI::

    python -m context_compiler.graph.ingest \
        --symbols ~/out/django/symbols.jsonl \
        --edges   ~/out/django/edges.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from hashlib import blake2b
from pathlib import Path

from neo4j.exceptions import ClientError

from .client import DEFAULT_BATCH, MAX_STRING_PROPERTY, BatchStats, GraphClient, connect
from .sidecar import iter_symbols

#: Mandatory relation types (spec Sec 2.2). ``INHERITS_FROM`` is stored for
#: display and ranking only -- it was consumed by MRO flattening at ingest and
#: is never traversed by the closure (Sec 3.2, Sec 4).
MANDATORY_EDGES = (
    "REFERENCES_TYPE",
    "CALLS",
    "OVERRIDES",
    "IMPLEMENTS",
    "DECORATED_BY",
    "READS_CONSTANT",
)
DISPLAY_EDGES = ("INHERITS_FROM",)
ALL_EDGES = MANDATORY_EDGES + DISPLAY_EDGES


def eid(edge_type: str, src: int, dst: int, revision: str | None = None) -> int:
    """Relationship identity per spec Sec 2.1.3.

    Static edges are a stable ``H(type, src, dst)`` and therefore idempotent
    under re-ingest. Runtime edges (Item 9) must pass ``revision`` or each new
    trace overwrites the previous observation and destroys I5's history.
    """
    key = f"{edge_type}|{src}|{dst}"
    if revision is not None:
        key = f"{key}|{revision}"
    return int.from_bytes(blake2b(key.encode(), digest_size=8).digest(), "big") >> 1


# -- Cypher templates ----------------------------------------------------
#
# Vertex upsert: MERGE by id then SET, exactly one label per SET clause.

Q_SYMBOL = (
    "UNWIND $rows AS row "
    "MERGE (n {id: row.v}) "
    "SET n:Symbol, n.fqn = row.fqn, n.kind = row.kind, n.file = row.file, "
    "    n.start_line = row.sl, n.end_line = row.el, n.body_hash = row.bh, "
    "    n.repr_L2_tokens = row.t2, n.repr_L3_tokens = row.t3, "
    "    n.identity_tokens = row.it, n.provenance_tokens = row.pt, "
    "    n.mro_partial = row.mp"
)

Q_TEST_LABEL = "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Test"

Q_EVALUABLE = "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.evaluable = row.ev"

Q_STATIC_VALUE = (
    "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.static_value = row.sv"
)

Q_TEXT = (
    "UNWIND $rows AS row MERGE (n {id: row.v}) "
    "SET n:Symbol, n.repr_L2_text = row.x2, n.repr_L3_text = row.x3, "
    "    n.repr_L2_refs = row.r2, n.repr_L3_refs = row.r3"
)

#: Narrower templates for rows carrying an over-cap text field. A fixed SET
#: template cannot skip one property for one row, so the row is routed to the
#: subset of templates whose values actually fit.
Q_TEXT_REFS = (
    "UNWIND $rows AS row MERGE (n {id: row.v}) "
    "SET n:Symbol, n.repr_L2_refs = row.r2, n.repr_L3_refs = row.r3"
)
Q_TEXT_L2 = "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.repr_L2_text = row.x2"
Q_TEXT_L3 = "UNWIND $rows AS row MERGE (n {id: row.v}) SET n:Symbol, n.repr_L3_text = row.x3"


def q_edge(edge_type: str, with_call_sites: bool) -> str:
    """Edge upsert: one type per batch, exactly one label per endpoint."""
    sets = "SET r.resolver = row.resolver, r.confidence = row.conf"
    if with_call_sites:
        sets += ", r.call_sites = row.cs"
    return (
        "UNWIND $rows AS row "
        "MATCH (s:Symbol {id: row.src}), (d:Symbol {id: row.dst}) "
        f"MERGE (s)-[r:{edge_type} {{id: row.eid}}]->(d) " + sets
    )


# -- row builders --------------------------------------------------------


def fits(value: str) -> bool:
    """Whether a string value is small enough to be a property value."""
    return len(value.encode()) <= MAX_STRING_PROPERTY


def symbol_row(rec: dict) -> dict:
    for field in ("fqn", "file", "body_hash"):
        if not fits(rec[field]):
            # Identity fields are load-bearing; skipping one silently would
            # produce a node nothing can resolve. Fail loudly instead.
            raise ValueError(
                f"symbol {rec['id']} has {field} over {MAX_STRING_PROPERTY} bytes; "
                "identity properties cannot be skipped"
            )
    return {
        "v": rec["id"],
        "fqn": rec["fqn"],
        "kind": rec["kind"],
        "file": rec["file"],
        "sl": rec["start_line"],
        "el": rec["end_line"],
        "bh": rec["body_hash"],
        "t2": rec["repr_L2_tokens"],
        "t3": rec["repr_L3_tokens"],
        "it": rec["identity_tokens"],
        "pt": rec["provenance_tokens"],
        "mp": bool(rec["mro_partial"]),
    }


def text_row(rec: dict) -> tuple[dict, list[str]]:
    """Build the text-property row and list any field over the size cap.

    Values are returned verbatim -- never truncated and never emptied. The
    caller routes an over-cap row to the narrower templates so the oversized
    property is simply absent from the graph and is served from the JSONL
    offset index instead.
    """
    values = {
        "v": rec["id"],
        "x2": rec["repr_L2_text"],
        "x3": rec["repr_L3_text"],
        "r2": json.dumps(rec["repr_L2_refs"], separators=(",", ":")),
        "r3": json.dumps(rec["repr_L3_refs"], separators=(",", ":")),
    }
    over = [
        field
        for key, field in (
            ("x2", "repr_L2_text"),
            ("x3", "repr_L3_text"),
            ("r2", "repr_L2_refs"),
            ("r3", "repr_L3_refs"),
        )
        if not fits(values[key])
    ]
    return values, over


def edge_row(rec: dict) -> dict:
    src, dst, etype = rec["src"], rec["dst"], rec["type"]
    row = {
        "src": src,
        "dst": dst,
        "eid": eid(etype, src, dst),
        "resolver": rec["resolver"],
        "conf": float(rec["confidence"]),
    }
    if etype == "CALLS":
        row["cs"] = rec.get("call_sites", 1)
    return row


# -- passes --------------------------------------------------------------


def ingest_symbols(
    client: GraphClient,
    path: Path,
    with_text: bool,
    batch: int,
    text_batch: int,
    offsets: dict[int, object] | None = None,
) -> tuple[list[BatchStats], dict]:
    """Load nodes as a sequence of single-label UNWIND upserts.

    Pass structure is forced by the engine, not chosen: one label per SET
    (A1.2) and no null parameters, so ``:Test``, ``evaluable`` and
    ``static_value`` each get their own pass over their own subset.
    """
    node_ids: list[int] = []
    base_rows: list[dict] = []
    test_rows: list[dict] = []
    eval_rows: list[dict] = []
    static_rows: list[dict] = []
    text_full: list[dict] = []
    text_partial: list[dict] = []
    oversize: Counter[str] = Counter()
    oversize_ids: list[int] = []
    kinds: Counter[str] = Counter()

    for rec, off, length in iter_symbols(path):
        base_rows.append(symbol_row(rec))
        node_ids.append(rec["id"])
        kinds[rec["kind"]] += 1
        if offsets is not None:
            offsets[rec["id"]] = (off, length)
        if rec["kind"] == "test":
            test_rows.append({"v": rec["id"]})
        if rec["evaluable"] is not None:
            eval_rows.append({"v": rec["id"], "ev": bool(rec["evaluable"])})
        if rec["static_value"] is not None:
            sv = str(rec["static_value"])
            if fits(sv):
                static_rows.append({"v": rec["id"], "sv": sv})
            else:
                # One Django constant folds to 66,517 bytes. Skipped, not
                # truncated: a clipped constant value is worse than an absent
                # one, and the real value is one seek away in symbols.jsonl.
                oversize["static_value"] += 1
                oversize_ids.append(rec["id"])
        if with_text:
            row, over = text_row(rec)
            if over:
                text_partial.append(row)
                for field in over:
                    oversize[field] += 1
                oversize_ids.append(rec["id"])
            else:
                text_full.append(row)

    passes: list[tuple[str, str, list[dict], int]] = [
        ("nodes :Symbol", Q_SYMBOL, base_rows, batch),
        ("nodes :Test (pass 2)", Q_TEST_LABEL, test_rows, batch),
        ("nodes evaluable", Q_EVALUABLE, eval_rows, batch),
        ("nodes static_value", Q_STATIC_VALUE, static_rows, batch),
    ]
    if with_text:
        passes.append(("nodes repr_*_text", Q_TEXT, text_full, text_batch))
        if text_partial:
            passes += [
                ("nodes repr_*_refs (partial)", Q_TEXT_REFS, text_partial, text_batch),
                (
                    "nodes repr_L2_text (partial)",
                    Q_TEXT_L2,
                    [r for r in text_partial if fits(r["x2"])],
                    text_batch,
                ),
                (
                    "nodes repr_L3_text (partial)",
                    Q_TEXT_L3,
                    [r for r in text_partial if fits(r["x3"])],
                    text_batch,
                ),
            ]

    stats: list[BatchStats] = []
    with client.session() as s:
        for name, query, rows, size in passes:
            if not rows:
                continue
            st = BatchStats(name)
            client.run_batches(query, rows, st, batch_size=size, session=s)
            stats.append(st)

    summary = {
        "node_ids": node_ids,
        "symbols": len(base_rows),
        "tests": len(test_rows),
        "evaluable": len(eval_rows),
        "static_value": len(static_rows),
        "kinds": dict(kinds),
        "oversize_fields": dict(oversize),
        "oversize_symbols": len(set(oversize_ids)),
    }
    return stats, summary


def ingest_edges(
    client: GraphClient, path: Path, batch: int, include_display: bool = True
) -> tuple[list[BatchStats], dict]:
    """Load edges, one relationship type per batch."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for line in open(path, "rb"):
        rec = json.loads(line)
        etype = rec["type"]
        counts[etype] += 1
        if etype not in ALL_EDGES:
            raise ValueError(f"unknown relation type in edges.jsonl: {etype!r}")
        if etype in DISPLAY_EDGES and not include_display:
            continue
        by_type[etype].append(edge_row(rec))

    stats: list[BatchStats] = []
    with client.session() as s:
        for etype in ALL_EDGES:
            rows = by_type.get(etype)
            if not rows:
                continue
            st = BatchStats(f"edges {etype}")
            client.run_batches(
                q_edge(etype, etype == "CALLS"), rows, st, batch_size=batch, session=s
            )
            stats.append(st)

    mandatory = sum(counts[t] for t in MANDATORY_EDGES)
    return stats, {
        "edges": sum(counts.values()),
        "mandatory": mandatory,
        "display": sum(counts[t] for t in DISPLAY_EDGES),
        "by_type": dict(counts),
    }


def verify(client: GraphClient, node_ids: list[int], batch: int = DEFAULT_BATCH) -> dict:
    """Read back the counts the acceptance gate asks for.

    A whole-graph relationship count does not work at Django scale. Measured
    against this build: ``MATCH (a)-[:READS_CONSTANT]->(b) RETURN count(*)``
    returns 820 in 2.3 s, but adding ``:Symbol`` labels to the same query
    exceeds the 29,999 ms deadline -- and unlabelled ``CALLS`` (95,288 edges)
    exceeds it too. Labels are the cost, exactly as A1.1 found for batch
    reads, and the scan cost is linear in edges.

    So read-back goes through the chunked, label-free A1.1 batch form instead:
    every edge's source is an emitted symbol (JSONL contract), so summing
    out-degree over all node ids is a complete count, each request stays well
    inside the deadline, and it exercises the same query path ``expand()``
    uses in production.
    """
    out: dict[str, int] = {}
    try:
        out["Symbol"] = client.count("(n:Symbol)")
        out["Test"] = client.count("(n:Test)")
    except ClientError as exc:
        if "timeout" not in str(exc).lower():
            raise
        out.setdefault("Symbol", -1)
        out.setdefault("Test", -1)

    rows = [{"v": n} for n in node_ids]
    total = 0
    with client.session() as s:
        for etype in ALL_EDGES:
            query = (
                "UNWIND $rows AS row "
                f"MATCH (x {{id: row.v}})-[:{etype}]->(y) "
                "RETURN row.v AS src, y.id AS dst"
            )
            seen = 0
            for start in range(0, len(rows), batch):
                seen += sum(1 for _ in s.run(query, rows=rows[start : start + batch]))
            out[etype] = seen
            if etype in MANDATORY_EDGES:
                total += seen
    out["mandatory_edges"] = total
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="context_compiler.graph.ingest")
    ap.add_argument("--symbols", required=True, type=Path)
    ap.add_argument("--edges", required=True, type=Path)
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument(
        "--text-batch",
        type=int,
        default=100,
        help="rows per batch for the repr_*_text pass (payload is ~100x larger)",
    )
    ap.add_argument(
        "--text",
        action="store_true",
        help="also write repr_L2_text/repr_L3_text/refs into graph properties. "
        "Off by default: 160 Django symbols exceed the ~32 KiB property cap "
        "and would be silently emptied. See docs/spikes/graph-item-3-4-results.md.",
    )
    ap.add_argument(
        "--offset-index",
        type=Path,
        default=None,
        help="write a {id: [byte_offset, length]} index into symbols.jsonl. This is "
        "the A1.1 sidecar principle applied to bulk text: the graph holds topology, "
        "and emission seeks to the offset for repr_*_text.",
    )
    ap.add_argument("--limit", type=int, default=0, help="debug: stop after N symbols")
    ap.add_argument("--skip-edges", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    symbols_path = args.symbols.expanduser()
    edges_path = args.edges.expanduser()
    if args.limit:
        symbols_path, edges_path = _truncate(symbols_path, edges_path, args.limit)

    offsets: dict[int, object] | None = {} if args.offset_index else None

    t0 = time.perf_counter()
    client = connect(batch_size=args.batch)
    try:
        node_stats, node_summary = ingest_symbols(
            client, symbols_path, args.text, args.batch, args.text_batch, offsets
        )
        edge_stats, edge_summary = ([], {})
        if not args.skip_edges:
            edge_stats, edge_summary = ingest_edges(client, edges_path, args.batch)
        wall = time.perf_counter() - t0
        t_verify = time.perf_counter()
        counts = verify(client, node_summary.pop("node_ids"), args.batch)
        verify_seconds = time.perf_counter() - t_verify
    finally:
        client.close()

    if offsets is not None:
        args.offset_index.parent.mkdir(parents=True, exist_ok=True)
        with open(args.offset_index, "w") as fh:
            json.dump(
                {"source": str(symbols_path), "offsets": {str(k): v for k, v in offsets.items()}},
                fh,
            )

    report = {
        "wall_seconds": round(wall, 2),
        "verify_seconds": round(verify_seconds, 2),
        "batch_size": args.batch,
        "text_in_graph": args.text,
        "nodes": node_summary,
        "edges": edge_summary,
        "readback": counts,
        "passes": [
            {
                "name": s.name,
                "rows": s.rows,
                "requests": s.requests,
                "seconds": round(s.seconds, 2),
                "retries": s.retries,
                "splits": s.splits,
            }
            for s in node_stats + edge_stats
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for s in node_stats + edge_stats:
            print(s)
        print(f"\ningest wall time: {wall:.2f}s  batch={args.batch}  text={args.text}")
        if node_summary["oversize_symbols"]:
            print(
                f"NOTE: {node_summary['oversize_symbols']} symbols carry a value over "
                f"{MAX_STRING_PROPERTY:,} bytes; those properties were skipped, not "
                f"truncated: {node_summary['oversize_fields']}"
            )
        print(f"read-back counts (chunked batch reads, {verify_seconds:.2f}s):")
        for k, v in counts.items():
            print(f"  {k:<20} {v:>9,}")
    return 0


def _truncate(symbols: Path, edges: Path, limit: int) -> tuple[Path, Path]:
    """Debug helper: write a referentially closed prefix to the scratch dir."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="cc-ingest-"))
    keep: set[int] = set()
    out_s = tmp / "symbols.jsonl"
    with open(out_s, "wb") as fh:
        for i, line in enumerate(open(symbols, "rb")):
            if i >= limit:
                break
            keep.add(json.loads(line)["id"])
            fh.write(line)
    out_e = tmp / "edges.jsonl"
    with open(out_e, "wb") as fh:
        for line in open(edges, "rb"):
            rec = json.loads(line)
            if rec["src"] in keep and rec["dst"] in keep:
                fh.write(line)
    return out_s, out_e


if __name__ == "__main__":
    sys.exit(main())
