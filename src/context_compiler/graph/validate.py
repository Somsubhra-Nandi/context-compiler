"""Cross-validation of the real fixpoint against the pre-registered prediction.

Before this code existed, a simulation sampled 200 sets of 6 library-function
seeds from the Django graph and predicted:

    closure size    median 47    p90 83    max 150
    L3+L2 tokens    median 3308  p90 6797  max 20272
    over 8000 tokens: 10/200

Reproducing that with the real closure is the single most valuable check in
Item 4: an independent prediction of the answer, made before the code existed.
Order-of-magnitude agreement is the pass condition -- the simulation ignored
level-merging on converging paths, so exact equality is not expected.
"""
from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .closure import L3, ClosureResult, Level, closure, source_cost
from .expand import Expander
from .sidecar import SymbolMeta

#: The pre-registered simulation figures, quoted from the Item 3-4 task.
PREDICTION = {
    "closure_size": {"median": 47, "p90": 83, "max": 150},
    "tokens": {"median": 3308, "p90": 6797, "max": 20272},
    "over_8000": 10,
    "trials": 200,
    "seeds_per_trial": 6,
}

#: Seed eligibility, quoted from the task: library functions, not tests.
SEED_KINDS = ("function", "method")
MIN_L3_TOKENS = 150


def eligible_seeds(symbols_path: str | Path) -> list[int]:
    """Ids matching the documented seed filter, in deterministic file order.

    ``file`` is not part of ``SymbolMeta`` (Amendment A1.1 fixes those fields),
    so eligibility is read from ``symbols.jsonl`` directly rather than by
    widening the sidecar.
    """
    out: list[int] = []
    with open(symbols_path, "rb") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["kind"] not in SEED_KINDS:
                continue
            if rec["file"].startswith("tests/"):
                continue
            if rec["repr_L3_tokens"] < MIN_L3_TOKENS:
                continue
            out.append(rec["id"])
    return out


def sample_seed_sets(
    pool: list[int], trials: int, per_trial: int, rng_seed: int = 20260817
) -> list[list[int]]:
    """Deterministic sample of ``trials`` disjointly-drawn seed sets."""
    rng = random.Random(rng_seed)
    return [rng.sample(pool, per_trial) for _ in range(trials)]


@dataclass
class Trial:
    seeds: list[int]
    closure_size: int
    emitted_size: int
    tokens: int
    levels: dict[str, int]
    round_trips: int
    ms: float
    provenance_complete: bool


@dataclass
class Distribution:
    trials: list[Trial] = field(default_factory=list)

    def _stat(self, key: str) -> dict:
        vals = sorted(getattr(t, key) for t in self.trials)
        if not vals:
            return {}
        n = len(vals)
        return {
            "median": round(statistics.median(vals), 1),
            "p90": vals[min(n - 1, int(n * 0.9))],
            "max": vals[-1],
            "mean": round(statistics.mean(vals), 1),
        }

    def summary(self) -> dict:
        return {
            "trials": len(self.trials),
            "closure_size": self._stat("closure_size"),
            "emitted_size": self._stat("emitted_size"),
            "tokens": self._stat("tokens"),
            "over_8000": sum(1 for t in self.trials if t.tokens > 8000),
            "round_trips": self._stat("round_trips"),
            "ms": self._stat("ms"),
            "provenance_complete": all(t.provenance_complete for t in self.trials),
        }


def provenance_is_complete(result: ClosureResult) -> bool:
    """I6/Sec 7.4: every non-seed closure member records why it is present."""
    return all(result.explain(n) for n in result.non_seeds())


def run_trials(
    seed_sets: Iterable[list[int]],
    expander: Expander,
    sidecar: Mapping[int, SymbolMeta],
) -> Distribution:
    dist = Distribution()
    for seeds in seed_sets:
        before_trips = expander.stats.round_trips
        before_ms = expander.stats.seconds
        result = closure({s: L3 for s in seeds}, expander)
        by_level: dict[str, int] = {}
        for lv in result.levels.values():
            by_level[Level(lv).name] = by_level.get(Level(lv).name, 0) + 1
        dist.trials.append(
            Trial(
                seeds=list(seeds),
                closure_size=len(result.levels),
                emitted_size=len(result.emitted()),
                tokens=source_cost(result, sidecar),
                levels=by_level,
                round_trips=expander.stats.round_trips - before_trips,
                ms=round((expander.stats.seconds - before_ms) * 1000, 2),
                provenance_complete=provenance_is_complete(result),
            )
        )
    return dist


def compare(summary: dict) -> dict:
    """Ratio of observed to predicted on each headline figure."""

    def ratio(observed: float, predicted: float) -> float:
        return round(observed / predicted, 3) if predicted else float("inf")

    return {
        "closure_size_median": ratio(
            summary["closure_size"]["median"], PREDICTION["closure_size"]["median"]
        ),
        "closure_size_p90": ratio(
            summary["closure_size"]["p90"], PREDICTION["closure_size"]["p90"]
        ),
        "tokens_median": ratio(summary["tokens"]["median"], PREDICTION["tokens"]["median"]),
        "tokens_p90": ratio(summary["tokens"]["p90"], PREDICTION["tokens"]["p90"]),
    }


def within_an_order_of_magnitude(summary: dict) -> bool:
    return all(0.1 <= r <= 10.0 for r in compare(summary).values())
