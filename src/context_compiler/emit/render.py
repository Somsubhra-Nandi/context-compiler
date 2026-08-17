"""Emission (spec Sec 7.1-Sec 7.4, invariants I4 and I6).

``emit()`` turns Item 5's ``Context`` into the string the model receives. It
renders; it does not decide. Three consequences worth stating up front, because
each one is a rule this module follows rather than a style preference:

* **No cost is recomputed.** Every number in the header, and every ``[L2, 47
  tokens]`` figure in a block header, is read from Item 5's ``Context`` or from
  the sidecar it was budgeted against. A second implementation of ``cost()``
  would drift from the first and the header would start lying again.

* **Mandatory identities are all rendered, always.** Sec 7.3 makes them the
  non-truncatable tier, and ``Structural closure: complete`` is only a true
  statement if that holds. ``emit()`` asserts the count it rendered matches the
  count it was given -- silently dropping one is the failure mode the assertion
  exists to make impossible.

* **Framing is minimised, and Amendment A4.1 is what makes it fit.** I4 requires
  ``actual_emitted_tokens <= budgeted_tokens``. Two real sources of slack fund it
  -- import and class-header hoisting (Sec 7.2: "emission-time dedup can only
  shrink output"), and the ``prov`` term charged for seeds, which by Sec 7.4 get
  no trailer -- but Item 6 measured them as not enough on their own: ``cost()``
  charged a flat 40 for "the context header" and nothing for the per-file
  structure Sec 7.1's grouping produces, so 11 of 50 Django contexts came in over
  budget. A4.1 fixed the under-count in ``graph.budget.cost()`` directly (a
  framing term keyed on emitted-symbol and distinct-file counts), which is why
  emission itself does not need a separate framing budget any more.

Everything structural is a ``#`` comment, so a file group is valid Python and an
L3 block plus its class shell parses on its own (``RenderedBlock.parse_unit``).
Provenance uses an ASCII ``<-`` rather than Sec 7.4's Unicode arrow -- the same
information for a third of the tokens. Identity lines are the one place where
rendering is fixed rather than chosen: they are byte-identical to the string the
extractor costed, em dash included, because ``cost()`` charges exactly that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence

from ..extract.representations import count_tokens
from ..graph.budget import mandatory_identities, refs_at
from ..graph.closure import L2, L3, Level, Reason
from ..graph.sidecar import SymbolMeta
from .source import SymbolRecord, TextSource

#: A canonical repr string starts with ``needed_imports()`` when it needs any.
IMPORT_PREFIXES = ("import ", "from ")

SEEDS = "seeds"
DEPENDENCIES = "dependencies"
OPTIONAL = "optional context"
MANDATORY_IDENTITIES = "mandatory identities"
IDENTITY_HINTS = "identity hints"

#: Provenance edges the packer writes carry this prefix (``pack.pack``).
OPTIONAL_EDGE_PREFIX = "OPTIONAL:"


class ProvenanceStyle:
    """How much of a node's derivation to render.

    ``COMPACT`` is Sec 7.4's default: one line, the first rule that pulled the
    node in. ``VERBOSE`` renders every recorded ``Reason``. Full derivation
    *chains* are neither -- they live in Item 7's ``explain_inclusion``, which is
    a separate call and not budget-bound.
    """

    COMPACT = "compact"
    VERBOSE = "verbose"


class ContextLike(Protocol):
    """The Item 5 output shape ``emit()`` reads. See ``graph.compile.Context``."""

    status: str
    budget: int
    levels: Mapping[int, Level]
    provenance: Mapping[int, Sequence[Reason]]
    seeds: Mapping[int, Level]
    cost: int
    hint_tokens: int
    deficit: int
    suggestion: str


# -- text assembly primitives -------------------------------------------


def split_imports(text: str) -> tuple[list[str], str]:
    """Peel the canonical leading import block off a repr string.

    ``canonical_l2`` / ``canonical_l3`` prepend ``needed_imports()`` followed by
    exactly one blank line. **Only that shape is peeled**: a leading run of
    import statements, a blank line, and a non-empty remainder. Anything else
    comes back whole, because a wrong guess would move code into a group header.

    An import statement may span several lines. ``discover_file`` stores the whole
    source segment, so a parenthesised ``from x import (\\n  A,\\n  B,\\n)`` arrives
    as one multi-line entry -- and those are the expensive ones, so they are
    exactly the ones worth deduping. Continuation lines are consumed until the
    parentheses balance, and each statement is returned as a single entry so
    dedup compares whole statements rather than fragments.
    """
    if not text.startswith(IMPORT_PREFIXES):
        return [], text
    lines = text.split("\n")
    statements: list[str] = []
    cut = 0
    while cut < len(lines) and lines[cut].startswith(IMPORT_PREFIXES):
        start = cut
        depth = lines[cut].count("(") - lines[cut].count(")")
        cut += 1
        while depth > 0 and cut < len(lines):
            depth += lines[cut].count("(") - lines[cut].count(")")
            cut += 1
        if depth > 0:
            return [], text  # unbalanced: not a shape we understand
        statements.append("\n".join(lines[start:cut]))
    if not statements or cut >= len(lines) or lines[cut].strip():
        return [], text
    body = "\n".join(lines[cut + 1 :])
    if not body.strip():
        return [], text
    return statements, body


def _short(fqn: str) -> str:
    """Last two dotted components -- enough to read, cheap to render.

    Safe because a provenance ``via`` is always itself emitted: a node only
    propagates from L2 or above, and a packed node's ``via`` is one of its
    seeds. The full FQN is therefore in the document already, as that node's own
    block header.
    """
    parts = fqn.rsplit(".", 2)
    return ".".join(parts[-2:]) if len(parts) > 1 else fqn


def split_class_shell(text: str, kind: str) -> tuple[str | None, str]:
    """Peel an enclosing ``class X:`` header off a member's canonical text.

    ``canonical_l2`` / ``canonical_l3`` re-emit the enclosing class header in
    front of every method, so two methods of one class each carry their own copy
    of it. Merging them under a single header is the same category of saving as
    import dedup, and Sec 7.2 blesses it for the same reason: it can only shrink
    the output, so I4 stays an upper bound.

    Returns ``(shell, remainder)``, or ``(None, text)`` when the text is not a
    class-wrapped member. The shell is only peeled when every remaining line is
    indented or blank -- that is what makes re-attaching it under one header an
    exact reconstruction rather than a guess.

    Restricted to ``kind == "method"``, because that is exactly when
    ``canonical_l2`` prepends an *enclosing* class header. For a class symbol the
    header is the symbol itself, and hoisting it would put the class above its
    own annotation.
    """
    if kind != "method" or not text.startswith("class "):
        return None, text
    head, _, rest = text.partition("\n")
    if not head.rstrip().endswith(":") or not rest.strip():
        return None, text
    for line in rest.split("\n"):
        if line and not line.startswith((" ", "\t")):
            return None, text
    return head, rest


@dataclass
class Block:
    """One emitted symbol, ready to render."""

    node: int
    level: Level
    record: SymbolRecord
    imports: list[str]
    body: str
    reasons: Sequence[Reason]
    is_seed: bool
    budgeted_tokens: int
    shell: str | None = None
    shell_body: str = ""
    owner: str = ""

    @property
    def leaf(self) -> str:
        return self.record.fqn.rsplit(".", 1)[-1]


@dataclass
class ClassGroup:
    """Members of one class, sharing a single ``class X:`` header."""

    shell: str | None
    owner: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class FileGroup:
    """Sec 7.1's grouping unit. Carries the imports hoisted out of its members."""

    file: str
    blocks: list[Block] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    classes: list[ClassGroup] = field(default_factory=list)


