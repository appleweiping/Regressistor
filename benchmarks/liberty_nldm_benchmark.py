"""Informational benchmark for strict Liberty NLDM conversion."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from regressistor.liberty import convert_liberty_nldm


def run(source: Path, repetitions: int) -> dict[str, object]:
    """Measure repeated conversion while checking result invariants."""

    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or not 1 <= repetitions <= 100
    ):
        raise ValueError("repetitions must be an integer from 1 through 100")
    payload = source.read_bytes()
    expected_sha256 = sha256(payload).hexdigest()
    samples: list[float] = []
    point_count: int | None = None
    source_count: int | None = None
    for _ in range(repetitions):
        started = time.perf_counter()
        conversion = convert_liberty_nldm(payload, source_name=source.name)
        samples.append(time.perf_counter() - started)
        if conversion.source_sha256 != expected_sha256:
            raise RuntimeError("Liberty source hash invariant failed")
        observed_points = len(conversion.bundle.points)
        observed_sources = len(conversion.metric_sources)
        if point_count is None:
            point_count = observed_points
            source_count = observed_sources
        elif point_count != observed_points or source_count != observed_sources:
            raise RuntimeError("Liberty conversion count invariant failed")
    if (
        point_count is None or source_count is None
    ):  # pragma: no cover - repetitions is at least one
        raise RuntimeError("Liberty benchmark produced no conversion")
    median = statistics.median(samples)
    environment = {
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    return {
        "schema_version": 1,
        "benchmark": "regressistor-liberty-nldm-import-v1",
        "distribution_version": version("regressistor"),
        "source_name": source.name,
        "source_sha256": expected_sha256,
        "source_bytes": len(payload),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": environment,
        "environment_sha256": sha256(
            json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "repetitions": repetitions,
        "invariants": {"points": point_count, "metric_sources": source_count},
        "timing": {
            "median_seconds": round(median, 9),
            "minimum_seconds": round(min(samples), 9),
            "maximum_seconds": round(max(samples), 9),
        },
        "timing_policy": "Informational only; no timing value is an acceptance threshold.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parents[1] / "tests" / "fixtures" / "synthetic_nldm.lib",
    )
    parser.add_argument("--repetitions", type=int, default=10)
    arguments = parser.parse_args()
    try:
        report = run(arguments.source, arguments.repetitions)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
