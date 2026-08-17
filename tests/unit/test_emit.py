"""Item 6 emission semantics on a hand-built repository. No HydraDB, no I/O.

The fixture below is a miniature repo with real Python text, so every token
count in the sidecar is produced by the same ``count_tokens()`` the emitter
uses. That is deliberate: an I4 check against invented token counts would only
prove the fixture is self-consistent. Here the sidecar and the emitted string
are two views of the same strings, exactly as they are on Django.
"""
from __future__ import annotations

import ast

import pytest

from context_compiler.emit import (
    EmittedContext,
    MappingTextSource,
    ProvenanceStyle,
    SymbolRecord,
    emit,
    split_imports,
)
from context_compiler.emit.render import (
    IDENTITY_HINTS,
    MANDATORY_IDENTITIES,
    OPTIONAL,
    SEEDS,
    _section,
    unresolved_references,
)
from context_compiler.extract.representations import count_tokens
from context_compiler.graph.budget import mandatory_identities
from context_compiler.graph.closure import L1, L2, L3, Level
from context_compiler.graph.compile import EXCEEDED, OK, Compiler
from context_compiler.graph.expand import HARD_EDGES
from context_compiler.graph.pack import StaticCallerSource
from context_compiler.graph.profiles import P0, P1, P2, P3
from context_compiler.graph.sidecar import SymbolMeta


# -- fixture repository --------------------------------------------------


class Sym:
    """One fixture symbol: real text in, consistent scalars out."""

    def __init__(self, node, fqn, file, line, l2, l3, r2=(), r3=(), kind="method"):
        self.node = node
        self.fqn = fqn
        self.file = file
        self.line = line
        self.l2 = l2
        self.l3 = l3
        self.r2 = tuple(r2)
        self.r3 = tuple(r3)
        self.kind = kind

    def meta(self) -> SymbolMeta:
        identity = f"{self.fqn} — {self.file}:{self.line}"
        provenance = f"{self.fqn} [extracted: ast+scip]"
        return SymbolMeta(
            fqn=self.fqn,
            kind=self.kind,
            repr_L2_tokens=count_tokens(self.l2),
            repr_L3_tokens=count_tokens(self.l3),
            repr_L2_refs=self.r2,
            repr_L3_refs=self.r3,
            identity_tokens=count_tokens(identity),
            provenance_tokens=count_tokens(provenance),
            evaluable=None,
        )

    def record(self) -> SymbolRecord:
        return SymbolRecord(
            id=self.node,
            fqn=self.fqn,
            kind=self.kind,
            file=self.file,
            start_line=self.line,
            repr_L2_text=self.l2,
            repr_L3_text=self.l3,
        )


REFRESH, LOGIN, ROTATE, VALIDATE, CLOCK, NOW, AUDIT = 101, 102, 201, 202, 301, 401, 501

POLICY_IMPORT = "from pkg.util import now"
CLOCK_IMPORT = "from pkg.clock import Clock"


