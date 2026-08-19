"""Focused tests for the isolated global-vector Arm A baseline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from context_compiler.baseline.arm_a import (  # noqa: E402
    MODEL_ID,
    MODEL_REVISION,
    VectorIndex,
    VectorIndexError,
    load_vector_index,
    query_vector,
    rank_candidates,
    run_arm_a,
    sha256_file,
)
from context_compiler.graph.closure import L2, L3  # noqa: E402
from context_compiler.graph.sidecar import SymbolMeta  # noqa: E402
from context_compiler.emit import MappingTextSource, SymbolRecord, emit  # noqa: E402


def meta(node, *, t2=10, t3=100, r2=(), r3=(), file="pkg/mod.py"):
    return SymbolMeta(
        fqn=f"pkg.s{node}",
        kind="function",
        file=file,
        repr_L2_tokens=t2,
        repr_L3_tokens=t3,
        repr_L2_refs=tuple(r2),
        repr_L3_refs=tuple(r3),
        identity_tokens=4,
        provenance_tokens=2,
        evaluable=None,
    )


def vector_index(ids, vectors):
    embeddings = np.asarray(vectors, dtype=np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    node_ids = np.asarray(ids, dtype=np.int64)
    return VectorIndex(
        embeddings=embeddings,
        ids=node_ids,
        metadata={},
        row_by_id={int(node): row for row, node in enumerate(node_ids)},
    )


def text_source(sidecar):
    return MappingTextSource(
        {
            node: SymbolRecord(
                id=node,
                fqn=item.fqn,
                kind=item.kind,
                file=item.file,
                start_line=node,
                repr_L2_text=f"def s{node}(): ...",
                repr_L3_text=f"def s{node}():\n    return {node}\n",
            )
            for node, item in sidecar.items()
        }
    )


def test_cosine_ranking_excludes_seeds_and_breaks_ties_by_node_id():
    index = vector_index([1, 2, 3, 4], [[1, 0], [1, 0], [1, 0], [0, 1]])

    ranked = rank_candidates(index, [1])

    assert [node for node, _score in ranked] == [2, 3, 4]
    assert 1 not in {node for node, _score in ranked}


def test_seed_query_is_deterministic_normalized_arithmetic_mean():
    index = vector_index([1, 2, 3], [[1, 0], [0, 1], [-1, 0]])

    first = query_vector(index, [1, 2])
    second = query_vector(index, [1, 2])

    np.testing.assert_array_equal(first, second)
    np.testing.assert_allclose(first, [2**-0.5, 2**-0.5], rtol=1e-6)
    assert np.isclose(np.linalg.norm(first), 1.0)


def test_seeds_stay_l3_are_not_readmitted_and_no_duplicates_occur():
    sidecar = {node: meta(node) for node in (1, 2, 3)}
    index = vector_index([1, 2, 3], [[1, 0], [1, 0], [0, 1]])

    context = run_arm_a([1, 1], sidecar, index, text_source(sidecar), budget=8_000)

    assert context.levels[1] == L3
    assert context.seeds == {1: L3}
    assert context.stats.candidates == 2
    assert len(context.levels) == len(set(context.levels))
    assert all(level == L2 for node, level in context.levels.items() if node != 1)


def test_budget_never_exceeded_and_large_candidate_is_skipped_for_later_small_one():
    sidecar = {
        1: meta(1, t3=100),
        2: meta(2, t2=1_000),
        3: meta(3, t2=10),
    }
    index = vector_index([1, 2, 3], [[1, 0], [1, 0], [0.5, 0.5]])

    source = text_source(sidecar)
    context = run_arm_a([1], sidecar, index, source, budget=400)

    assert 2 not in context.levels
    assert context.levels[3] == L2
    assert context.stats.skipped_too_large == 1
    assert context.total_tokens() <= context.budget
    assert emit(context, source, sidecar).tokens <= context.budget


def test_arm_a_does_not_call_closure_or_graph(monkeypatch):
    import importlib

    closure_module = importlib.import_module("context_compiler.graph.closure")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closure machinery was called")

    monkeypatch.setattr(closure_module, "closure", forbidden)
    sidecar = {node: meta(node) for node in (1, 2)}
    index = vector_index([1, 2], [[1, 0], [0, 1]])

    context = run_arm_a([1], sidecar, index, text_source(sidecar), budget=8_000)

    assert context.levels == {1: L3, 2: L2}


def write_index(tmp_path: Path, ids=(1, 2), vectors=((1.0, 0.0), (0.0, 1.0))):
    symbols = tmp_path / "symbols.jsonl"
    records = []
    sidecar = {}
    for node in ids:
        records.append(
            {
                "id": node,
                "fqn": f"pkg.s{node}",
                "kind": "function",
                "file": "pkg/mod.py",
                "start_line": node,
                "repr_L2_text": f"def s{node}(): ...",
                "repr_L3_text": f"def s{node}(): return {node}",
                "repr_L2_tokens": 10,
                "repr_L3_tokens": 20,
                "repr_L2_refs": [],
                "repr_L3_refs": [],
                "identity_tokens": 4,
                "provenance_tokens": 2,
                "evaluable": None,
            }
        )
        sidecar[node] = meta(node)
    symbols.write_text("".join(json.dumps(record) + "\n" for record in records))
    embeddings = np.asarray(vectors, dtype=np.float32)
    np.save(tmp_path / "embeddings.npy", embeddings, allow_pickle=False)
    np.save(tmp_path / "ids.npy", np.asarray(ids, dtype=np.int64), allow_pickle=False)
    metadata = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "sentence_transformers_version": "test",
        "transformers_version": "test",
        "torch_version": "test",
        "cuda_version": "test",
        "embedding_dimension": embeddings.shape[1],
        "dtype": "float32",
        "normalized": True,
        "number_of_vectors": len(ids),
        "input_symbols_sha256": sha256_file(symbols),
        "ids_sha256": sha256_file(tmp_path / "ids.npy"),
        "embeddings_sha256": sha256_file(tmp_path / "embeddings.npy"),
        "max_sequence_length": 2048,
        "truncation": True,
        "batch_size": 16,
        "creation_timestamp": "test",
        "command": "test",
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    return symbols, sidecar, metadata


def test_corrupt_or_mismatched_metadata_is_rejected(tmp_path):
    symbols, sidecar, metadata = write_index(tmp_path)
    metadata["model_revision"] = "wrong"
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(VectorIndexError, match="model ID/revision"):
        load_vector_index(tmp_path, symbols, sidecar)


def test_embedding_row_node_id_alignment_is_validated(tmp_path):
    symbols, sidecar, metadata = write_index(tmp_path, ids=(2, 1))
    # Artifacts are internally hashed, but row order violates ascending node ID.
    metadata["ids_sha256"] = sha256_file(tmp_path / "ids.npy")
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(VectorIndexError, match="row/node-ID mapping"):
        load_vector_index(tmp_path, symbols, sidecar)


def test_nonfinite_or_unnormalized_embeddings_are_rejected(tmp_path):
    symbols, sidecar, metadata = write_index(tmp_path, vectors=((2.0, 0.0), (0.0, 1.0)))
    metadata["embeddings_sha256"] = sha256_file(tmp_path / "embeddings.npy")
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(VectorIndexError, match="not L2-normalized"):
        load_vector_index(tmp_path, symbols, sidecar)