@dataclass
class SectionTokens:
    """Per-section token counts, for the results doc.

    These are counted by tokenising each section's text on its own, so they sum
    to within a token or two of the whole rather than exactly: the tokeniser is
    not additive across a boundary. The authoritative figure is
    ``EmittedContext.tokens``, which tokenises the assembled string once.
    """

    header: int = 0
    seeds: int = 0
    dependencies: int = 0
    optional: int = 0
    mandatory_identities: int = 0
    identity_hints: int = 0

    def total(self) -> int:
        return (
            self.header
            + self.seeds
            + self.dependencies
            + self.optional
            + self.mandatory_identities
            + self.identity_hints
        )


@dataclass(frozen=True)
class RenderedBlock:
    """What emission actually wrote for one symbol.

    ``parse_unit`` is the smallest self-contained Python snippet containing the
    block: its class shell, when it has one, plus its text. A method body on its
    own is indented and does not parse, which is the point of hoisting the shell
    -- so the Item 1 ``ast.parse()`` check extends to *this*, not to the raw
    fragment.
    """

    node: int
    level: Level
    fqn: str
    file: str
    header: str
    text: str
    shell: str | None = None

    @property
    def parse_unit(self) -> str:
        return f"{self.shell}\n{self.text}" if self.shell else self.text