def _fixture_repo() -> list[Sym]:
    return [
        Sym(
            REFRESH,
            "pkg.service.AuthService.refresh",
            "pkg/service.py",
            18,
            l2=(
                f"{CLOCK_IMPORT}\n\nclass AuthService:\n"
                "    def refresh(self, token) -> str: ...\n"
                '    """Issue a replacement token."""\n'
            ),
            l3=(
                f"{CLOCK_IMPORT}\n\nclass AuthService:\n"
                "    def refresh(self, token) -> str:\n"
                '        """Issue a replacement token."""\n'
                "        policy = TokenPolicy(Clock())\n"
                "        policy.validate(token)\n"
                "        return policy.rotate(token)\n"
            ),
            r2=(CLOCK,),
            r3=(ROTATE, VALIDATE, CLOCK),
        ),
        Sym(
            LOGIN,
            "pkg.service.AuthService.login",
            "pkg/service.py",
            9,
            l2=(
                f"{CLOCK_IMPORT}\n\nclass AuthService:\n"
                "    def login(self, user, password) -> str: ...\n"
            ),
            l3=(
                f"{CLOCK_IMPORT}\n\nclass AuthService:\n"
                "    def login(self, user, password) -> str:\n"
                "        token = self.refresh(user.token)\n"
                "        return token\n"
            ),
            r2=(CLOCK,),
            r3=(REFRESH, CLOCK),
        ),
        Sym(
            ROTATE,
            "pkg.policy.TokenPolicy.rotate",
            "pkg/policy.py",
            41,
            l2=(
                f"{POLICY_IMPORT}\n\nclass TokenPolicy:\n"
                "    def rotate(self, token) -> str: ...\n"
            ),
            l3=(
                f"{POLICY_IMPORT}\n\nclass TokenPolicy:\n"
                "    def rotate(self, token) -> str:\n"
                "        return f'{token}:{now()}'\n"
            ),
            r2=(),
            r3=(NOW,),
        ),
        Sym(
            VALIDATE,
            "pkg.policy.TokenPolicy.validate",
            "pkg/policy.py",
            22,
            l2=(
                f"{POLICY_IMPORT}\n\nclass TokenPolicy:\n"
                "    def validate(self, token) -> bool: ...\n"
            ),
            l3=(
                f"{POLICY_IMPORT}\n\nclass TokenPolicy:\n"
                "    def validate(self, token) -> bool:\n"
                "        return bool(token) and now() > 0\n"
            ),
            r2=(),
            r3=(NOW,),
        ),
        Sym(
            CLOCK,
            "pkg.clock.Clock",
            "pkg/clock.py",
            5,
            l2="class Clock:\n    def now(self) -> float: ...\n",
            l3="class Clock:\n    def now(self) -> float:\n        return 0.0\n",
            kind="class",
        ),
        Sym(
            NOW,
            "pkg.util.now",
            "pkg/util.py",
            3,
            l2="def now() -> float: ...\n",
            l3="def now() -> float:\n    return 0.0\n",
            kind="function",
        ),
        Sym(
            AUDIT,
            "pkg.audit.record_login",
            "pkg/audit.py",
            11,
            l2="def record_login(user) -> None: ...\n",
            l3="def record_login(user) -> None:\n    pass\n",
            kind="function",
        ),
    ]


EDGES = [
    (REFRESH, "CALLS", ROTATE),
    (REFRESH, "CALLS", VALIDATE),
    (REFRESH, "REFERENCES_TYPE", CLOCK),
    (ROTATE, "CALLS", NOW),
    (VALIDATE, "CALLS", NOW),
    (LOGIN, "CALLS", REFRESH),
    (AUDIT, "CALLS", REFRESH),
]


class StubExpander:
    def __init__(self, edges):
        self.by_src: dict[int, list[tuple[str, int]]] = {}
        for src, et, dst in edges:
            self.by_src.setdefault(src, []).append((et, dst))

        class _S:
            round_trips = 0

        self.stats = _S()

    def __call__(self, frontier):
        self.stats.round_trips += len(HARD_EDGES)
        out = []
        for n in frontier:
            out.extend((n, et, dst) for et, dst in self.by_src.get(n, ()))
        return out


class StubReverse:
    def __init__(self, edges):
        self.by_dst: dict[tuple[str, int], list[int]] = {}
        for src, et, dst in edges:
            self.by_dst.setdefault((et, dst), []).append(src)
        self.round_trips = 0

    def read(self, edge_type, node):
        self.round_trips += 1
        return list(self.by_dst.get((edge_type, node), ()))


@pytest.fixture
def repo():
    syms = _fixture_repo()
    sidecar = {s.node: s.meta() for s in syms}
    source = MappingTextSource({s.node: s.record() for s in syms})
    return sidecar, source


def _out_degrees(edges):
    out: dict[int, int] = {}
    for src, _et, _dst in edges:
        out[src] = out.get(src, 0) + 1
    return out


def compile_fixture(sidecar, budget=2000, seeds=(REFRESH,), profiles=None):
    compiler = Compiler(
        sidecar=sidecar,
        expander=StubExpander(EDGES),
        reverse=StubReverse(EDGES),
        degrees=_out_degrees(EDGES),
        sources=(StaticCallerSource(),),
        profiles=profiles or (P3, P2, P1, P0),
    )
    return compiler.compile_context(list(seeds), budget)


# -- split_imports -------------------------------------------------------


def test_split_imports_peels_the_canonical_block():
    imports, body = split_imports("import os\nfrom x import y\n\ndef f():\n    pass\n")
    assert imports == ["import os", "from x import y"]
    assert body == "def f():\n    pass\n"


def test_split_imports_leaves_a_body_that_merely_starts_with_from():
    """`from` inside a body is not an import block: no blank-line terminator."""
    text = "from x import y\ndef f():\n    pass\n"
    assert split_imports(text) == ([], text)


