"""Corner projection and metric indexing."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

from regressistor.errors import InputError
from regressistor.model import (
    Bundle,
    CaseIdentity,
    CaseKey,
    Measurement,
    Policy,
    case_identity,
    scalar_identity,
)

MetricIndex = dict[CaseIdentity, tuple[CaseKey, dict[str, list[Measurement]]]]


def project_case(case: CaseKey, keys: tuple[str, ...]) -> CaseKey:
    """Project a bundle case onto policy keys in policy order."""
    values = dict(case)
    missing = [key for key in keys if key not in values]
    if missing:
        raise InputError(f"bundle case is missing policy keys {missing!r}: {values!r}")
    return tuple((key, values[key]) for key in keys)


def index_bundle(bundle: Bundle, policy: Policy) -> MetricIndex:
    """Group repeated samples by projected case and metric."""
    grouped: dict[CaseIdentity, dict[str, list[Measurement]]] = defaultdict(
        lambda: defaultdict(list)
    )
    cases: dict[CaseIdentity, CaseKey] = {}
    identities: set[tuple[CaseIdentity, str]] = set()
    for point in bundle.points:
        case = project_case(point.case, policy.case_keys)
        typed_case = case_identity(case)
        sample_identity = (typed_case, point.sample)
        if sample_identity in identities:
            raise InputError(f"duplicate projected case/sample: {dict(case)!r}, {point.sample!r}")
        identities.add(sample_identity)
        cases[typed_case] = case
        for metric, measurement in point.metrics.items():
            grouped[typed_case][metric].append(measurement)
    return {identity: (cases[identity], dict(metrics)) for identity, metrics in grouped.items()}


def sorted_cases(cases: Iterable[CaseKey]) -> list[CaseKey]:
    """Sort heterogeneous scalar cases through canonical JSON."""
    distinct = {case_identity(case): case for case in cases}
    return sorted(
        distinct.values(),
        key=lambda case: json.dumps(
            [(key, *scalar_identity(value)) for key, value in case],
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