@dataclass
class EmittedContext:
    """The Item 6 deliverable: rendered text plus the I4 evidence."""

    text: str
    tokens: int
    budgeted_tokens: int
    budget: int
    status: str
    profile: str
    order: list[int] = field(default_factory=list)
    blocks: list[RenderedBlock] = field(default_factory=list)
    seeds: list[int] = field(default_factory=list)
    mandatory_identities: list[int] = field(default_factory=list)
    hints: list[int] = field(default_factory=list)
    identity_index_truncated: bool = False
    dedup_saved_tokens: int = 0
    dedup_saved_lines: int = 0
    files: int = 0
    sections: SectionTokens = field(default_factory=SectionTokens)
    seeks: int = 0
    seek_seconds: float = 0.0

    @property
    def token_margin(self) -> int:
        """I4: must be ``<= 0``. Positive means something upstream under-counted."""
        return self.tokens - self.budgeted_tokens

    @property
    def margin_fraction(self) -> float:
        return self.token_margin / self.budgeted_tokens if self.budgeted_tokens else 0.0

    def __str__(self) -> str:  # pragma: no cover - reporting only
        return (
            f"{self.status}  {len(self.order)} symbols  "
            f"{self.tokens:,}/{self.budget:,} tokens  margin {self.token_margin:+,}"
        )


# -- rendering -----------------------------------------------------------


def _header(
    context: ContextLike,
    profile_label: str,
    emitted: Sequence[Block],
    n_mandatory: int,
    n_hints: int,
) -> list[str]:
    """Sec 7's header. Every number is read, none is recomputed."""
    declarations = sum(1 for b in emitted if b.level == L2)
    bodies = sum(1 for b in emitted if b.level >= L3)
    closure = "complete"
    if context.status.startswith("DEMOTED"):
        suffix = f"{profile_label}, demoted from P3 FULL"
    else:
        suffix = profile_label
    return [
        f"Compiled context · {len(emitted)} symbols · "
        f"{context.cost + context.hint_tokens:,} / {context.budget:,} tokens",
        f"{declarations} declarations · {bodies} bodies · "
        f"{n_mandatory + n_hints} identities",
        f"Structural closure: {closure} ({suffix})",
    ]


def _provenance(block: Block, source: TextSource) -> str:
    """Sec 7.4's trailer, compact: the first rule that pulled this node in.

    Rendered as a suffix on the block's own header line rather than a line of its
    own. That is not cosmetic -- the framing budget is the ``prov`` term of
    ``cost()``, and a second line per emitted symbol does not fit inside it. The
    ``(rule: ...)`` clause moves to ``verbose_provenance`` for the same reason;
    it is recoverable from the edge type and the two levels, which the header
    already shows.
    """
    if block.is_seed or not block.reasons:
        return ""
    reason = block.reasons[0]
    via = source.record(reason.via)
    name = _short(via.fqn) if via is not None else str(reason.via)
    return f"  <- {name}  {reason.edge}"


def _block_lines(
    block: Block,
    source: TextSource,
    style: str = ProvenanceStyle.COMPACT,
    *,
    name: str | None = None,
    body: str | None = None,
) -> list[str]:
    """One emitted symbol: its header line, then its canonical text."""
    label = name if name is not None else block.leaf
    lines = [
        f"# {label}  [{Level(block.level).name}, {block.budgeted_tokens}t]"
        + _provenance(block, source)
    ]
    if style == ProvenanceStyle.VERBOSE and not block.is_seed:
        for reason in block.reasons:
            via = source.record(reason.via)
            named = _short(via.fqn) if via is not None else str(reason.via)
            lines.append(f"#   <- {named}  {reason.edge}  (rule: {reason.rule})")
    lines.append((block.body if body is None else body).rstrip("\n"))
    return lines


