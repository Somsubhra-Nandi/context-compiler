"""Ground-truth checks against the required Item 1–2 target repositories."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from context_compiler.extract.pipeline import extract_repository


TARGET_ROOT = Path.home() / "targets"


@pytest.fixture(scope="module")
def extracted(tmp_path_factory: pytest.TempPathFactory):
    artifacts = {}
    for name in ("requests", "flask"):
        repo = TARGET_ROOT / name
        if not (repo / "index.scip").is_file():
            pytest.skip(f"ground-truth target or SCIP index unavailable: {repo}")
        out = tmp_path_factory.mktemp(f"verify-{name}")
        extract_repository(repo, out, reindex=False)
        symbols = [json.loads(line) for line in (out / "symbols.jsonl").read_text().splitlines()]
        edges = [json.loads(line) for line in (out / "edges.jsonl").read_text().splitlines()]
        artifacts[name] = (symbols, edges)
    return artifacts


def test_session_request_ground_truth_calls(extracted):
    symbols, edges = extracted["requests"]
    by_fqn = {row["fqn"]: row["id"] for row in symbols}
    by_id = {row["id"]: row["fqn"] for row in symbols}
    source = by_fqn["requests.sessions.Session.request"]
    actual = {by_id[edge["dst"]] for edge in edges
              if edge["src"] == source and edge["type"] == "CALLS"}
    expected = {
        "requests._types.is_prepared",
        "requests.models.Request",
        "requests.sessions.Session.prepare_request",
        "requests.sessions.Session.merge_environment_settings",
        "requests.sessions.Session.send",
    }
    assert expected <= actual, f"missing ground-truth Session.request calls: {expected - actual}"


def test_all_canonical_representations_parse(extracted):
    failures = []
    total = 0
    for repository, (symbols, _) in extracted.items():
        for row in symbols:
            for level in ("L2", "L3"):
                total += 1
                try:
                    ast.parse(row[f"repr_{level}_text"])
                except SyntaxError as exc:
                    failures.append(f"{repository}:{row['fqn']}:{level}:{exc}")
    rate = len(failures) / total
    assert rate <= 0.0, (
        f"canonical parse failure rate {rate:.2%} exceeds justified floor 0%; "
        "there are no exempt constructs because methods include class headers: "
        + "; ".join(failures[:15])
    )
