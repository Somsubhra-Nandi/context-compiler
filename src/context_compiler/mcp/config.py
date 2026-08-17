"""Item 7 Sec 2: env-var config with sane defaults, checked fail-fast at startup.

``CC_SYMBOLS``, ``CC_OFFSETS``, ``CC_BOLT_URI`` and ``CC_BUDGET`` are the task's
four knobs. ``CC_EDGES`` is added here beyond that list: the compiler needs the
out/in-degree tables for packing and ``impact_cone``'s hub skip, and re-deriving
them from ``symbols.jsonl`` alone is not possible -- it defaults to a sibling
``edges.jsonl`` next to ``CC_SYMBOLS`` so the common case needs no extra env var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUDGET = 8000


@dataclass(frozen=True)
class Config:
    symbols: Path
    offsets: Path
    edges: Path
    bolt_uri: str
    budget: int

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = env if env is not None else os.environ
        symbols = Path(env.get("CC_SYMBOLS", "~/out/django/symbols.jsonl")).expanduser()
        default_dir = symbols.parent
        offsets = Path(env.get("CC_OFFSETS", str(default_dir / "offsets.json"))).expanduser()
        edges = Path(env.get("CC_EDGES", str(default_dir / "edges.jsonl"))).expanduser()
        bolt_uri = env.get(
            "CC_BOLT_URI", env.get("HYDRADB_BOLT_URI", "bolt://127.0.0.1:7687")
        )
        budget = int(env.get("CC_BUDGET", str(DEFAULT_BUDGET)))
        return cls(symbols=symbols, offsets=offsets, edges=edges, bolt_uri=bolt_uri, budget=budget)