def _group_lines(
    group: FileGroup, source: TextSource, style: str, rendered: list[RenderedBlock]
) -> list[str]:
    """Render one file group: path, hoisted imports, then its class groups.

    The path line is what makes the leaf names in the block headers sufficient: a
    Python FQN is the module path plus the leaf, so printing the module prefix on
    every block would be paying twice for the same information.
    """
    lines = [f"# {group.file}"]
    lines.extend(group.imports)
    for cls in group.classes:
        if cls.shell is not None:
            lines.append("")
            lines.append(cls.shell)
        for block in cls.blocks:
            if cls.shell is None:
                lines.append("")
            body = block.shell_body if cls.shell is not None else block.body
            block_lines = _block_lines(block, source, style, body=body)
            lines.extend(block_lines)
            rendered.append(
                RenderedBlock(
                    node=block.node,
                    level=block.level,
                    fqn=block.record.fqn,
                    file=block.record.file,
                    header=block_lines[0],
                    text=body.rstrip("\n"),
                    shell=cls.shell,
                )
            )
    return lines


def _class_groups(blocks: Sequence[Block]) -> list[ClassGroup]:
    """Merge same-class members inside one file group.

    Keyed on ``(shell, owner)`` rather than the shell line alone: two nested
    ``class Meta:`` blocks in one file render identical headers, and they are not
    the same class.
    """
    groups: dict[tuple[str | None, str], ClassGroup] = {}
    for block in blocks:
        key = (block.shell, block.owner)
        group = groups.get(key)
        if group is None:
            group = groups[key] = ClassGroup(shell=block.shell, owner=block.owner)
        group.blocks.append(block)
    # Shell-less blocks first, then classes in first-appearance order.
    return sorted(
        groups.values(),
        key=lambda g: (g.shell is not None, g.blocks[0].record.start_line),
    )


def _section(title: str, count: int | None = None) -> str:
    suffix = f" ({count})" if count is not None else ""
    return f"# --- {title}{suffix} ---"


def _group_by_file(blocks: Iterable[Block]) -> list[FileGroup]:
    """Sec 7.1's grouping: a model reading two methods from one file sees them
    together rather than interleaved with unrelated modules."""
    groups: dict[str, FileGroup] = {}
    for block in blocks:
        groups.setdefault(block.record.file, FileGroup(block.record.file)).blocks.append(
            block
        )
    for group in groups.values():
        group.blocks.sort(key=lambda b: (b.record.start_line, b.record.fqn))
        group.classes = _class_groups(group.blocks)
    return [groups[f] for f in sorted(groups)]


def _hoist_imports(groups: Sequence[FileGroup], seen: set[str]) -> tuple[int, int]:
    """Move each group's import lines into its header, once, globally deduped.

    Sec 7.2: "emission-time dedup can only shrink output below the budgeted
    figure, which is what makes I4 an upper bound rather than an estimate." This
    is that dedup, and it is the main thing paying for the framing. ``seen`` is
    threaded across sections so a line rendered under the seeds' file group is
    not rendered again under a dependency's.

    Returns ``(lines_saved, tokens_saved)``.
    """
    saved_lines = 0
    saved_tokens = 0
    for group in groups:
        for block in group.blocks:
            for line in block.imports:
                if line in seen:
                    saved_lines += 1
                    saved_tokens += count_tokens(line + "\n")
                    continue
                seen.add(line)
                group.imports.append(line)
    return saved_lines, saved_tokens


def _classify(
    context: ContextLike, blocks: Mapping[int, Block]
) -> tuple[list[Block], list[Block], list[Block]]:
    """Split emitted blocks into Sec 7.1's three content sections.

    A *dependency* is a non-seed reached by a mandatory rule fired from a seed --
    the "direct L2 dependencies" of Sec 7.1. Everything else emitted got there
    through packing, or through a rule fired from a packed node, and belongs in
    the optional section: it is context the budget bought, not context the task
    required.
    """
    seed_ids = set(context.seeds)
    seeds: list[Block] = []
    dependencies: list[Block] = []
    optional: list[Block] = []
    for node, block in blocks.items():
        if block.is_seed:
            seeds.append(block)
            continue
        direct = any(
            r.via in seed_ids and not r.edge.startswith(OPTIONAL_EDGE_PREFIX)
            for r in block.reasons
        )
        (dependencies if direct else optional).append(block)
    return seeds, dependencies, optional


