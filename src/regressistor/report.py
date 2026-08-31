"""Stable report model and JSON serialization."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from regressistor.errors import InputError, OutputError
from regressistor.model import CaseKey, Decision, Scalar, Severity, Status

_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000
_MAX_JSON_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class Report:
    decisions: tuple[Decision, ...]
    case_keys: tuple[str, ...]
    policy_sha256: str
    baseline_sha256: str
    candidate_sha256: str
    baseline_run: dict[str, Any]
    candidate_run: dict[str, Any]

    @property
    def passed(self) -> bool:
        return not any(decision.blocking for decision in self.decisions)

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(decision.status.value for decision in self.decisions)
        return {status.value: counts.get(status.value, 0) for status in Status}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "counts": self.counts,
            "case_keys": list(self.case_keys),
            "inputs": {
                "policy_sha256": self.policy_sha256,
                "baseline_sha256": self.baseline_sha256,
                "candidate_sha256": self.candidate_sha256,
                "baseline_run": self.baseline_run,
                "candidate_run": self.candidate_run,
            },
            "results": [decision.as_dict() for decision in self.decisions],
        }

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise OutputError(f"cannot write report {target}: {error}") from error
        return target


def _optional_float(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"{context} must be numeric or null")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise InputError(f"{context} must be finite") from error
    if not math.isfinite(result):
        raise InputError(f"{context} must be finite")
    return result


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{context} must be a string")
    return value


def _case_scalar(value: Any, context: str) -> Scalar:
    if isinstance(value, bool | str | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InputError(f"{context} must be a finite JSON scalar")


def _json_safe(value: Any, context: str, nodes: list[int], depth: int = 0) -> None:
    nodes[0] += 1
    if depth > _MAX_JSON_DEPTH or nodes[0] > _MAX_JSON_NODES:
        raise InputError(f"{context} exceeds JSON complexity limits")
    if value is None or isinstance(value, bool | str | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputError(f"{context} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_safe(item, f"{context}[{index}]", nodes, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InputError(f"{context} object keys must be strings")
            _json_safe(item, f"{context}.{key}", nodes, depth + 1)
        return
    raise InputError(f"{context} contains a non-JSON value")


def report_from_dict(data: Any) -> Report:
    """Validate the report fields needed by the explanation command."""
    if not isinstance(data, dict):
        raise InputError("report must be a schema_version 1 object")
    schema_version = data.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise InputError("report must be a schema_version 1 object")
    _json_safe(data, "report", [0])
    case_keys = data.get("case_keys")
    inputs = data.get("inputs")
    results = data.get("results")
    if not isinstance(case_keys, list) or not all(isinstance(key, str) for key in case_keys):
        raise InputError("report.case_keys must be an array of strings")
    if len(case_keys) != len(set(case_keys)):
        raise InputError("report.case_keys must not contain duplicates")
    if not isinstance(inputs, dict) or not isinstance(results, list):
        raise InputError("report.inputs and report.results are required")

    decisions: list[Decision] = []
    for index, raw in enumerate(results):
        context = f"report.results[{index}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("case"), dict):
            raise InputError(f"{context} must contain a case object")
        status_raw = _text(raw.get("status"), f"{context}.status")
        severity_raw = _text(raw.get("severity"), f"{context}.severity")
        try:
            status = Status(status_raw)
            severity = Severity(severity_raw)
        except (TypeError, ValueError) as error:
            raise InputError(f"{context} contains an invalid status or severity") from error
        metric = _text(raw.get("metric"), f"{context}.metric")
        unit = _text(raw.get("unit"), f"{context}.unit")
        message = _text(raw.get("message"), f"{context}.message")
        blocking = raw.get("blocking")
        if not isinstance(blocking, bool):
            raise InputError(f"{context}.blocking must be boolean")
        raw_case = raw["case"]
        if set(raw_case) != set(case_keys):
            raise InputError(f"{context}.case keys must exactly match report.case_keys")
        case: CaseKey = tuple(
            (key, _case_scalar(raw_case[key], f"{context}.case.{key}")) for key in case_keys
        )
        decisions.append(
            Decision(
                metric=metric,
                unit=unit,
                case=case,
                status=status,
                severity=severity,
                blocking=blocking,
                message=message,
                baseline=_optional_float(raw.get("baseline"), f"{context}.baseline"),
                candidate=_optional_float(raw.get("candidate"), f"{context}.candidate"),
                contract_margin=_optional_float(
                    raw.get("contract_margin"), f"{context}.contract_margin"
                ),
                regression_margin=_optional_float(
                    raw.get("regression_margin"), f"{context}.regression_margin"
                ),
                adverse_change=_optional_float(
                    raw.get("adverse_change"), f"{context}.adverse_change"
                ),
                allowed_change=_optional_float(
                    raw.get("allowed_change"), f"{context}.allowed_change"
                ),
            )
        )
    policy_hash = _text(inputs.get("policy_sha256", ""), "report.inputs.policy_sha256")
    baseline_hash = _text(inputs.get("baseline_sha256", ""), "report.inputs.baseline_sha256")
    candidate_hash = _text(inputs.get("candidate_sha256", ""), "report.inputs.candidate_sha256")
    baseline_run = inputs.get("baseline_run", {})
    candidate_run = inputs.get("candidate_run", {})
    if not isinstance(baseline_run, dict) or not isinstance(candidate_run, dict):
        raise InputError("report input run metadata must be objects")
    return Report(
        tuple(decisions),
        tuple(case_keys),
        policy_hash,
        baseline_hash,
        candidate_hash,
        dict(baseline_run),
        dict(candidate_run),
    )


def load_report(path: str | Path) -> Report:
    source = Path(path)
    try:
        payload = source.read_bytes()
        if len(payload) > _MAX_JSON_BYTES:
            raise InputError(f"report exceeds {_MAX_JSON_BYTES} byte input limit")
        data = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(f"cannot read report {source}: {error}") from error
    return report_from_dict(data)
