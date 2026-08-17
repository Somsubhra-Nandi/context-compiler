"""Deterministic hybrid AST + SCIP extraction CLI."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from hashlib import blake2b, sha256
import json
from pathlib import Path
import time
from typing import Any

from .ast_occurrences import Symbol, discover_file, occurrence_nodes
from .constants import MAX_STATIC_VALUE_BYTES, fold_constants, render_folded
from .mro import flatten_members
from .representations import canonical_l2, canonical_l3, count_tokens, own_class_members
from .scip_reader import ScipIndex, read_index, run_index, symbol_to_fqn


def node_id(fqn: str) -> int:
    return int.from_bytes(blake2b(fqn.encode(), digest_size=8).digest(), "big") >> 1


def _assign_ids(fqns: list[str]) -> dict[str, int]:
    used: set[int] = set()
    result: dict[str, int] = {}
    for fqn in sorted(fqns):
        value = node_id(fqn)
        while value in used:
            value += 1
        used.add(value)
        result[fqn] = value
    return result


def _definition_value(symbol: Symbol) -> ast.AST | None:
    node = symbol.node
    return node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None


def _reference_symbol(scip: ScipIndex, symbol: Symbol, node: ast.AST) -> str | None:
    column = getattr(node, "col_offset", 0)
    if isinstance(node, ast.Attribute):
        column = max(column, getattr(node, "end_col_offset", column) - len(node.attr))
    elif isinstance(node, ast.Subscript):
        node = node.value
        column = getattr(node, "col_offset", column)
    raw = scip.resolve(symbol.path, getattr(node, "lineno", 0), column)
    return symbol_to_fqn(raw) if raw else None


def extract_repository(repo: Path, out: Path, *, reindex: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    repo, out = repo.resolve(), out.resolve()
    index_path = run_index(repo) if reindex or not (repo / "index.scip").exists() else repo / "index.scip"
    scip = ScipIndex(read_index(index_path))
    symbols: list[Symbol] = []
    for path in sorted(repo.rglob("*.py")):
        if any(part.startswith(".") for part in path.relative_to(repo).parts):
            continue
        try:
            found, _ = discover_file(repo, path)
            symbols.extend(found)
        except (SyntaxError, UnicodeDecodeError):
            continue
    # Python runtime FQNs coalesce overload definitions deterministically.
    by_fqn = {symbol.fqn: symbol for symbol in symbols}
    ids = _assign_ids(list(by_fqn))

    constant_defs = {fqn: value for fqn, symbol in by_fqn.items()
                     if symbol.kind == "constant" and (value := _definition_value(symbol)) is not None}
    # Permit same-module short references during folding.
    folds: dict[str, Any] = {}
    for module in sorted({symbol.module for symbol in by_fqn.values()}):
        local = {fqn.rsplit(".", 1)[-1]: expr for fqn, expr in constant_defs.items() if fqn.rsplit(".", 1)[0] == module}
        for name, folded in fold_constants(local).items():
            folds[f"{module}.{name}"] = folded

    bases: dict[str, list[str]] = {}
    members: dict[str, list[str]] = {}
    for fqn, symbol in by_fqn.items():
        if symbol.kind == "class" and isinstance(symbol.node, ast.ClassDef):
            resolved = []
            for base in symbol.node.bases:
                target = _reference_symbol(scip, symbol, base)
                if target in by_fqn:
                    resolved.append(target)
                else:
                    resolved.append(target or ast.unparse(base))
            bases[fqn] = resolved
            members[fqn] = own_class_members(symbol.node)

    rows: list[dict[str, Any]] = []
    ref_fqns: dict[tuple[str, str], set[str]] = defaultdict(set)
    edge_counts: Counter[tuple[str, str, str]] = Counter()
    attempts: Counter[str] = Counter()
    successes: Counter[str] = Counter()
    for fqn, symbol in by_fqn.items():
        for edge_type, occurrence in occurrence_nodes(symbol):
            attempts[edge_type] += 1
            target = _reference_symbol(scip, symbol, occurrence)
            if target in by_fqn and target != fqn:
                successes[edge_type] += 1
                edge_counts[(edge_type, fqn, target)] += 1
                ref_fqns[(fqn, "L3")].add(target)
                if edge_type in {"REFERENCES_TYPE", "DECORATED_BY"}:
                    ref_fqns[(fqn, "L2")].add(target)
        if isinstance(symbol.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name in {n.id for n in ast.walk(symbol.node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
                target = f"{symbol.module}.{name}"
                if target in by_fqn and by_fqn[target].kind == "constant" and target != fqn:
                    edge_counts[("READS_CONSTANT", fqn, target)] += 1
                    ref_fqns[(fqn, "L3")].add(target)

    for src_raw, dst_raw, _ in scip.relationships:
        src, dst = symbol_to_fqn(src_raw), symbol_to_fqn(dst_raw)
        if src in by_fqn and dst in by_fqn and src != dst:
            edge_type = "OVERRIDES" if by_fqn[src].kind in {"method", "function"} else "IMPLEMENTS"
            edge_counts[(edge_type, src, dst)] += 1
            ref_fqns[(src, "L2")].add(dst)
            ref_fqns[(src, "L3")].add(dst)

    mro_partial: dict[str, bool] = {}
    surfaces: dict[str, list[tuple[str, str]]] = {}
    for fqn in bases:
        surfaces[fqn], mro_partial[fqn] = flatten_members(fqn, bases, members)
        for base in bases[fqn]:
            if base in by_fqn:
                edge_counts[("INHERITS_FROM", fqn, base)] += 1

    for fqn in sorted(by_fqn):
        symbol = by_fqn[fqn]
        l2 = canonical_l2(symbol, surfaces.get(fqn))
        l3 = canonical_l3(symbol)
        rendered = render_folded(folds.get(fqn)) if symbol.kind == "constant" else None
        if rendered and rendered.inlined:
            l2 = f"{fqn.rsplit('.', 1)[-1]} = {rendered.literal}\n"
        elif rendered and rendered.over_cap:
            # A2.2: keep the defining expression and state the size, so the
            # model learns what the constant *is* without paying for the value.
            l2 = l2.rstrip("\n") + (
                f"\n# folded value omitted: {rendered.size_bytes:,} bytes"
                f" (cap {MAX_STATIC_VALUE_BYTES:,})\n"
            )
        identity = f"{fqn} — {symbol.path}:{getattr(symbol.node, 'lineno', 1)}"
        provenance = f"{fqn} [extracted: ast+scip]"
        rows.append({
            "id": ids[fqn], "fqn": fqn, "kind": symbol.kind, "file": symbol.path,
            "start_line": getattr(symbol.node, "lineno", 1),
            "end_line": getattr(symbol.node, "end_lineno", getattr(symbol.node, "lineno", 1)),
            "body_hash": "sha256:" + sha256(l3.encode()).hexdigest(),
            "repr_L2_text": l2, "repr_L2_tokens": count_tokens(l2),
            "repr_L2_refs": sorted(ids[x] for x in ref_fqns[(fqn, "L2")] if x in ids),
            "repr_L3_text": l3, "repr_L3_tokens": count_tokens(l3),
            "repr_L3_refs": sorted(ids[x] for x in ref_fqns[(fqn, "L3")] if x in ids),
            "identity_tokens": count_tokens(identity), "provenance_tokens": count_tokens(provenance),
            "evaluable": rendered.evaluable if rendered else None,
            "static_value": rendered.literal if rendered else None,
            "static_value_bytes": rendered.size_bytes if rendered and rendered.over_cap else None,
            "mro_partial": mro_partial.get(fqn, False),
        })

    edges: list[dict[str, Any]] = []
    for (edge_type, src, dst), sites in sorted(edge_counts.items()):
        edge = {"type": edge_type, "src": ids[src], "dst": ids[dst],
                "resolver": "scip-python" if edge_type in {"IMPLEMENTS", "OVERRIDES"} else "ast+scip",
                "confidence": 0.95}
        if edge_type == "CALLS": edge["call_sites"] = sites
        if edge_type == "INHERITS_FROM": edge["mandatory"] = False
        edges.append(edge)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("symbols.jsonl", rows), ("edges.jsonl", edges)):
        with (out / name).open("w", encoding="utf-8", newline="\n") as handle:
            for row in data:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n")
    return {"symbols": len(rows), "edges": len(edges), "edges_by_type": Counter(e["type"] for e in edges),
            "resolution_attempts": attempts, "resolution_successes": successes,
            "seconds": time.monotonic() - started}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-reindex", action="store_true")
    args = parser.parse_args()
    stats = extract_repository(args.repo, args.out, reindex=not args.no_reindex)
    print(json.dumps(stats, default=dict, sort_keys=True))


if __name__ == "__main__":
    main()