def test_split_imports_leaves_an_import_only_text_whole():
    """Peeling everything would leave an empty block, so nothing is peeled."""
    text = "import os\n\n"
    assert split_imports(text) == ([], text)


def test_split_imports_is_a_no_op_without_imports():
    text = "def f():\n    pass\n"
    assert split_imports(text) == ([], text)


# -- Sec 7.1 ordering ----------------------------------------------------


def test_seeds_come_first_dependencies_next_hints_last(repo):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)

    seeds_at = out.text.index(_section(SEEDS, 1))
    deps_at = out.text.index(_section("dependencies", 3))
    hints_at = out.text.index(_section(IDENTITY_HINTS, len(out.hints)))
    assert seeds_at < deps_at < hints_at
    assert out.text.index("Compiled context") < seeds_at, "header first"
    assert hints_at > out.text.rindex("# pkg/"), "identity sections are the tail"
    assert out.order[0] == REFRESH, "the seed is the first symbol rendered"


def test_dependencies_are_grouped_by_file(repo):
    """Sec 7.1: two methods from one file are rendered together."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)

    # rotate and validate both live in pkg/policy.py and must share one group
    deps = out.text.split(_section("dependencies", 3), 1)[1].split("# ---", 1)[0]
    assert deps.count("# pkg/policy.py") == 1, "one group per file per section"
    group = deps.split("# pkg/policy.py", 1)[1].split("# pkg/", 1)[0]
    assert "rotate" in group and "validate" in group
    # and both are rendered adjacently, not interleaved with pkg/clock.py
    assert "clock" not in group


def test_order_covers_exactly_the_emitted_set(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    assert set(out.order) == ctx.emitted()
    assert len(out.order) == len(set(out.order)), "no symbol is rendered twice"


# -- Sec 7.3 identity sections -------------------------------------------


def test_every_mandatory_identity_is_rendered(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)

    expected = mandatory_identities(ctx.levels, sidecar)
    assert set(out.mandatory_identities) == expected
    for node in expected:
        assert source.record(node).identity() in out.text


def test_identity_lines_are_byte_identical_to_the_costed_string(repo):
    """I4 depends on this: `cost()` charges `identity_tokens` for this exact line."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    for node in out.mandatory_identities:
        rendered = source.record(node).identity()
        assert count_tokens(rendered) == sidecar[node].identity_tokens


def test_hint_truncation_sets_the_flag(repo):
    """Only hints may be cut, and cutting them must be visible."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    ctx.hints.truncated = True
    out = emit(ctx, source, sidecar)
    assert out.identity_index_truncated is True

    ctx.hints.truncated = False
    assert emit(ctx, source, sidecar).identity_index_truncated is False


def test_mandatory_identities_cannot_be_truncated(repo):
    """There is no code path that drops one; the emitter asserts the count."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    assert len(out.mandatory_identities) == len(
        mandatory_identities(ctx.levels, sidecar)
    )
    assert out.identity_index_truncated == bool(ctx.hints.truncated)


def test_no_emitted_reference_is_unresolvable(repo):
    """Sec 7.3's claim: every name in emitted text resolves in the artifact."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    assert unresolved_references(ctx, sidecar) == []


# -- Sec 7.4 provenance --------------------------------------------------


def test_every_non_seed_carries_a_provenance_line(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)

    for node in out.order:
        header = _block_header(out.text, source.record(node))
        if node in ctx.seeds:
            assert "  <- " not in header, ("a seed needs no provenance", header)
        else:
            assert "  <- " in header, (sidecar[node].fqn, header)


def test_provenance_names_the_rule_and_the_via(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    assert "  <- AuthService.refresh  CALLS" in out.text


def test_verbose_provenance_is_opt_in(repo):
    """Sec 7.4: one line by default. Full chains are Item 7's explain_inclusion."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    compact = emit(ctx, source, sidecar)
    verbose = emit(ctx, source, sidecar, verbose_provenance=True)
    assert verbose.tokens >= compact.tokens
    assert compact.text.count("#   <-") <= verbose.text.count("#   <-")
    assert emit(
        ctx, source, sidecar, provenance=ProvenanceStyle.VERBOSE
    ).text == verbose.text


# -- Sec 7.2 dedup -------------------------------------------------------


