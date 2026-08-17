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


#: Amendment A2.2. Largest folded literal that gets inlined into
#: ``static_value`` and ``repr_L2_text``.
#:
#: The engine limit that surfaced this is incidental -- one Django constant
#: (``tests.validators.tests.INVALID_URLS``) folds to 66,517 bytes and broke
#: the default ingest, because a string property caps just under 32 KiB. The
#: real reason for the cap is that a 66 KB folded constant is *bad context*:
#: the model does not need 2,000 invalid URLs, it needs to know the constant is
#: a list of invalid URLs. Inlining it wastes budget the packer could spend on
#: a caller.
MAX_STATIC_VALUE_BYTES = 4096


@dataclass(frozen=True)
class Folded:
    evaluable: bool
    value: Any = None


@dataclass(frozen=True)
class Rendered:
    """A folded constant, after the A2.2 size decision.

    ``evaluable`` stays **true** for an over-cap value: the constant *is*
    statically evaluable, we are simply declining to inline it. Downgrading it
    to false would be a lie about the code, and would change which propagation
    row Sec 4 applies to it.

    ``literal`` is ``None`` when over cap. It is **never truncated** -- a
    clipped constant looks valid and is wrong, and I4's "token counts describe
    the canonical emitted representation" depends on stored text being exactly
    what was costed.
    """

    evaluable: bool
    literal: str | None = None
    size_bytes: int = 0

    @property
    def inlined(self) -> bool:
        return self.literal is not None

    @property
    def over_cap(self) -> bool:
        return self.evaluable and self.literal is None


def render_folded(folded: Folded | None, cap: int = MAX_STATIC_VALUE_BYTES) -> Rendered:
    """Apply the A2.2 cap to a fold result.

    ``None`` (no fold attempted) and a failed fold both render as
    non-evaluable with no literal.
    """
    if folded is None or not folded.evaluable:
        return Rendered(evaluable=False)
    literal = repr(folded.value)
    size = len(literal.encode())
    if size > cap:
        return Rendered(evaluable=True, literal=None, size_bytes=size)
    return Rendered(evaluable=True, literal=literal, size_bytes=size)


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
