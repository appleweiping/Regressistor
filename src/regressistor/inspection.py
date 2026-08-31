"""Read-only coverage audit for a bundle under a policy."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from regressistor.bundle import case_label
from regressistor.matching import index_bundle, sorted_cases
from regressistor.model import Bundle, Policy, Scalar, case_identity, scalar_identity


@dataclass(frozen=True, slots=True)
class MetricCoverage:
    name: str
    configured_unit: str
    present_cases: int
    missing_cases: int
    sample_values: int
    observed_units: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.missing_cases == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured_unit": self.configured_unit,
            "present_cases": self.present_cases,
            "missing_cases": self.missing_cases,
            "sample_values": self.sample_values,
            "observed_units": list(self.observed_units),
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class BundleInspection:
    source_path: str
    case_count: int
    point_count: int
    cases: tuple[str, ...]
    case_values: dict[str, tuple[Any, ...]]
    metrics: tuple[MetricCoverage, ...]
    unconfigured_metrics: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return all(metric.complete for metric in self.metrics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_path": self.source_path,
            "case_count": self.case_count,
            "point_count": self.point_count,
            "cases": list(self.cases),
            "case_values": {key: list(values) for key, values in self.case_values.items()},
            "metrics": [metric.as_dict() for metric in self.metrics],
            "unconfigured_metrics": list(self.unconfigured_metrics),
            "complete": self.complete,
        }


def _stable_values(values: Iterable[Scalar]) -> tuple[Scalar, ...]:
    distinct = {scalar_identity(value): value for value in values}
    return tuple(
        sorted(
            distinct.values(),
            key=lambda value: json.dumps(
                scalar_identity(value), sort_keys=True, separators=(",", ":")
            ),
        )
    )


def inspect_bundle(policy: Policy, bundle: Bundle) -> BundleInspection:
    """Summarize metric coverage without evaluating any gates."""
    index = index_bundle(bundle, policy)
    cases = sorted_cases(case for case, _metrics in index.values())
    configured_names = {metric.name for metric in policy.metrics}
    observed_names = {
        metric_name for _case, case_metrics in index.values() for metric_name in case_metrics
    }

    coverage: list[MetricCoverage] = []
    for metric in policy.metrics:
        present = 0
        sample_values = 0
        units: set[str] = set()
        for case in cases:
            samples = index[case_identity(case)][1].get(metric.name, [])
            if samples:
                present += 1
                sample_values += len(samples)
                units.update(sample.unit for sample in samples)
        coverage.append(
            MetricCoverage(
                name=metric.name,
                configured_unit=metric.unit,
                present_cases=present,
                missing_cases=len(cases) - present,
                sample_values=sample_values,
                observed_units=tuple(sorted(units)),
            )
        )

    case_values = {}
    for key in policy.case_keys:
        case_values[key] = _stable_values(dict(case)[key] for case in cases)
    return BundleInspection(
        source_path=bundle.source_path,
        case_count=len(cases),
        point_count=len(bundle.points),
        cases=tuple(case_label(case) for case in cases),
        case_values=case_values,
        metrics=tuple(coverage),
        unconfigured_metrics=tuple(sorted(observed_names - configured_names)),
    )


def inspection_text(inspection: BundleInspection) -> str:
    """Render a compact terminal summary."""
    outcome = "COMPLETE" if inspection.complete else "INCOMPLETE"
    lines = [
        f"Bundle coverage: {outcome}",
        f"Cases: {inspection.case_count}; points: {inspection.point_count}",
    ]
    for metric in inspection.metrics:
        units = ", ".join(metric.observed_units) or "none"
        lines.append(
            f"- {metric.name}: {metric.present_cases}/{inspection.case_count} cases, "
            f"{metric.sample_values} samples, units={units}"
        )
    if inspection.unconfigured_metrics:
        lines.append("Unconfigured metrics: " + ", ".join(inspection.unconfigured_metrics))
    return "\n".join(lines)