def test_dedup_shrinks_output_when_two_symbols_share_an_import(repo):
    """rotate and validate both need `from pkg.util import now`; it is rendered once."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    assert out.text.count(POLICY_IMPORT) == 1
    assert out.dedup_saved_lines >= 1
    assert out.dedup_saved_tokens >= count_tokens(POLICY_IMPORT + "\n")


def test_class_shell_is_hoisted_once_per_class(repo):
    """Two methods of one class share one `class X:` header (Sec 7.2 dedup)."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    policy = out.text.split("# pkg/policy.py", 1)[1].split("# ---", 1)[0]
    assert policy.count("class TokenPolicy:") == 1
    assert "def rotate" in policy and "def validate" in policy


def test_class_shell_is_not_hoisted_off_a_class_symbol(repo):
    """For a class, the header *is* the symbol; hoisting would invert the label."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    clock = out.text.split("# pkg/clock.py", 1)[1].split("# pkg/", 1)[0]
    assert clock.index("# Clock  [L2") < clock.index("class Clock:")


def test_dedup_only_ever_shrinks(repo):
    """Sec 7.2: dedup can only shrink output, which is what makes I4 a bound.

    Checked by counting occurrences: every duplicate import line and every
    duplicate class header in the *inputs* appears exactly once in the output.
    """
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)

    assert out.dedup_saved_tokens > 0 and out.dedup_saved_lines > 0
    for line in {POLICY_IMPORT, CLOCK_IMPORT, "class TokenPolicy:", "class AuthService:"}:
        occurrences_in_input = sum(
            block.text.count(line) + (block.shell or "").count(line)
            for block in out.blocks
        )
        assert out.text.count(line) <= max(1, occurrences_in_input)


# -- I4 ------------------------------------------------------------------
#
# `token_margin <= 0` does NOT hold, and the cause is upstream of emission. See
# docs/spikes/emit-item-6-results.md Sec 4: Sec 6.2's `cost()` charges a flat
# `HEADER_TOKENS = 40` for the context header and nothing at all for the rest of
# the model-visible structure -- the per-file group headers Sec 7.1's grouping
# requires, the section markers, and the part of each block header that does not
# fit inside `provenance_tokens`. Measured on 50 Django contexts: median +341
# tokens (+4.7%), max +622 (+7.9%).
#
# These two tests are `xfail(strict=True)` rather than deleted or loosened, so
# they turn into a hard pass the moment a framing term lands in `cost()` and a
# hard failure if someone marks them fixed without fixing them.



@pytest.mark.xfail(strict=True, reason="I4 under-count in Sec 6.2 cost(); see results doc Sec 4")
def test_token_margin_is_not_positive(repo):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    assert out.token_margin <= 0, (out.tokens, out.budgeted_tokens)


@pytest.mark.xfail(strict=True, reason="I4 under-count in Sec 6.2 cost(); see results doc Sec 4")
def test_token_margin_is_not_positive_multi_seed(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar, seeds=(REFRESH, LOGIN), budget=3000)
    out = emit(ctx, source, sidecar)
    assert out.token_margin <= 0, (out.tokens, out.budgeted_tokens)


def test_framing_overhead_stays_within_the_measured_bound(repo):
    """The bound the amendment would charge. A regression here is a real one."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    assert out.token_margin <= out.framing_allowance, (
        out.token_margin,
        len(out.order),
    )


@pytest.mark.parametrize("budget", [1200, 1600, 2000, 4000, 8000])
def test_framing_overhead_holds_across_budgets(repo, budget):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar, budget=budget), source, sidecar)
    assert out.token_margin <= out.framing_allowance, (budget, out.token_margin)


@pytest.mark.parametrize("profile", [P3, P2, P1, P0])
def test_framing_overhead_holds_on_every_profile(repo, profile):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar, profiles=(profile,)), source, sidecar)
    assert out.token_margin <= out.framing_allowance, (profile.name, out.token_margin)


def test_multi_seed_renders_both_seeds(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar, seeds=(REFRESH, LOGIN), budget=3000)
    out = emit(ctx, source, sidecar)
    assert set(out.seeds) == {REFRESH, LOGIN}
    assert out.token_margin <= out.framing_allowance


# -- the assembled artifact still parses ---------------------------------


def _block_header(text: str, record) -> str:
    """The one header line emission writes for a symbol."""
    for line in text.split("\n"):
        if not line.startswith("# "):
            continue
        label, _, rest = line[2:].partition("  [")
        if not rest:
            continue
        if label == record.fqn.rsplit(".", 1)[-1] or label == ".".join(
            record.fqn.rsplit(".", 3)[-2:]
        ):
            return line
    raise AssertionError(f"no block header for {record.fqn} in\n{text}")





