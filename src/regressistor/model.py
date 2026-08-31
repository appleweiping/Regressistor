"""Immutable domain objects shared by parsers and the comparison engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias

Scalar: TypeAlias = str | int | float | bool
CaseKey: TypeAlias = tuple[tuple[str, Scalar], ...]
ScalarIdentity: TypeAlias = tuple[str, Scalar]
CaseIdentity: TypeAlias = tuple[tuple[str, ScalarIdentity], ...]


def scalar_identity(value: Scalar) -> ScalarIdentity:
    """Tag JSON scalar types so booleans never alias integers or floats."""
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    return ("str", value)


def case_identity(case: CaseKey) -> CaseIdentity:
    return tuple((key, scalar_identity(value)) for key, value in case)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class MissingAction(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    IGNORE = "ignore"


class Reducer(StrEnum):
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    P05 = "p05"
    P95 = "p95"


class ContractKind(StrEnum):
    MIN = "min"
    MAX = "max"
    RANGE = "range"
    TARGET = "target"


class Direction(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    TARGET = "target"


class Status(StrEnum):
    # This public decision status is not a credential.
    PASS = "pass"  # nosec B105
    SPEC_FAIL = "spec_fail"
    REGRESSION = "regression"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class Contract:
    kind: ContractKind
    lower: float | None = None
    upper: float | None = None
    target: float | None = None
    tolerance: float | None = None


@dataclass(frozen=True, slots=True)
class RegressionBudget:
    direction: Direction
    absolute: float = 0.0
    relative: float = 0.0
    relative_floor: float = 0.0
    target: float | None = None


@dataclass(frozen=True, slots=True)
class MetricPolicy:
    name: str
    unit: str
    reducer: Reducer
    severity: Severity
    contract: Contract | None = None
    regression: RegressionBudget | None = None


@dataclass(frozen=True, slots=True)
class MissingPolicy:
    baseline_case: MissingAction = MissingAction.ERROR
    candidate_case: MissingAction = MissingAction.ERROR
    baseline_metric: MissingAction = MissingAction.ERROR
    candidate_metric: MissingAction = MissingAction.ERROR


@dataclass(frozen=True, slots=True)
class Policy:
    case_keys: tuple[str, ...]
    metrics: tuple[MetricPolicy, ...]
    missing: MissingPolicy = field(default_factory=MissingPolicy)
    numeric_epsilon: float = 1e-12
    source_hash: str = ""


@dataclass(frozen=True, slots=True)
class Measurement:
    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class Point:
    case: CaseKey
    metrics: dict[str, Measurement]
    sample: str


@dataclass(frozen=True, slots=True)
class Bundle:
    points: tuple[Point, ...]
    run: dict[str, Any]
    source_hash: str
    source_path: str


@dataclass(frozen=True, slots=True)
class Decision:
    metric: str
    unit: str
    case: CaseKey
    status: Status
    severity: Severity
    blocking: bool
    message: str
    baseline: float | None = None
    candidate: float | None = None
    contract_margin: float | None = None
    regression_margin: float | None = None
    adverse_change: float | None = None
    allowed_change: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "unit": self.unit,
            "case": dict(self.case),
            "status": self.status,
            "severity": self.severity,
            "blocking": self.blocking,
            "message": self.message,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "contract_margin": self.contract_margin,
            "regression_margin": self.regression_margin,
            "adverse_change": self.adverse_change,
            "allowed_change": self.allowed_change,
        }
