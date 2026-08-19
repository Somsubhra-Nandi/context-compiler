"""Baseline Arm A: global embedding-similarity retrieval without a graph.

One normalized vector represents each symbol's canonical ``repr_L2_text``.
For a trial, Arm A retrieves the precomputed normalized vectors for the supplied
L3 seeds, takes their arithmetic mean, and L2-normalizes that mean.  Every
non-seed symbol is then ranked globally by cosine similarity (a dot product),
with ascending node ID as the deterministic tie-break.  Candidates are scanned
in that order and admitted at L2 when the shared :func:`graph.budget.cost` fits
the full supplied budget.

This module intentionally contains no graph reader, closure call, propagation,
connectivity reranking, or compiler bundle logic.  NumPy is an optional
dependency: ordinary Context Compiler imports do not import this module.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from ..emit.render import ContextLike, TextSource, emit
from ..graph.budget import HintIndex, cost
from ..graph.closure import L2, L3, Level, Reason
from ..graph.sidecar import SymbolMeta, iter_symbols

if TYPE_CHECKING:  # NumPy remains optional for normal Context Compiler use.
    import numpy as np


MODEL_ID = "jinaai/jina-embeddings-v2-base-code"
MODEL_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"
OK = "OK"
EXCEEDED = "CLOSURE_BUDGET_EXCEEDED"


class VectorIndexError(ValueError):
    """The persistent vector index is corrupt or does not match its sidecar."""


@dataclass(frozen=True)
class ArmAProfile:
    """Display metadata consumed by the shared emitter."""

    name: str = "ARM_A"
    label: str = "VECTOR TOP-K, NO CLOSURE"


@dataclass
class ArmAStats:
    """Per-query retrieval and admission measurements."""

    candidates: int = 0
    admitted: int = 0
    skipped_too_large: int = 0
    floor_cost: int = 0
    query_seconds: float = 0.0
    ranking_seconds: float = 0.0
    retrieval_seconds: float = 0.0
    admission_seconds: float = 0.0
    emission_guard_seconds: float = 0.0
    trimmed_for_emission: int = 0
    guarded_emitted_tokens: int = 0
    seconds: float = 0.0


@dataclass
class ArmAContext(ContextLike):
    """Arm A output, intentionally compatible with the shared ``emit()``."""

    status: str
    budget: int
    levels: dict[int, Level] = field(default_factory=dict)
    provenance: dict[int, list[Reason]] = field(default_factory=dict)
    seeds: dict[int, Level] = field(default_factory=dict)
    profile: ArmAProfile = field(default_factory=ArmAProfile)
    hints: HintIndex | None = None
    cost: int = 0
    hint_tokens: int = 0
    deficit: int = 0
    suggestion: str = ""
    stats: ArmAStats = field(default_factory=ArmAStats)

    @property
    def ok(self) -> bool:
        return self.status != EXCEEDED

    def emitted(self) -> set[int]:
        return {node for node, level in self.levels.items() if level >= L2}

    def total_tokens(self) -> int:
        return self.cost + self.hint_tokens

    def utilisation(self) -> float:
        return self.total_tokens() / self.budget if self.budget else 0.0


@dataclass(frozen=True)
class VectorIndex:
    """Validated normalized embeddings and their deterministic row mapping."""

    embeddings: "np.ndarray"
    ids: "np.ndarray"
    metadata: Mapping[str, object]
    row_by_id: Mapping[int, int]


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Arm A requires the optional vector dependencies; install "
            "requirements-vector.txt"
        ) from exc
    return np


def sha256_file(path: str | Path) -> str:
    """Stream a file into SHA-256 without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_ids(symbols_path: Path) -> list[int]:
    ids = [int(record["id"]) for record, _offset, _length in iter_symbols(symbols_path)]
    if len(ids) != len(set(ids)):
        raise VectorIndexError("symbols.jsonl contains duplicate node IDs")
    return sorted(ids)


