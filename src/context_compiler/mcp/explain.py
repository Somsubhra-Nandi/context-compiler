"""``explain_inclusion`` (Item 7 Sec 4): the full derivation chain, not a trailer.

Emission's Sec 7.4 trailer is one line, the first rule that pulled a node in --
that is what fits the framing budget. This is a separate, non-budget-bound call:
it walks every recorded ``Reason`` back through its own ``via`` until reaching a
seed, which is the actual answer to "how do you know this is the right context."

Provenance chains are shallow by construction (I1 bounds mandatory depth at two
productive hops from an L3 seed, and a packed bundle's own induced closure is
bounded the same way), but the walk carries a depth guard and a visited set
regardless -- cheap insurance against a future propagation rule that isn't.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..emit.render import OPTIONAL_EDGE_PREFIX
from ..graph.budget import refs_at
from ..graph.closure import L2, Level
from ..graph.compile import Context
from ..graph.sidecar import SymbolMeta

DEFAULT_MAX_CHAIN_DEPTH = 8


@dataclass
class ChainStep:
    fqn: str
    edge: str
    rule: str
    depth: int


@dataclass
class InclusionExplanation:
    fqn: str
    present: bool
    level: str = ""
    tokens: int = 0
    emitted: bool = False
    is_seed: bool = False
    chain: list[ChainStep] = field(default_factory=list)
    would_include_via: list[str] = field(default_factory=list)


def _referencing_emitted(
    node: int, context: Context, sidecar: Mapping[int, SymbolMeta]
) -> list[int]:
    """Emitted symbols whose canonical text names ``node``, in fqn order."""
    out = []
    for n, level in context.levels.items():
        if level < L2:
            continue
        meta = sidecar.get(n)
        if meta is not None and node in refs_at(meta, Level(level)):
            out.append(n)
    out.sort(key=lambda n: sidecar[n].fqn)
    return out


def explain_inclusion(
    node: int,
    context: Context,
    sidecar: Mapping[int, SymbolMeta],
    *,
    max_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
) -> InclusionExplanation:
    meta = sidecar.get(node)
    fqn = meta.fqn if meta is not None else str(node)
    level = context.levels.get(node)

    if level is None:
        referencing = _referencing_emitted(node, context, sidecar)
        return InclusionExplanation(
            fqn=fqn,
            present=False,
            would_include_via=[sidecar[n].fqn for n in referencing],
        )

    is_seed = node in context.seeds
    emitted = level >= L2
    tokens = 0
    if meta is not None:
        if level >= 3:
            tokens = meta.repr_L3_tokens
        elif level == L2:
            tokens = meta.repr_L2_tokens
        else:
            tokens = meta.identity_tokens

    chain: list[ChainStep] = []
    if not is_seed:
        seen: set[int] = set()
        frontier = [(node, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if current in seen or depth >= max_depth:
                continue
            seen.add(current)
            for reason in context.provenance.get(current, ()):
                via_meta = sidecar.get(reason.via)
                via_fqn = via_meta.fqn if via_meta is not None else str(reason.via)
                chain.append(
                    ChainStep(fqn=via_fqn, edge=reason.edge, rule=reason.rule, depth=depth + 1)
                )
                if reason.via not in context.seeds:
                    frontier.append((reason.via, depth + 1))

    return InclusionExplanation(
        fqn=fqn,
        present=True,
        level=Level(level).name,
        tokens=tokens,
        emitted=emitted,
        is_seed=is_seed,
        chain=chain,
    )


def render_explanation(exp: InclusionExplanation) -> str:
    if not exp.present:
        lines = [f"{exp.fqn}  -- not in the compiled context."]
        if exp.would_include_via:
            lines.append("Referenced (as an identity, not emitted) by:")
            lines.extend(f"  <- {f}" for f in exp.would_include_via)
        else:
            lines.append(
                "Not referenced anywhere in the current context. "
                "Pass it as an explicit seed to compile_context to include it."
            )
        return "\n".join(lines)

    header = f"{exp.fqn}  [{exp.level}, {exp.tokens}t]" if exp.emitted else f"{exp.fqn}  [{exp.level}]"
    lines = [header]
    if exp.is_seed:
        lines.append("  seed -- not pulled in by a rule")
    elif exp.chain:
        for step in exp.chain:
            lines.append(f"  <- {step.fqn}  {step.edge}  (rule: {step.rule})")
        mandatory = [s for s in exp.chain if not s.edge.startswith(OPTIONAL_EDGE_PREFIX)]
        optional = [s for s in exp.chain if s.edge.startswith(OPTIONAL_EDGE_PREFIX)]
        if mandatory:
            n = len(mandatory)
            lines.append(f"  included because: {n} mandatory rule{'s' if n != 1 else ''} fired")
        if optional:
            lines.append(f"  also reached via optional packing ({len(optional)} bundle step(s))")
    else:
        lines.append("  included via optional packing (no mandatory rule fired)")
    lines.append("  runtime-confirmed: no evidence yet (Item 9)")
    return "\n".join(lines)
