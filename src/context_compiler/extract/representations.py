"""Canonical source reconstruction and token accounting."""

from __future__ import annotations

import ast
from functools import lru_cache

import tiktoken

from .ast_occurrences import Symbol


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _used_names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}


def needed_imports(symbol: Symbol) -> list[str]:
    used = _used_names(symbol.node)
    return sorted({line for name, line in symbol.imports.items() if name in used})


def _doc_first(node: ast.AST) -> str | None:
    doc = ast.get_docstring(node, clean=True)
    return doc.splitlines()[0] if doc else None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    clone = ast.FunctionDef(
        name=node.name, args=node.args, body=[ast.Expr(ast.Constant(Ellipsis))],
        decorator_list=[], returns=node.returns, type_comment=node.type_comment,
        type_params=getattr(node, "type_params", []),
    )
    text = ast.unparse(ast.fix_missing_locations(clone))
    return text.replace("\n    ...", " ...")


def _decorators(node: ast.AST) -> list[str]:
    return [f"@{ast.unparse(item)}" for item in getattr(node, "decorator_list", [])]


def own_class_members(node: ast.ClassDef) -> list[str]:
    members: list[str] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.append(_signature(item))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            members.append(f"{item.target.id}: {ast.unparse(item.annotation)}")
    return members


def canonical_l2(symbol: Symbol, class_surface: list[tuple[str, str]] | None = None) -> str:
    node = symbol.node
    imports = needed_imports(symbol)
    lines = imports + ([""] if imports else [])
    lines.extend(_decorators(node))
    if symbol.kind == "module":
        lines.append(f"# module {symbol.fqn}")
    elif symbol.kind == "constant":
        lines.append(ast.get_source_segment(symbol.source, node) or ast.unparse(node))
    elif isinstance(node, ast.ClassDef):
        bases = f"({', '.join(ast.unparse(base) for base in node.bases)})" if node.bases else ""
        lines.append(f"class {node.name}{bases}:")
        surface = class_surface if class_surface is not None else [(m, symbol.fqn) for m in own_class_members(node)]
        if not surface:
            lines.append("    ...")
        for member, owner in surface:
            suffix = "" if owner == symbol.fqn else f"  # from {owner}"
            lines.append(f"    {member}{suffix}")
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = _signature(node)
        doc = _doc_first(node)
        if symbol.parent_class:
            lines.append(f"class {symbol.parent_class.rsplit('.', 1)[-1]}:")
            lines.append(f"    {signature}")
            if doc: lines.append(f'    """{doc}"""')
        else:
            lines.append(signature)
            if doc: lines.append(f'"""{doc}"""')
    return "\n".join(lines).rstrip() + "\n"


def canonical_l3(symbol: Symbol, relevant_fields: list[str] | None = None) -> str:
    node = symbol.node
    imports = needed_imports(symbol)
    body = ast.get_source_segment(symbol.source, node) or ast.unparse(node)
    lines = imports + ([""] if imports else [])
    if symbol.parent_class:
        lines.append(f"class {symbol.parent_class.rsplit('.', 1)[-1]}:")
        for field in relevant_fields or []:
            lines.append(f"    {field}")
        lines.extend(f"    {line}" if line else "" for line in body.splitlines())
    else:
        lines.append(body)
    return "\n".join(lines).rstrip() + "\n"