def load_vector_index(
    directory: str | Path,
    symbols_path: str | Path,
    sidecar: Mapping[int, SymbolMeta],
) -> VectorIndex:
    """Load and exhaustively validate an Arm A index.

    Validation binds the matrix to the exact input bytes, frozen model commit,
    sorted sidecar IDs, and artifact hashes.  It also verifies finiteness and
    unit norms before any retrieval is allowed.
    """
    np = _numpy()
    root = Path(directory).expanduser()
    symbols = Path(symbols_path).expanduser()
    try:
        metadata = json.loads((root / "metadata.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VectorIndexError(f"cannot read vector metadata: {exc}") from exc

    required = {
        "model_id",
        "model_revision",
        "sentence_transformers_version",
        "transformers_version",
        "torch_version",
        "cuda_version",
        "embedding_dimension",
        "dtype",
        "normalized",
        "number_of_vectors",
        "input_symbols_sha256",
        "ids_sha256",
        "embeddings_sha256",
        "max_sequence_length",
        "truncation",
        "batch_size",
        "creation_timestamp",
        "command",
    }
    missing = sorted(required - set(metadata))
    if missing:
        raise VectorIndexError(f"metadata missing required keys: {missing}")
    if metadata["model_id"] != MODEL_ID or metadata["model_revision"] != MODEL_REVISION:
        raise VectorIndexError("index model ID/revision does not match frozen Arm A")
    if metadata["normalized"] is not True:
        raise VectorIndexError("index metadata must declare normalized=true")
    if metadata["input_symbols_sha256"] != sha256_file(symbols):
        raise VectorIndexError("index input SHA-256 does not match symbols.jsonl")

    embeddings_path = root / "embeddings.npy"
    ids_path = root / "ids.npy"
    if metadata["ids_sha256"] != sha256_file(ids_path):
        raise VectorIndexError("ids.npy SHA-256 does not match metadata")
    if metadata["embeddings_sha256"] != sha256_file(embeddings_path):
        raise VectorIndexError("embeddings.npy SHA-256 does not match metadata")
    try:
        embeddings = np.load(embeddings_path, mmap_mode="r", allow_pickle=False)
        ids = np.load(ids_path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise VectorIndexError(f"cannot load vector artifacts: {exc}") from exc

    if embeddings.ndim != 2 or ids.ndim != 1:
        raise VectorIndexError("expected a 2-D embedding matrix and 1-D ID mapping")
    rows, dimension = embeddings.shape
    if rows != len(ids):
        raise VectorIndexError("embedding rows and node-ID rows differ")
    if rows != int(metadata["number_of_vectors"]):
        raise VectorIndexError("vector count does not match metadata")
    if dimension != int(metadata["embedding_dimension"]):
        raise VectorIndexError("embedding dimension does not match metadata")
    if str(embeddings.dtype) != str(metadata["dtype"]):
        raise VectorIndexError("embedding dtype does not match metadata")
    if embeddings.dtype != np.float32:
        raise VectorIndexError("Arm A embeddings must be float32")
    if not np.issubdtype(ids.dtype, np.integer):
        raise VectorIndexError("node-ID mapping must use an integer dtype")

    expected_ids = _expected_ids(symbols)
    actual_ids = [int(node) for node in ids]
    if actual_ids != expected_ids:
        raise VectorIndexError("row/node-ID mapping is not the sorted symbols.jsonl ID set")
    if actual_ids != sorted(sidecar):
        raise VectorIndexError("row/node-ID mapping does not align with the loaded sidecar")
    if not np.isfinite(embeddings).all():
        raise VectorIndexError("embedding matrix contains NaN or infinity")
    norms = np.linalg.norm(embeddings, axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-4):
        raise VectorIndexError("embedding rows are not L2-normalized")

    return VectorIndex(
        embeddings=embeddings,
        ids=ids,
        metadata=metadata,
        row_by_id={node: row for row, node in enumerate(actual_ids)},
    )


def query_vector(index: VectorIndex, seeds: list[int]):
    """Arithmetic mean of normalized seed vectors, normalized once more."""
    np = _numpy()
    if not seeds:
        raise ValueError("Arm A requires at least one seed")
    try:
        rows = [index.row_by_id[node] for node in seeds]
    except KeyError as exc:
        raise KeyError(f"seed id not in vector index: {exc.args[0]}") from exc
    query = np.asarray(index.embeddings[rows], dtype=np.float32).mean(axis=0)
    norm = float(np.linalg.norm(query))
    if not np.isfinite(norm) or norm <= 0.0:
        raise VectorIndexError("seed mean produced a non-finite or zero query vector")
    return np.asarray(query / norm, dtype=np.float32)


def rank_candidates(index: VectorIndex, seeds: list[int]):
    """Return ``(node_id, cosine)`` for every non-seed in fixed rank order."""
    np = _numpy()
    query = query_vector(index, seeds)
    scores = np.asarray(index.embeddings @ query, dtype=np.float32)
    if not np.isfinite(scores).all():
        raise VectorIndexError("cosine scoring produced NaN or infinity")
    seed_set = set(seeds)
    mask = np.fromiter((int(node) not in seed_set for node in index.ids), dtype=bool)
    candidate_ids = np.asarray(index.ids[mask], dtype=np.int64)
    candidate_scores = scores[mask]
    order = np.lexsort((candidate_ids, -candidate_scores))
    return [(int(candidate_ids[row]), float(candidate_scores[row])) for row in order]


def run_arm_a(
    seeds: list[int],
    sidecar: Mapping[int, SymbolMeta],
    index: VectorIndex,
    source: TextSource,
    budget: int = 8_000,
) -> ArmAContext:
    """Run budget-constrained global vector top-k for the supplied seed IDs."""
    if not isinstance(seeds, list) or any(not isinstance(node, int) for node in seeds):
        raise TypeError("Arm A seeds must be a list[int]")
    unique_seeds = list(dict.fromkeys(seeds))
    if not unique_seeds:
        raise ValueError("Arm A requires at least one seed")
    missing = [node for node in unique_seeds if node not in sidecar]
    if missing:
        raise KeyError(f"seed ids not in sidecar: {missing}")

    t0 = time.perf_counter()
    query_started = time.perf_counter()
    query = query_vector(index, unique_seeds)
    query_seconds = time.perf_counter() - query_started

    np = _numpy()
    ranking_started = time.perf_counter()
    scores = np.asarray(index.embeddings @ query, dtype=np.float32)
    if not np.isfinite(scores).all():
        raise VectorIndexError("cosine scoring produced NaN or infinity")
    seed_set = set(unique_seeds)
    mask = np.fromiter((int(node) not in seed_set for node in index.ids), dtype=bool)
    candidate_ids = np.asarray(index.ids[mask], dtype=np.int64)
    candidate_scores = scores[mask]
    order = np.lexsort((candidate_ids, -candidate_scores))
    ranked_ids = candidate_ids[order]
    ranking_seconds = time.perf_counter() - ranking_started

    levels: dict[int, Level] = {node: L3 for node in unique_seeds}
    provenance: dict[int, list[Reason]] = {}
    floor = cost(levels, sidecar)
    stats = ArmAStats(
        candidates=len(ranked_ids),
        floor_cost=floor,
        query_seconds=query_seconds,
        ranking_seconds=ranking_seconds,
        retrieval_seconds=query_seconds + ranking_seconds,
    )
    if floor > budget:
        stats.seconds = time.perf_counter() - t0
        return ArmAContext(
            status=EXCEEDED,
            budget=budget,
            levels=levels,
            seeds={node: L3 for node in unique_seeds},
            cost=floor,
            deficit=floor - budget,
            suggestion="reduce the seed count or raise the budget",
            stats=stats,
        )

    admission_started = time.perf_counter()
    admitted_order: list[int] = []
    for raw_node in ranked_ids:
        node = int(raw_node)
        # Unique index IDs and seed exclusion make duplicate admission
        # impossible, but keep the invariant explicit at this boundary.
        if node in levels:
            continue
        trial_levels = dict(levels)
        trial_levels[node] = L2
        trial_cost = cost(trial_levels, sidecar)
        if trial_cost > budget:
            stats.skipped_too_large += 1
            continue
        levels[node] = L2
        provenance[node] = [
            Reason(
                via=unique_seeds[0],
                edge="OPTIONAL:VECTOR_SIMILARITY",
                rule="global_cosine(mean(normalized_seed_vectors))->L2",
            )
        ]
        admitted_order.append(node)
        stats.admitted += 1
    stats.admission_seconds = time.perf_counter() - admission_started

    final_cost = cost(levels, sidecar)
    context = ArmAContext(
        status=OK,
        budget=budget,
        levels=levels,
        provenance=provenance,
        seeds={node: L3 for node in unique_seeds},
        cost=final_cost,
        hint_tokens=0,
        stats=stats,
    )

    # ``cost()`` is the admission authority shared with Arm B/C.  Its framing
    # term was fitted against graph-coherent contexts, however, and a global
    # vector result can span enough files for real rendering to exceed that
    # upper-bound estimate.  Guard the non-negotiable actual-token budget with
    # the shared emitter, removing only the lowest-ranked admitted candidates.
    guard_started = time.perf_counter()
    rendered = emit(context, source, sidecar)
    while (context.cost > budget or rendered.tokens > budget) and admitted_order:
        node = admitted_order.pop()
        context.levels.pop(node)
        context.provenance.pop(node, None)
        context.cost = cost(context.levels, sidecar)
        stats.admitted -= 1
        stats.trimmed_for_emission += 1
        rendered = emit(context, source, sidecar)
    stats.emission_guard_seconds = time.perf_counter() - guard_started
    stats.guarded_emitted_tokens = rendered.tokens

    if context.cost > budget or rendered.tokens > budget:
        context.status = EXCEEDED
        context.deficit = max(context.cost, rendered.tokens) - budget
        context.suggestion = "reduce the seed count or raise the budget"

    assert context.cost <= budget or context.status == EXCEEDED
    assert rendered.tokens <= budget or context.status == EXCEEDED
    assert all(context.levels[node] == L3 for node in unique_seeds)
    stats.seconds = time.perf_counter() - t0
    return context


__all__ = [
    "ArmAContext",
    "ArmAProfile",
    "ArmAStats",
    "EXCEEDED",
    "MODEL_ID",
    "MODEL_REVISION",
    "OK",
    "VectorIndex",
    "VectorIndexError",
    "load_vector_index",
    "query_vector",
    "rank_candidates",
    "run_arm_a",
    "sha256_file",
]