def _identity_lines(
    nodes: Sequence[int], source: TextSource
) -> tuple[list[str], list[int]]:
    """Render ``fqn — file:line`` per node, in FQN order. Never truncates."""
    pairs = []
    for node in nodes:
        rec = source.record(node)
        if rec is None:
            continue
        pairs.append((rec.fqn, node, rec.identity()))
    pairs.sort()
    return [p[2] for p in pairs], [p[1] for p in pairs]


def _exceeded(context: ContextLike, source: TextSource, profile_label: str) -> EmittedContext:
    """Sec 6.2's first-class failure, rendered as something a caller can act on.

    Not an empty context and not an exception: the deficit and the floor profile
    are the product feature -- *your task's mandatory dependency floor does not
    fit; here is by how much and which seeds caused it.* A3.2's corrected
    suggestion applies, because the P0 floor is bounded by the seeds' own
    declarations rather than by the breadth of the closure beneath them.
    """
    seed_lines, seed_ids = _identity_lines(list(context.seeds), source)
    lines = [
        f"Compiled context · {context.status}",
        f"Mandatory dependency floor is {context.cost:,} tokens at {profile_label}; "
        f"budget is {context.budget:,} (deficit {context.deficit:,}).",
        f"Suggestion: {context.suggestion}.",
        "",
        _section(SEEDS, len(seed_ids)),
        *seed_lines,
    ]
    text = "\n".join(lines) + "\n"
    return EmittedContext(
        text=text,
        tokens=count_tokens(text),
        budgeted_tokens=context.cost + context.hint_tokens,
        budget=context.budget,
        status=context.status,
        profile=profile_label,
        seeds=seed_ids,
        seeks=source.stats.seeks,
        seek_seconds=source.stats.seconds,
    )