def test_every_emitted_l3_block_parses(repo):
    """Extends the Item 1 check from one representation to assembled output.

    The unit is the block plus its hoisted class shell: a method body on its own
    is indented and cannot parse, and the shell is what emission delivers with it.
    """
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    bodies = [b for b in out.blocks if b.level >= L3]
    assert bodies, "the fixture must emit at least one body"
    for block in bodies:
        ast.parse(block.parse_unit)


def test_every_emitted_block_parses_including_declarations(repo):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    for block in out.blocks:
        ast.parse(block.parse_unit)


def test_every_block_header_is_present_in_the_document(repo):
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    for block in out.blocks:
        assert block.header in out.text
        assert block.text.rstrip("\n") in out.text


def test_a_file_group_is_valid_python(repo):
    """Framing is all `#` comments, so hoisted imports plus bodies still parse."""
    sidecar, source = repo
    out = emit(compile_fixture(sidecar), source, sidecar)
    group = out.text.split("# pkg/policy.py", 1)[1].split("# ---", 1)[0]
    group = group.split("# pkg/", 1)[0]
    ast.parse(group)


# -- header --------------------------------------------------------------


def test_header_reports_the_input_numbers_not_recomputed_ones(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar)
    out = emit(ctx, source, sidecar)
    first = out.text.split("\n", 1)[0]
    assert f"{ctx.cost + ctx.hint_tokens:,} / {ctx.budget:,} tokens" in first
    assert f"{len(ctx.emitted())} symbols" in first
    assert "Structural closure: complete (P3 FULL)" in out.text


def test_header_names_the_demotion(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar, profiles=(P1, P0))
    ctx.status = "DEMOTED:P1"
    out = emit(ctx, source, sidecar)
    assert "Structural closure: complete (P1 MINIMAL, demoted from P3 FULL)" in out.text


# -- Sec 6.2's first-class failure ---------------------------------------


def test_exceeded_renders_a_useful_message(repo):
    sidecar, source = repo
    ctx = compile_fixture(sidecar, budget=90)
    assert ctx.status == EXCEEDED

    out = emit(ctx, source, sidecar)
    assert out.status == EXCEEDED
    assert out.text.strip(), "not an empty context"
    assert EXCEEDED in out.text
    assert "reduce the seed count or raise the budget" in out.text
    assert f"deficit {ctx.deficit:,}" in out.text
    assert "P0 FLOOR" in out.text
    # the seeds are named, so the caller knows what to cut
    assert source.record(REFRESH).identity() in out.text
    assert out.seeds == [REFRESH]


def test_exceeded_does_not_emit_bodies(repo):
    """The floor did not fit; rendering it anyway would be the bug."""
    sidecar, source = repo
    ctx = compile_fixture(sidecar, budget=90)
    out = emit(ctx, source, sidecar)
    assert "def refresh" not in out.text
    assert out.order == []


# -- multi-line imports --------------------------------------------------


MULTILINE_IMPORT = "from pkg.constants import (\n    ALPHA,\n    BETA,\n)"


def test_split_imports_keeps_a_parenthesised_import_whole():
    """`discover_file` stores the whole source segment, so these arrive multi-line."""
    text = f"{MULTILINE_IMPORT}\nimport os\n\ndef f():\n    return ALPHA\n"
    imports, body = split_imports(text)
    assert imports == [MULTILINE_IMPORT, "import os"]
    assert body == "def f():\n    return ALPHA\n"


def test_split_imports_bails_on_an_unbalanced_parenthesis():
    text = "from pkg import (\n    ALPHA,\n"
    assert split_imports(text) == ([], text)


def test_multiline_imports_are_deduped_as_whole_statements():
    """These are the expensive imports, so they are the ones worth deduping."""
    syms = _fixture_repo()
    for sym in syms:
        if sym.file == "pkg/policy.py":
            sym.l2 = sym.l2.replace(POLICY_IMPORT, MULTILINE_IMPORT)
            sym.l3 = sym.l3.replace(POLICY_IMPORT, MULTILINE_IMPORT)
    sidecar = {s.node: s.meta() for s in syms}
    source = MappingTextSource({s.node: s.record() for s in syms})
    out = emit(compile_fixture(sidecar), source, sidecar)
    assert out.text.count("from pkg.constants import (") == 1
    assert out.dedup_saved_tokens >= count_tokens(MULTILINE_IMPORT + "\n")
