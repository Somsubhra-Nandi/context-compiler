"""Safe offline constant folding without eval()."""

from __future__ import annotations

import ast
import logging
import operator
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow, ast.BitOr: operator.or_,
           ast.BitAnd: operator.and_, ast.BitXor: operator.xor,
           ast.LShift: operator.lshift, ast.RShift: operator.rshift}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert,
          ast.Not: operator.not_}


@dataclass(frozen=True)
class Folded:
    evaluable: bool
    value: Any = None


def _evaluate(node: ast.AST, resolve: Any) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_evaluate(v, resolve) for v in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else set(values) if isinstance(node, ast.Set) else values
    if isinstance(node, ast.Dict):
        return {_evaluate(k, resolve): _evaluate(v, resolve) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Name):
        return resolve(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_evaluate(node.left, resolve), _evaluate(node.right, resolve))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand, resolve))
    if isinstance(node, ast.BoolOp):
        values = [_evaluate(v, resolve) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    raise ValueError(f"unsafe or dynamic expression: {type(node).__name__}")


def fold_constants(definitions: dict[str, ast.AST]) -> dict[str, Folded]:
    result: dict[str, Folded] = {}
    visiting: list[str] = []

    def fold(name: str) -> Any:
        if name in result:
            if result[name].evaluable:
                return result[name].value
            raise ValueError(name)
        if name in visiting:
            cycle = visiting[visiting.index(name):]
            for member in cycle:
                result[member] = Folded(False)
            LOG.warning("constant cycle: %s", ", ".join(cycle))
            raise ValueError(name)
        if name not in definitions:
            raise ValueError(name)
        visiting.append(name)
        try:
            value = _evaluate(definitions[name], fold)
            result[name] = Folded(True, value)
            return value
        except Exception:
            result.setdefault(name, Folded(False))
            raise
        finally:
            visiting.pop()

    for constant in definitions:
        try:
            fold(constant)
        except Exception:
            pass
    return result