def emit(
    context: ContextLike,
    source: TextSource,
    sidecar: Mapping[int, SymbolMeta],
    *,
    provenance: str = ProvenanceStyle.COMPACT,
    verbose_provenance: bool = False,
) -> EmittedContext:
    """Render ``context`` (spec Sec 7). Selects nothing, budgets nothing.

    ``sidecar`` supplies the budgeted per-node token counts the block headers
    quote and the ``repr_*_refs`` lists ``mandatory_identities()`` reads. Both
    are Item 5's numbers, read rather than recomputed.
    """
    if verbose_provenance:
        provenance = ProvenanceStyle.VERBOSE
    profile = getattr(context, "profile", None)
    profile_label = (
        f"{profile.name} {getattr(profile, 'label', '')}".strip() if profile else "-"
    )

    if not getattr(context, "ok", context.status != "CLOSURE_BUDGET_EXCEEDED"):
        return _exceeded(context, source, profile_label)

    seed_ids = set(context.seeds)
    blocks: dict[int, Block] = {}
    naive_tokens = 0
    for node, level in context.levels.items():
        if level < L2:
            continue
        rec = source.record(node)
        meta = sidecar.get(node)
        if rec is None or meta is None:
            continue
        text = rec.text(level)
        naive_tokens += count_tokens(text)
        imports, body = split_imports(text)
        shell, shell_body = split_class_shell(body, rec.kind)
        blocks[node] = Block(
            node=node,
            level=Level(level),
            record=rec,
            imports=imports,
            body=body,
            reasons=list(context.provenance.get(node, ())),
            is_seed=node in seed_ids,
            budgeted_tokens=meta.repr_L3_tokens if level >= L3 else meta.repr_L2_tokens,
            shell=shell,
            shell_body=shell_body,
            owner=rec.fqn.rsplit(".", 1)[0],
        )

    seeds, dependencies, optional = _classify(context, blocks)
    seed_groups = _group_by_file(seeds)
    dep_groups = _group_by_file(dependencies)
    opt_groups = _group_by_file(optional)

    # One global dedup pass, in output order, so an import is rendered under the
    # first file group that needs it and never again.
    seen: set[str] = set()
    saved_lines = 0
    saved_tokens = 0
    for groups in (seed_groups, dep_groups, opt_groups):
        dl, dt = _hoist_imports(groups, seen)
        saved_lines += dl
        saved_tokens += dt
        for group in groups:
            for cls in group.classes:
                if cls.shell is None:
                    continue
                # every member past the first no longer repeats `class X:`
                saved_lines += len(cls.blocks) - 1
                saved_tokens += (len(cls.blocks) - 1) * count_tokens(cls.shell + "\n")

    mandatory = mandatory_identities(context.levels, sidecar)
    mandatory_lines, mandatory_ids = _identity_lines(sorted(mandatory), source)
    assert len(mandatory_ids) == len(mandatory), (
        "Sec 7.3: mandatory identities are never dropped",
        len(mandatory_ids),
        len(mandatory),
    )

    hint_index = getattr(context, "hints", None)
    hint_nodes = list(hint_index.nodes) if hint_index is not None else []
    hint_lines, hint_ids = _identity_lines(hint_nodes, source)

    sections = SectionTokens()
    parts: list[str] = []

    def add(title: str, lines: Sequence[str], attr: str, count: int | None = None) -> None:
        if not lines:
            return
        rendered = ([_section(title, count)] if title else []) + list(lines)
        text = "\n".join(rendered)
        setattr(sections, attr, count_tokens(text))
        parts.append(text)

    header = _header(context, profile_label, list(blocks.values()), len(mandatory_ids), len(hint_ids))
    add("", header, "header")

    rendered: list[RenderedBlock] = []
    seed_order: list[int] = []
    for title, groups, attr in (
        (SEEDS, seed_groups, "seeds"),
        (DEPENDENCIES, dep_groups, "dependencies"),
        (OPTIONAL, opt_groups, "optional"),
    ):
        lines: list[str] = []
        for group in groups:
            if lines:
                lines.append("")
            lines.extend(_group_lines(group, source, provenance, rendered))
        if title == SEEDS:
            # Read back off `rendered`, so document order has one definition.
            seed_order = [b.node for b in rendered]
        count = sum(len(g.blocks) for g in groups)
        add(title, lines, attr, count or None)
    order = [b.node for b in rendered]

    add(MANDATORY_IDENTITIES, mandatory_lines, "mandatory_identities", len(mandatory_lines))
    add(IDENTITY_HINTS, hint_lines, "identity_hints", len(hint_lines))

    text = "\n\n".join(parts) + "\n"
    return EmittedContext(
        text=text,
        tokens=count_tokens(text),
        budgeted_tokens=context.cost + context.hint_tokens,
        budget=context.budget,
        status=context.status,
        profile=profile_label,
        order=order,
        blocks=rendered,
        seeds=seed_order,
        mandatory_identities=mandatory_ids,
        hints=hint_ids,
        identity_index_truncated=bool(hint_index is not None and hint_index.truncated),
        dedup_saved_tokens=saved_tokens,
        dedup_saved_lines=saved_lines,
        files=len(seed_groups) + len(dep_groups) + len(opt_groups),
        sections=sections,
        seeks=source.stats.seeks,
        seek_seconds=source.stats.seconds,
    )


# -- the Sec 7.3 completeness check --------------------------------------


def unresolved_references(
    context: ContextLike, sidecar: Mapping[int, SymbolMeta]
) -> list[tuple[int, int]]:
    """``(node, ref)`` pairs an emitted symbol names but the context cannot resolve.

    Empty is the property Sec 7.3 advertises: every FQN appearing textually in
    emitted text is either emitted itself or carries an identity line. It should
    hold by construction, because ``mandatory_identities()`` is defined as
    exactly ``refs - emitted`` -- which is why this is worth checking rather than
    assuming. A non-empty result means emission and the cost model disagree about
    what was emitted.
    """
    emitted = {n for n, lv in context.levels.items() if lv >= L2}
    charged = mandatory_identities(context.levels, sidecar)
    out: list[tuple[int, int]] = []
    for node in emitted:
        meta = sidecar.get(node)
        if meta is None:
            continue
        for ref in refs_at(meta, Level(context.levels[node])):
            if ref in emitted or ref in charged:
                continue
            if ref not in sidecar:
                continue  # not a symbol this repo knows; no identity line exists
            out.append((node, ref))
    return out
