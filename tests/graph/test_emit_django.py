"""Item 6 emission against the real Django graph and the real offset index.

Lives under ``tests/graph/`` rather than ``tests/unit/`` because it needs
HydraDB and a 117 MB ``symbols.jsonl``; ``tests/unit`` stays fixtures-only and
sub-second. ``CC_TRIALS`` bounds the number of compiled contexts (default 50,
which is what the results doc quotes).

The one figure this file exists to produce is the I4 margin distribution:
``actual_emitted_tokens - budgeted_tokens`` over real compiled contexts. Item 6
found it **positive** on 11 of 50 contexts, with the cause upstream of emission
-- see docs/spikes/emit-item-6-results.md Sec 4. Amendment A4.1 fixed the
under-count in ``graph.budget.cost()`` directly, so ``token_margin <= 0`` is now
a hard invariant here rather than an xfail.
"""
from __future__ import annotations

import ast
import os
import statistics
from pathlib import Path

import pytest

from context_compiler.emit import emit, unresolved_references
from context_compiler.emit.source import OffsetTextSource, source_from_symbols
from context_compiler.graph.budget import mandatory_identities
from context_compiler.graph.client import GraphClient
from context_compiler.graph.closure import L2, L3
from context_compiler.graph.compile import Compiler
from context_compiler.graph.expand import HARD_EDGES, Expander, ReverseReader
from context_compiler.graph.sidecar import load_degree_tables, load_sidecar
from context_compiler.graph.validate import eligible_seeds, sample_seed_sets

SYMBOLS = Path("~/out/django/symbols.jsonl").expanduser()
EDGES = Path("~/out/django/edges.jsonl").expanduser()
BUDGET = 8000
TRIALS = int(os.environ.get("CC_TRIALS", "50"))

pytestmark = pytest.mark.skipif(
    not SYMBOLS.exists() or not EDGES.exists(),
    reason="needs the Django extraction in ~/out/django",
)


@pytest.fixture(scope="module")
def django():
    sidecar = load_sidecar(SYMBOLS)
    degrees, in_degrees = load_degree_tables(EDGES, tuple(HARD_EDGES))
    source = source_from_symbols(SYMBOLS)
    client = GraphClient()
    client.verify()
    with Expander(client, membership=sidecar) as expander:
        with ReverseReader(client, membership=sidecar) as reverse:
            compiler = Compiler(
                sidecar=sidecar,
                expander=expander,
                reverse=reverse,
                degrees=degrees,
                in_degrees=in_degrees,
            )
            yield compiler, sidecar, source
    client.close()


@pytest.fixture(scope="module")
def contexts(django):
    """``TRIALS`` compiled-and-emitted contexts, computed once."""
    compiler, sidecar, source = django
    sets = sample_seed_sets(eligible_seeds(SYMBOLS), TRIALS, 6)
    out = []
    for seeds in sets:
        ctx = compiler.compile_context(seeds, BUDGET)
        out.append((ctx, emit(ctx, source, sidecar)))
    return out


def test_contexts_were_produced(contexts):
    assert len(contexts) == TRIALS
    assert all(e.tokens > 0 for _c, e in contexts)


def test_token_margin_is_not_positive(contexts):
    """I4: budgeted cost must be an upper bound on emitted cost."""
    positive = sorted(
        (e.token_margin, e.margin_fraction) for _c, e in contexts if e.token_margin > 0
    )
    assert not positive, (
        f"{len(positive)}/{len(contexts)} contexts over budget; "
        f"median {statistics.median(m for m, _f in positive)}, max {positive[-1][0]}"
    )


def test_no_emitted_text_references_an_unresolvable_fqn(contexts, django):
    """Sec 7.3: every FQN in emitted text is emitted or carries an identity line."""
    _compiler, sidecar, _source = django
    for ctx, _e in contexts:
        assert unresolved_references(ctx, sidecar) == []


def test_every_mandatory_identity_is_rendered(contexts, django):
    """Sec 7.3's non-truncatable tier. Not one may be dropped."""
    _compiler, sidecar, _source = django
    for ctx, e in contexts:
        expected = mandatory_identities(ctx.levels, sidecar)
        assert set(e.mandatory_identities) == expected
        assert len(e.mandatory_identities) == len(expected)


def test_every_emitted_block_parses(contexts):
    """The Item 1 ``ast.parse()`` check, extended to assembled output."""
    failures = []
    for _ctx, e in contexts:
        for block in e.blocks:
            try:
                ast.parse(block.parse_unit)
            except SyntaxError as exc:
                failures.append((block.fqn, block.level.name, str(exc)))
    assert not failures, failures[:5]


def test_every_emitted_symbol_is_rendered_exactly_once(contexts):
    for ctx, e in contexts:
        assert set(e.order) == ctx.emitted()
        assert len(e.order) == len(set(e.order))


def test_seeds_are_rendered_first(contexts):
    for ctx, e in contexts:
        assert set(e.seeds) == {n for n in ctx.seeds if n in ctx.levels}
        assert e.order[: len(e.seeds)] == e.seeds


def test_identity_sections_are_the_tail(contexts):
    for _ctx, e in contexts:
        if not e.mandatory_identities:
            continue
        last_body = max(e.text.rindex(b.header) for b in e.blocks)
        assert e.text.index("# --- mandatory identities") > last_body


def test_dedup_saves_tokens_on_real_contexts(contexts):
    """Sec 7.2's shrink-only property, on real repeated imports and class headers."""
    saved = [e.dedup_saved_tokens for _c, e in contexts]
    assert all(s >= 0 for s in saved)
    assert statistics.median(saved) > 0


def test_offset_index_seeks_are_cheap(contexts):
    """Reported in the results doc: A2.1's replacement for graph-resident text."""
    per_seek = [
        (e.seek_seconds * 1000 / e.seeks) for _c, e in contexts if e.seeks
    ]
    assert per_seek, "the offset index must actually have been used"
    assert statistics.median(per_seek) < 5.0, statistics.median(per_seek)


def test_header_quotes_the_input_budget(contexts):
    for ctx, e in contexts:
        first = e.text.split("\n", 1)[0]
        assert f"{ctx.cost + ctx.hint_tokens:,} / {ctx.budget:,} tokens" in first
        assert "Structural closure: complete" in e.text


def test_offset_source_and_scan_agree(django):
    """``load_offsets`` on the ingest index and a direct scan must match."""
    _compiler, _sidecar, source = django
    index = Path("~/out/django/offsets.json").expanduser()
    if not index.exists():
        pytest.skip("no ingest --offset-index written")
    from context_compiler.emit.source import load_offsets

    path, offsets = load_offsets(index)
    assert path.name == "symbols.jsonl"
    sample = list(source.offsets.items())[:200]
    for node, off in sample:
        assert offsets[node].offset == off.offset
        assert offsets[node].length == off.length
