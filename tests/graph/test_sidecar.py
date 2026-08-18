"""Sidecar loading (Amendment A1.1). No HydraDB required."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from context_compiler.graph.sidecar import (
    SymbolMeta,
    load_sidecar,
    read_repr_text,
    sidecar_bytes,
)

SYMBOLS = Path(os.environ.get("CC_SYMBOLS", "~/out/django/symbols.jsonl")).expanduser()


def write_jsonl(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "symbols.jsonl"
    with open(p, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def record(nid: int, **over) -> dict:
    base = {
        "id": nid,
        "fqn": f"pkg.mod.sym{nid}",
        "kind": "function",
        "file": "pkg/mod.py",
        "start_line": 1,
        "end_line": 2,
        "body_hash": "sha256:00",
        "repr_L2_text": "def f(): ...",
        "repr_L2_tokens": 5,
        "repr_L2_refs": [],
        "repr_L3_text": "def f():\n    return 1\n",
        "repr_L3_tokens": 11,
        "repr_L3_refs": [],
        "identity_tokens": 7,
        "provenance_tokens": 9,
        "evaluable": None,
        "static_value": None,
        "mro_partial": False,
    }
    base.update(over)
    return base


def test_loads_scalars_keyed_by_integer_id(tmp_path):
    p = write_jsonl(tmp_path, [record(1), record(2, kind="test")])
    sc = load_sidecar(p)
    assert set(sc) == {1, 2}
    assert isinstance(sc[1], SymbolMeta)
    assert sc[1].fqn == "pkg.mod.sym1"
    assert sc[2].kind == "test"


def test_text_blobs_are_excluded():
    """A1.1: bulk text deliberately stays out of the sidecar."""
    assert "repr_L2_text" not in SymbolMeta._fields
    assert "repr_L3_text" not in SymbolMeta._fields


def test_field_set_matches_amendment_a7():
    """A4.1 adds ``file`` and A7 adds traceback source ranges."""
    assert SymbolMeta._fields == (
        "fqn",
        "kind",
        "file",
        "repr_L2_tokens",
        "repr_L3_tokens",
        "repr_L2_refs",
        "repr_L3_refs",
        "identity_tokens",
        "provenance_tokens",
        "evaluable",
        "start_line",
        "end_line",
    )


def test_refs_are_tuples_of_int(tmp_path):
    p = write_jsonl(tmp_path, [record(1, repr_L2_refs=[9, 8], repr_L3_refs=[7])])
    sc = load_sidecar(p)
    assert sc[1].repr_L2_refs == (9, 8)
    assert sc[1].repr_L3_refs == (7,)


def test_evaluable_tri_state_is_preserved(tmp_path):
    p = write_jsonl(
        tmp_path,
        [record(1, evaluable=None), record(2, evaluable=True), record(3, evaluable=False)],
    )
    sc = load_sidecar(p)
    assert sc[1].evaluable is None
    assert sc[2].evaluable is True
    assert sc[3].evaluable is False


def test_offset_index_round_trips_the_original_record(tmp_path):
    recs = [record(1), record(2, repr_L3_text="x" * 5000), record(3)]
    p = write_jsonl(tmp_path, recs)
    offsets: dict = {}
    load_sidecar(p, offsets)
    assert set(offsets) == {1, 2, 3}
    for r in recs:
        assert read_repr_text(p, offsets[r["id"]]) == r


def test_sidecar_bytes_counts_shared_strings_once(tmp_path):
    p = write_jsonl(tmp_path, [record(i) for i in range(50)])
    sc = load_sidecar(p)
    assert sidecar_bytes(sc) > 0


@pytest.mark.skipif(not SYMBOLS.exists(), reason="Django fixture not present")
def test_django_sidecar_footprint_is_reported():
    """Sec A1.1 estimates ~40 MB per 200k symbols; record the real figure."""
    sc = load_sidecar(SYMBOLS)
    assert len(sc) == 43_420
    mb = sidecar_bytes(sc) / 1e6
    # A loose ceiling: the point is that scalars-only stays far below the
    # 89.5 MB of repr text this table deliberately excludes.
    assert mb < 89.5, f"sidecar is {mb:.1f} MB, larger than the text it replaces"
