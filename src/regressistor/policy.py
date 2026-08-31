"""Strict parser for regression-gate policy files."""

from __future__ import annotations

import hashlib
import math
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from regressistor._strict_data import check_data_complexity, read_document
from regressistor.errors import InputError
from regressistor.model import (
    Contract,
    ContractKind,
    Direction,
    MetricPolicy,
    MissingAction,
    MissingPolicy,
    Policy,
    Reducer,
    RegressionBudget,
    Severity,
)
from regressistor.units import validate_unit

_HEX_DIGEST = frozenset("0123456789abcdef")


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"{context} must be a table")
    return value


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise InputError(f"{context} has unknown fields: {', '.join(unknown)}")


def _text(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value) > 256
    ):
        raise InputError(f"{context} must be a portable non-empty string")
    return value


def _number(value: Any, context: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"{context} must be numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise InputError(f"{context} must be finite") from error
    if not math.isfinite(result):
        raise InputError(f"{context} must be finite")
    if minimum is not None and result < minimum:
        raise InputError(f"{context} must be at least {minimum}")
    return result


def _enum(enum_type: type[Any], value: Any, context: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(item.value for item in enum_type)
        raise InputError(f"{context} must be one of: {choices}") from error


def _contract(raw: Any, context: str) -> Contract | None:
    if raw is None:
        return None
    table = _mapping(raw, context)
    kind = _enum(ContractKind, table.get("kind"), f"{context}.kind")

    if kind is ContractKind.MIN:
        _reject_unknown(table, {"kind", "limit"}, context)
        lower = _number(table.get("limit"), f"{context}.limit")
        return Contract(kind=kind, lower=lower)
    if kind is ContractKind.MAX:
        _reject_unknown(table, {"kind", "limit"}, context)
        upper = _number(table.get("limit"), f"{context}.limit")
        return Contract(kind=kind, upper=upper)
    if kind is ContractKind.RANGE:
        _reject_unknown(table, {"kind", "lower", "upper"}, context)
        lower = _number(table.get("lower"), f"{context}.lower")
        upper = _number(table.get("upper"), f"{context}.upper")
        if lower > upper:
            raise InputError(f"{context}.lower must not exceed upper")
        return Contract(kind=kind, lower=lower, upper=upper)

    _reject_unknown(table, {"kind", "target", "tolerance"}, context)
    target = _number(table.get("target"), f"{context}.target")
    tolerance = _number(table.get("tolerance"), f"{context}.tolerance", minimum=0.0)
    return Contract(kind=kind, target=target, tolerance=tolerance)


def _regression(raw: Any, context: str, contract: Contract | None) -> RegressionBudget | None:
    if raw is None:
        return None
    table = _mapping(raw, context)
    direction = _enum(Direction, table.get("direction"), f"{context}.direction")
    allowed = {"direction", "absolute_budget", "relative_budget", "relative_floor"}
    if direction is Direction.TARGET:
        allowed.add("target")
    _reject_unknown(table, allowed, context)
    absolute = _number(table.get("absolute_budget", 0.0), f"{context}.absolute_budget", minimum=0.0)
    relative = _number(table.get("relative_budget", 0.0), f"{context}.relative_budget", minimum=0.0)
    relative_floor = _number(
        table.get("relative_floor", 0.0), f"{context}.relative_floor", minimum=0.0
    )
    target: float | None = None
    if direction is Direction.TARGET:
        raw_target = table.get("target")
        if raw_target is None and contract and contract.kind is ContractKind.TARGET:
            raw_target = contract.target
        target = _number(raw_target, f"{context}.target")
    return RegressionBudget(direction, absolute, relative, relative_floor, target)


def parse_policy(data: Mapping[str, Any], *, source_hash: str = "") -> Policy:
    """Build a validated policy from parsed TOML data."""
    if source_hash and (
        len(source_hash) != 64 or any(character not in _HEX_DIGEST for character in source_hash)
    ):
        raise InputError("policy source_hash must be empty or a lowercase SHA-256 digest")
    check_data_complexity(data, "policy")
    _reject_unknown(
        data, {"schema_version", "case_keys", "numeric_epsilon", "missing", "metrics"}, "policy"
    )
    schema_version = data.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise InputError("policy.schema_version must be 1")

    raw_case_keys = data.get("case_keys")
    if not isinstance(raw_case_keys, list) or not raw_case_keys:
        raise InputError("policy.case_keys must be a non-empty array")
    case_keys = tuple(_text(item, "policy.case_keys item") for item in raw_case_keys)
    if len(case_keys) != len({key.casefold() for key in case_keys}):
        raise InputError("policy.case_keys contains duplicates ignoring case")

    raw_missing = _mapping(data.get("missing", {}), "policy.missing")
    _reject_unknown(
        raw_missing,
        {"baseline_case", "candidate_case", "baseline_metric", "candidate_metric"},
        "policy.missing",
    )
    missing = MissingPolicy(
        baseline_case=_enum(
            MissingAction, raw_missing.get("baseline_case", "error"), "missing.baseline_case"
        ),
        candidate_case=_enum(
            MissingAction, raw_missing.get("candidate_case", "error"), "missing.candidate_case"
        ),
        baseline_metric=_enum(
            MissingAction,
            raw_missing.get("baseline_metric", "error"),
            "missing.baseline_metric",
        ),
        candidate_metric=_enum(
            MissingAction,
            raw_missing.get("candidate_metric", "error"),
            "missing.candidate_metric",
        ),
    )

    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, list) or not raw_metrics:
        raise InputError("policy.metrics must contain at least one metric table")
    metrics: list[MetricPolicy] = []
    names: set[str] = set()
    for index, raw_metric in enumerate(raw_metrics):
        context = f"policy.metrics[{index}]"
        table = _mapping(raw_metric, context)
        _reject_unknown(
            table,
            {"name", "unit", "reduce", "severity", "contract", "regression"},
            context,
        )
        name = _text(table.get("name"), f"{context}.name")
        identity = name.casefold()
        if identity in names:
            raise InputError(f"duplicate metric policy: {name}")
        names.add(identity)
        unit = validate_unit(_text(table.get("unit", "1"), f"{context}.unit"))
        reducer = _enum(Reducer, table.get("reduce", "mean"), f"{context}.reduce")
        severity = _enum(Severity, table.get("severity", "error"), f"{context}.severity")
        contract = _contract(table.get("contract"), f"{context}.contract")
        regression = _regression(table.get("regression"), f"{context}.regression", contract)
        if contract is None and regression is None:
            raise InputError(f"{context} must define contract, regression, or both")
        metrics.append(MetricPolicy(name, unit, reducer, severity, contract, regression))

    epsilon = _number(data.get("numeric_epsilon", 1e-12), "policy.numeric_epsilon", minimum=0.0)
    return Policy(case_keys, tuple(metrics), missing, epsilon, source_hash)


def load_policy(path: str | Path) -> Policy:
    """Read and validate a TOML policy."""
    source, payload = read_document(path, context="policy")
    try:
        data = tomllib.loads(payload.decode("utf-8"))
    except (
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        RecursionError,
        OverflowError,
    ) as error:
        raise InputError(f"invalid TOML policy {source}: {error}") from error
    return parse_policy(data, source_hash=hashlib.sha256(payload).hexdigest())
