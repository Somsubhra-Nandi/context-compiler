"""Read SCIP protobuf indexes through the official CLI's JSON interface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


def find_scip_cli() -> str:
    candidates = [os.environ.get("SCIP_CLI"), shutil.which("scip"), "/tmp/scip"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("scip CLI not found; set SCIP_CLI to the official binary")


def read_index(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [find_scip_cli(), "print", "--json", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(result.stdout)


def run_index(repo: Path) -> Path:
    subprocess.run(["scip-python", "index", "."], cwd=repo, check=True)
    path = repo / "index.scip"
    if not path.is_file():
        raise RuntimeError("scip-python completed without creating index.scip")
    return path


_GLOBAL = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+(.+)$")


def symbol_to_fqn(symbol: str, source_prefixes: tuple[str, ...] = ("src.",)) -> str | None:
    """Map a scip-python global symbol string to a Python runtime FQN."""
    if symbol.startswith("local "):
        return None
    match = _GLOBAL.match(symbol)
    if not match:
        return None
    descriptor = match.group(1).replace("`", "")
    # Parameters and local descriptors do not denote graph symbols.
    if descriptor.endswith(")") and ".(" in descriptor:
        return None
    descriptor = re.sub(r"\([^)]*\)$", "", descriptor)
    descriptor = descriptor.replace("/__init__:", "")
    descriptor = descriptor.replace("/__init__", "") if descriptor.endswith("/__init__") else descriptor
    descriptor = descriptor.replace("/", ".").replace("#", ".")
    descriptor = descriptor.replace("().", ".").replace(":", "")
    descriptor = descriptor.rstrip(".")
    descriptor = re.sub(r"\([^)]*\)", "", descriptor)
    for prefix in source_prefixes:
        if descriptor.startswith(prefix):
            descriptor = descriptor[len(prefix) :]
            break
    return descriptor or None


class ScipIndex:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.by_path: dict[str, dict[tuple[int, int], str]] = {}
        self.relationships: list[tuple[str, str, str]] = []
        for document in data.get("documents", []):
            positions: dict[tuple[int, int], str] = {}
            for occurrence in document.get("occurrences", []):
                r = occurrence.get("range", [])
                if len(r) >= 3:
                    positions[(r[0] + 1, r[1])] = occurrence.get("symbol", "")
            self.by_path[document["relative_path"]] = positions
            for info in document.get("symbols", []):
                for rel in info.get("relationships", []):
                    if rel.get("is_implementation"):
                        self.relationships.append((info["symbol"], rel["symbol"], "IMPLEMENTS"))

    def resolve(self, relative_path: str, line: int, column: int) -> str | None:
        positions = self.by_path.get(relative_path, {})
        exact = positions.get((line, column))
        if exact:
            return exact
        # AST positions occasionally point at the expression rather than identifier.
        same_line = [(abs(col - column), sym) for (ln, col), sym in positions.items() if ln == line]
        return min(same_line)[1] if same_line else None
