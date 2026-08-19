#!/usr/bin/env python
"""Build the frozen Arm A symbol-vector index on a CUDA host.

Example:
  python scripts/build_vector_index.py \
    --symbols ~/cc-vector-cache/input/symbols.jsonl \
    --out ~/cc-vector-cache/output \
    --batch-size 16 --max-sequence-length 2048

The model and commit are constants imported from Arm A.  There is deliberately
no CLI option that can silently substitute either one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL_ID = "jinaai/jina-embeddings-v2-base-code"
MODEL_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_frozen_parameters(repo_root: Path) -> None:
    frozen = json.loads((repo_root / "frozen_params.json").read_text())["arm_a_deferred"]
    if frozen.get("model_id") != MODEL_ID or frozen.get("model_revision") != MODEL_REVISION:
        raise RuntimeError("builder model constants do not match frozen_params.json")


def load_records(path: Path) -> tuple[list[int], list[str]]:
    """Load exactly one nonblank repr_L2_text chunk per unique symbol, by ID."""
    rows: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            record = json.loads(raw)
            if "id" not in record:
                raise ValueError(f"symbols.jsonl line {line_number} has no id")
            text = record.get("repr_L2_text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    f"symbol {record['id']} on line {line_number} has no usable repr_L2_text"
                )
            rows.append((int(record["id"]), text))
    rows.sort(key=lambda item: item[0])
    ids = [node for node, _text in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("symbols.jsonl contains duplicate node IDs")
    return ids, [text for _node, text in rows]


def smoke(model, texts: list[str], np, torch) -> tuple[int, float]:
    """Exercise CUDA with real inputs and verify finite normalized output."""
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available; refusing to build the GPU index")
    sample = [texts[0], texts[len(texts) // 2], max(texts, key=len)]
    started = time.perf_counter()
    vectors = model.encode(
        sample,
        batch_size=1,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    elapsed = time.perf_counter() - started
    vectors = np.asarray(vectors)
    if vectors.ndim != 2 or len(vectors) != len(sample):
        raise RuntimeError(f"unexpected smoke embedding shape: {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise RuntimeError("smoke embeddings contain NaN or infinity")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-4, atol=1e-4):
        raise RuntimeError("smoke embeddings are not normalized")
    if next(model.parameters()).device.type != "cuda":
        raise RuntimeError("model parameters are not on CUDA after smoke embedding")
    return int(vectors.shape[1]), elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-sequence-length", type=int, default=2_048)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.batch_size <= 0 or args.max_sequence_length <= 0:
        parser.error("batch size and max sequence length must be positive")

    repo_root = Path(__file__).resolve().parents[1]
    verify_frozen_parameters(repo_root)

    import numpy as np
    import sentence_transformers
    import torch
    import transformers
    from sentence_transformers import SentenceTransformer

    symbols = Path(args.symbols).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    ids, texts = load_records(symbols)
    print(f"loaded {len(ids):,} repr_L2_text chunks from {symbols}", file=sys.stderr)
    print(f"loading {MODEL_ID}@{MODEL_REVISION} with trust_remote_code=True", file=sys.stderr)
    model = SentenceTransformer(
        MODEL_ID,
        revision=MODEL_REVISION,
        trust_remote_code=True,
        device="cuda",
    )
    model.max_seq_length = args.max_sequence_length
    auto_model = model[0].auto_model
    remote_code_module = auto_model.__class__.__module__
    remote_code_parts = remote_code_module.split(".")
    remote_code_revision = next(
        (part for part in remote_code_parts if len(part) == 40 and all(c in "0123456789abcdef" for c in part)),
        None,
    )
    dimension, smoke_seconds = smoke(model, texts, np, torch)
    print(
        f"smoke OK: CUDA={torch.cuda.get_device_name(0)} dimension={dimension} "
        f"seconds={smoke_seconds:.2f}",
        file=sys.stderr,
    )
    if args.smoke_only:
        return 0

    artifact_paths = [output / name for name in ("embeddings.npy", "ids.npy", "metadata.json")]
    existing = [path for path in artifact_paths if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(f"output artifacts already exist: {existing}; pass --overwrite")
    output.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    embeddings = model.encode(
        texts,
        batch_size=args.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    build_seconds = time.perf_counter() - started
    embeddings = np.asarray(embeddings, dtype=np.float32)
    id_array = np.asarray(ids, dtype=np.int64)
    if embeddings.shape != (len(ids), dimension):
        raise RuntimeError(
            f"embedding shape {embeddings.shape} != expected {(len(ids), dimension)}"
        )
    if not np.isfinite(embeddings).all():
        raise RuntimeError("full embedding matrix contains NaN or infinity")
    norms = np.linalg.norm(embeddings, axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-4):
        raise RuntimeError("full embedding matrix contains non-unit rows")

    embeddings_tmp = output / "embeddings.npy.tmp"
    ids_tmp = output / "ids.npy.tmp"
    with embeddings_tmp.open("wb") as handle:
        np.save(handle, embeddings, allow_pickle=False)
    with ids_tmp.open("wb") as handle:
        np.save(handle, id_array, allow_pickle=False)
    os.replace(embeddings_tmp, output / "embeddings.npy")
    os.replace(ids_tmp, output / "ids.npy")

    command = " ".join(shlex.quote(part) for part in [sys.executable, *sys.argv])
    metadata = {
        "format_version": 1,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "trust_remote_code": True,
        "remote_code_module": remote_code_module,
        "remote_code_revision": remote_code_revision,
        "model_config_commit": getattr(auto_model.config, "_commit_hash", None),
        "sentence_transformers_version": sentence_transformers.__version__,
        "transformers_version": transformers.__version__,
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_device": torch.cuda.get_device_name(0),
        "embedding_dimension": dimension,
        "dtype": str(embeddings.dtype),
        "normalized": True,
        "number_of_vectors": len(ids),
        "input_symbols": str(symbols),
        "input_symbols_sha256": sha256_file(symbols),
        "ids_sha256": sha256_file(output / "ids.npy"),
        "embeddings_sha256": sha256_file(output / "embeddings.npy"),
        "row_order": "ascending node ID",
        "representation": "repr_L2_text; one symbol per vector",
        "pooling": "model SentenceTransformer pooling",
        "query_definition": "L2-normalized arithmetic mean of normalized seed vectors",
        "similarity": "cosine via normalized dot product",
        "max_sequence_length": args.max_sequence_length,
        "truncation": True,
        "batch_size": args.batch_size,
        "smoke_seconds": round(smoke_seconds, 3),
        "embedding_seconds": round(build_seconds, 3),
        "creation_timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "working_directory": str(Path.cwd()),
    }
    metadata_tmp = output / "metadata.json.tmp"
    metadata_tmp.write_text(json.dumps(metadata, indent=2) + "\n")
    os.replace(metadata_tmp, output / "metadata.json")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
