"""AST symbol discovery and occurrence classification."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Symbol:
    fqn: str
    kind: str
    path: str
    module: str
    node: ast.AST
    parent_class: str | None = None
    source: str = ""
    imports: dict[str, str] = field(default_factory=dict)


def module_name(repo: Path, path: Path) -> str:
    relative = path.relative_to(repo).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[0] in {"src", "lib"}:
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def discover_file(repo: Path, path: Path) -> tuple[list[Symbol], ast.Module]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path), type_comments=True)
    module = module_name(repo, path)
    relative = path.relative_to(repo).as_posix()
    imports: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            line = ast.get_source_segment(source, stmt) or ast.unparse(stmt)
            for alias in stmt.names:
                imports[alias.asname or alias.name.split(".")[0]] = line
        elif isinstance(stmt, ast.ImportFrom):
            line = ast.get_source_segment(source, stmt) or ast.unparse(stmt)
            for alias in stmt.names:
                imports[alias.asname or alias.name] = line
    module_node = Symbol(module, "module", relative, module, tree, source=source, imports=imports)
    symbols = [module_node]

    def walk(body: list[ast.stmt], parents: list[str], parent_class: str | None = None) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fqn = ".".join([module, *parents, node.name])
                is_test = node.name.startswith("test_") or "/test" in f"/{relative}"
                kind = "test" if is_test else "method" if parent_class else "function"
                symbols.append(Symbol(fqn, kind, relative, module, node, parent_class, source, imports))
            elif isinstance(node, ast.ClassDef):
                fqn = ".".join([module, *parents, node.name])
                symbols.append(Symbol(fqn, "class", relative, module, node, parent_class, source, imports))
                walk(node.body, [*parents, node.name], fqn)
            elif not parents and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and (target.id.isupper() or isinstance(node, ast.AnnAssign)):
                        symbols.append(Symbol(f"{module}.{target.id}", "constant", relative, module,
                                              node, None, source, imports))
    walk(tree.body, [])
    return symbols, tree


def qualified_name(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        parent = qualified_name(expr.value)
        return f"{parent}.{expr.attr}" if parent else expr.attr
    return None


def _own_scope(node: ast.AST) -> list[ast.AST]:
    """Every descendant of ``node``, without descending into a nested
    ``FunctionDef``/``AsyncFunctionDef``/``ClassDef``'s own body.

    ``ast.walk`` cannot do this: it enqueues every descendant up front, so a
    ``continue`` in a caller's loop only skips classifying the node it was
    given -- it cannot stop already-queued grandchildren from being yielded
    later. A nested def has its own ``Symbol`` and its own occurrence scan;
    a call two scopes down inside one belongs to it, never to whatever
    contains it (Amendment A5.2/A6).
    """
    out: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        out.append(child)
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.extend(_own_scope(child))
    return out


def occurrence_nodes(symbol: Symbol) -> list[tuple[str, ast.AST]]:
    """Return (edge type, identifier AST node) within a symbol's own scope.

    A class's own scope is its decorators, bases and class-level statements --
    not its methods' bodies. A module's own scope is its imports, module-level
    statements and decorators -- not its functions'. A function's own scope is
    its own signature and body -- not any function or class nested inside it.
    """
    out: list[tuple[str, ast.AST]] = []
    root = symbol.node
    if isinstance(root, ast.ClassDef):
        out.extend(("REFERENCES_TYPE", base) for base in root.bases)
        out.extend(("DECORATED_BY", decorator) for decorator in root.decorator_list)
    elif isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        annotations = [a.annotation for a in root.args.args + root.args.kwonlyargs if a.annotation]
        if root.args.vararg and root.args.vararg.annotation: annotations.append(root.args.vararg.annotation)
        if root.args.kwarg and root.args.kwarg.annotation: annotations.append(root.args.kwarg.annotation)
        if root.returns: annotations.append(root.returns)
        out.extend(("REFERENCES_TYPE", annotation) for annotation in annotations)
        out.extend(("DECORATED_BY", decorator) for decorator in root.decorator_list)
    for node in _own_scope(root):
        if isinstance(node, ast.Call):
            out.append(("CALLS", node.func))
    return out
