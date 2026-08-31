"""Reproducible scaling benchmark without performance acceptance thresholds."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import regressistor
from regressistor.bundle import load_bundle
from regressistor.gate import compare
from regressistor.matching import index_bundle
from regressistor.policy import load_policy

POLICY = """schema_version = 1
case_keys = ["index"]
[missing]
baseline_case = "error"
candidate_case = "error"
baseline_metric = "error"
candidate_metric = "error"
[[metrics]]
name = "gain"
unit = "dB"
reduce = "mean"
severity = "error"
contract = { kind = "min", limit = 40.0 }
regression = { direction = "higher", absolute_budget = 0.1, relative_budget = 0.0 }
"""


def _package_tree_sha256() -> str:
    if regressistor.__file__ is None:
        raise RuntimeError("cannot locate imported regressistor package")
    root = Path(regressistor.__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _bundle(points: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run": {"benchmark": "regressistor-scaling-v1", "points": points},
        "points": [
            {
                "case": {"index": index},
                "sample": 0,
                "metrics": {"gain": {"value": 60.0 + (index % 5) / 10, "unit": "dB"}},
            }
            for index in range(points)
        ],
    }


def _measure(operation: Callable[[], object], repetitions: int, points: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    return {
        "median_seconds": round(median, 9),
        "minimum_seconds": round(min(samples), 9),
        "points_per_second": round(points / median, 3) if median else 0.0,
    }


def run(point_counts: tuple[int, ...], repetitions: int) -> dict[str, object]:
    """Execute independently timed load, index, and compare stages."""

    if not point_counts or any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5_000
        for value in point_counts
    ):
        raise ValueError("point counts must be integers from 1 through 5000")
    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or not 1 <= repetitions <= 100
    ):
        raise ValueError("repetitions must be an integer from 1 through 100")
    environment = {
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    results: list[dict[str, object]] = []
    workload = sha256(POLICY.encode())
    with tempfile.TemporaryDirectory(prefix="regressistor-benchmark-") as temporary:
        root = Path(temporary)
        policy_path = root / "policy.toml"
        policy_path.write_text(POLICY, encoding="utf-8", newline="\n")
        policy = load_policy(policy_path)
        for points in point_counts:
            bundle_path = root / f"bundle-{points}.json"
            payload = json.dumps(_bundle(points), sort_keys=True, separators=(",", ":"))
            bundle_path.write_text(payload, encoding="utf-8", newline="\n")
            workload.update(points.to_bytes(4, "big"))
            workload.update(sha256(payload.encode()).digest())
            loaded = load_bundle(bundle_path)
            report = compare(policy, loaded, loaded)
            if not report.passed or len(report.decisions) != points:
                raise RuntimeError("benchmark decision invariant failed")
            results.append(
                {
                    "points": points,
                    "invariants": {"decision_count": len(report.decisions), "passed": True},
                    "stages": {
                        "strict_load": _measure(
                            lambda path=bundle_path: load_bundle(path), repetitions, points
                        ),
                        "index": _measure(
                            lambda bundle=loaded: index_bundle(bundle, policy), repetitions, points
                        ),
                        "compare": _measure(
                            lambda bundle=loaded: compare(policy, bundle, bundle),
                            repetitions,
                            points,
                        ),
                    },
                }
            )
    environment_json = json.dumps(environment, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": 1,
        "benchmark": "regressistor-scaling-v1",
        "distribution_version": version("regressistor"),
        "package_tree_sha256": _package_tree_sha256(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": environment,
        "environment_sha256": sha256(environment_json.encode()).hexdigest(),
        "workload_sha256": workload.hexdigest(),
        "repetitions": repetitions,
        "results": results,
        "timing_policy": "Informational only; no timing value is an acceptance threshold.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", default="10,100,1000")
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    try:
        counts = tuple(int(item) for item in args.points.split(","))
        report = run(counts, args.repetitions)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
