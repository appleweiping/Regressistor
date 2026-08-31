"""Stable report model and JSON serialization."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from regressistor._strict_data import check_data_complexity, load_json_path
from regressistor.errors import InputError, OutputError
from regressistor.model import CaseKey, Decision, Scalar, Severity, Status, case_identity
from regressistor.units import validate_unit

MAX_REPORT_BYTES = 16 * 1_048_576
MAX_REPORT_NODES = 250_000
MAX_REPORT_DEPTH = 68
MAX_DECISIONS = 10_000
_REPORT_FIELDS = {"schema_version", "passed", "counts", "case_keys", "inputs", "results"}
_INPUT_FIELDS = {
    "policy_sha256",
    "baseline_sha256",
    "candidate_sha256",
    "baseline_run",
    "candidate_run",
}
_RESULT_FIELDS = {
    "metric",
    "unit",
    "case",
    "status",
    "severity",
    "blocking",
    "message",
    "baseline",
    "candidate",
    "contract_margin",
    "regression_margin",
    "adverse_change",
    "allowed_change",
}
_HEX = frozenset("0123456789abcdef")


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
            data = self.to_dict()
            # Apply the same schema and complexity checks as load_report before writing.
            # A successful write is therefore guaranteed to be readable by this version.
            report_from_dict(data)
            payload = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
            if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
                raise OutputError(f"report exceeds {MAX_REPORT_BYTES} byte serialized output limit")
            target.write_text(payload, encoding="utf-8")
        except OSError as error:
            raise OutputError(f"cannot write report {target}: {error}") from error
        except InputError as error:
            raise OutputError(f"cannot serialize report {target}: {error}") from error
        except (TypeError, ValueError) as error:
            raise OutputError(f"cannot serialize report {target}: {error}") from error
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
    if not isinstance(value, str) or not value or value != value.strip() or not value.isprintable():
        raise InputError(f"{context} must be a portable non-empty string")
    return value


def _exact(value: dict[str, Any], fields: set[str], context: str) -> None:
    if set(value) != fields:
        raise InputError(f"{context} fields are invalid")


def _digest(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{context} must be empty or a lowercase SHA-256 digest")
    text = value
    if text and (len(text) != 64 or any(character not in _HEX for character in text)):
        raise InputError(f"{context} must be empty or a lowercase SHA-256 digest")
    return text


def _case_scalar(value: Any, context: str) -> Scalar:
    if isinstance(value, str):
        if not value or value != value.strip() or not value.isprintable() or len(value) > 256:
            raise InputError(f"{context} must be a portable string of at most 256 characters")
        return value
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InputError(f"{context} must be a finite JSON scalar")


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
    _exact(data, _REPORT_FIELDS, "report")
    check_data_complexity(
        data,
        "report",
        max_depth=MAX_REPORT_DEPTH,
        max_nodes=MAX_REPORT_NODES,
        max_text_bytes=MAX_REPORT_BYTES,
    )
    passed = data["passed"]
    counts = data["counts"]
    if not isinstance(passed, bool):
        raise InputError("report.passed must be boolean")
    if not isinstance(counts, dict) or set(counts) != {status.value for status in Status}:
        raise InputError("report.counts fields are invalid")
    if not all(
        not isinstance(count, bool) and isinstance(count, int) and count >= 0
        for count in counts.values()
    ):
        raise InputError("report.counts values must be non-negative integers")
    case_keys = data.get("case_keys")
    inputs = data.get("inputs")
    results = data.get("results")
    if not isinstance(case_keys, list) or not case_keys:
        raise InputError("report.case_keys must be a non-empty array of strings")
    case_keys = [_text(key, f"report.case_keys[{index}]") for index, key in enumerate(case_keys)]
    if any(len(key) > 256 for key in case_keys):
        raise InputError("report.case_keys entries must be at most 256 characters")
    if len(case_keys) != len({key.casefold() for key in case_keys}):
        raise InputError("report.case_keys must not contain duplicates ignoring case")
    if not isinstance(inputs, dict) or not isinstance(results, list) or not results:
        raise InputError("report.inputs and report.results are required")
    if len(results) > MAX_DECISIONS:
        raise InputError(f"report.results exceeds the {MAX_DECISIONS} decision limit")
    _exact(inputs, _INPUT_FIELDS, "report.inputs")

    decisions: list[Decision] = []
    decision_identities: set[tuple[object, str]] = set()
    for index, raw in enumerate(results):
        context = f"report.results[{index}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("case"), dict):
            raise InputError(f"{context} must contain a case object")
        _exact(raw, _RESULT_FIELDS, context)
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
        if len(metric) > 256 or len(unit) > 256:
            raise InputError(f"{context} metric and unit must be at most 256 characters")
        validate_unit(unit)
        blocking = raw.get("blocking")
        if not isinstance(blocking, bool):
            raise InputError(f"{context}.blocking must be boolean")
        expected_blocking = status is not Status.PASS and severity is Severity.ERROR
        if blocking is not expected_blocking:
            raise InputError(f"{context}.blocking is inconsistent with status and severity")
        raw_case = raw["case"]
        if set(raw_case) != set(case_keys):
            raise InputError(f"{context}.case keys must exactly match report.case_keys")
        case: CaseKey = tuple(
            (key, _case_scalar(raw_case[key], f"{context}.case.{key}")) for key in case_keys
        )
        identity = (case_identity(case), metric.casefold())
        if identity in decision_identities:
            raise InputError(f"{context} duplicates a metric/case decision")
        decision_identities.add(identity)
        baseline = _optional_float(raw.get("baseline"), f"{context}.baseline")
        candidate = _optional_float(raw.get("candidate"), f"{context}.candidate")
        contract_margin = _optional_float(raw.get("contract_margin"), f"{context}.contract_margin")
        regression_margin = _optional_float(
            raw.get("regression_margin"), f"{context}.regression_margin"
        )
        adverse_change = _optional_float(raw.get("adverse_change"), f"{context}.adverse_change")
        allowed_change = _optional_float(raw.get("allowed_change"), f"{context}.allowed_change")
        numeric_values = (
            baseline,
            candidate,
            contract_margin,
            regression_margin,
            adverse_change,
            allowed_change,
        )
        if status in {Status.MISSING, Status.INVALID}:
            if any(value is not None for value in numeric_values):
                raise InputError(f"{context} {status.value} result must not contain numeric values")
        elif candidate is None:
            raise InputError(f"{context} {status.value} result requires a candidate value")
        regression_values = (regression_margin, adverse_change, allowed_change)
        if any(value is not None for value in regression_values) and not all(
            value is not None for value in regression_values
        ):
            raise InputError(f"{context} regression values must be all present or all null")
        if all(value is not None for value in regression_values):
            present_margin = cast(float, regression_margin)
            present_adverse = cast(float, adverse_change)
            present_allowed = cast(float, allowed_change)
            if present_allowed < 0 or not math.isclose(
                present_margin,
                present_allowed - present_adverse,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise InputError(f"{context} regression values are inconsistent")
        if status is Status.REGRESSION and (regression_margin is None or regression_margin >= 0):
            raise InputError(f"{context} regression result requires a negative margin")
        if status is Status.SPEC_FAIL and (contract_margin is None or contract_margin >= 0):
            raise InputError(f"{context} spec_fail result requires a negative contract margin")
        decisions.append(
            Decision(
                metric=metric,
                unit=unit,
                case=case,
                status=status,
                severity=severity,
                blocking=blocking,
                message=message,
                baseline=baseline,
                candidate=candidate,
                contract_margin=contract_margin,
                regression_margin=regression_margin,
                adverse_change=adverse_change,
                allowed_change=allowed_change,
            )
        )
    policy_hash = _digest(inputs.get("policy_sha256", ""), "report.inputs.policy_sha256")
    baseline_hash = _digest(inputs.get("baseline_sha256", ""), "report.inputs.baseline_sha256")
    candidate_hash = _digest(inputs.get("candidate_sha256", ""), "report.inputs.candidate_sha256")
    baseline_run = inputs.get("baseline_run", {})
    candidate_run = inputs.get("candidate_run", {})
    if not isinstance(baseline_run, dict) or not isinstance(candidate_run, dict):
        raise InputError("report input run metadata must be objects")
    report = Report(
        tuple(decisions),
        tuple(case_keys),
        policy_hash,
        baseline_hash,
        candidate_hash,
        dict(baseline_run),
        dict(candidate_run),
    )
    if passed != report.passed or counts != report.counts:
        raise InputError("report summary does not match its results")
    return report


def load_report(path: str | Path) -> Report:
    try:
        source, _, data = load_json_path(
            path,
            context="report",
            max_bytes=MAX_REPORT_BYTES,
            max_depth=MAX_REPORT_DEPTH,
            max_nodes=MAX_REPORT_NODES,
            max_text_bytes=MAX_REPORT_BYTES,
        )
    except InputError as error:
        source = Path(path)
        raise InputError(f"cannot read report {source}: {error}") from error
    return report_from_dict(data)
