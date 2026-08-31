"""Validation and canonical serialization for measurement bundles."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from regressistor.errors import InputError, OutputError
from regressistor.model import Bundle, CaseKey, Measurement, Point, Scalar, case_identity
from regressistor.units import validate_unit

_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000
_MAX_JSON_BYTES = 1_048_576


def _scalar(value: Any, context: str) -> Scalar:
    if isinstance(value, bool | str | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InputError(f"{context} must be a finite JSON scalar")


def _case(raw: Any, context: str) -> CaseKey:
    if not isinstance(raw, Mapping) or not raw:
        raise InputError(f"{context} must be a non-empty object")
    result: list[tuple[str, Scalar]] = []
    if not all(isinstance(key, str) and key for key in raw):
        raise InputError(f"{context} keys must be non-empty strings")
    for key in sorted(raw):
        result.append((key, _scalar(raw[key], f"{context}.{key}")))
    return tuple(result)


def _measurement(raw: Any, context: str) -> Measurement:
    if not isinstance(raw, Mapping):
        raise InputError(f"{context} must be an object with value and unit")
    unknown = sorted(set(raw) - {"value", "unit"})
    if unknown:
        raise InputError(f"{context} has unknown fields: {', '.join(unknown)}")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"{context}.value must be numeric")
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise InputError(f"{context}.value must be a finite number") from error
    if not math.isfinite(value):
        raise InputError(f"{context}.value must be finite")
    unit = raw.get("unit", "1")
    if not isinstance(unit, str):
        raise InputError(f"{context}.unit must be a string")
    return Measurement(value, validate_unit(unit))


def parse_bundle(
    data: Mapping[str, Any], *, source_hash: str = "", source_path: str = ""
) -> Bundle:
    """Build a validated bundle from decoded JSON."""
    unknown = sorted(set(data) - {"schema_version", "run", "points"})
    if unknown:
        raise InputError(f"bundle has unknown fields: {', '.join(unknown)}")
    schema_version = data.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise InputError("bundle.schema_version must be 1")
    run = data.get("run", {})
    if not isinstance(run, dict):
        raise InputError("bundle.run must be an object")
    _json_safe(run, "bundle.run", [0])

    raw_points = data.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise InputError("bundle.points must be a non-empty array")
    points: list[Point] = []
    identities: set[tuple[object, str]] = set()
    for index, raw_point in enumerate(raw_points):
        context = f"bundle.points[{index}]"
        if not isinstance(raw_point, Mapping):
            raise InputError(f"{context} must be an object")
        unknown_point = sorted(set(raw_point) - {"case", "metrics", "sample"})
        if unknown_point:
            raise InputError(f"{context} has unknown fields: {', '.join(unknown_point)}")
        case = _case(raw_point.get("case"), f"{context}.case")
        sample_raw = raw_point.get("sample", "0")
        if isinstance(sample_raw, bool) or not isinstance(sample_raw, str | int):
            raise InputError(f"{context}.sample must be a string or integer")
        sample = str(sample_raw)
        identity = (case_identity(case), sample)
        if identity in identities:
            raise InputError(f"duplicate case/sample at {context}: {dict(case)!r}, {sample!r}")
        identities.add(identity)

        raw_metrics = raw_point.get("metrics")
        if not isinstance(raw_metrics, Mapping) or not raw_metrics:
            raise InputError(f"{context}.metrics must be a non-empty object")
        metrics: dict[str, Measurement] = {}
        for name, raw_measurement in raw_metrics.items():
            if not isinstance(name, str) or not name:
                raise InputError(f"{context}.metrics keys must be non-empty strings")
            metrics[name] = _measurement(raw_measurement, f"{context}.metrics.{name}")
        points.append(Point(case, metrics, sample))
    return Bundle(tuple(points), dict(run), source_hash, source_path)


def _json_safe(value: Any, context: str, nodes: list[int], depth: int = 0) -> None:
    nodes[0] += 1
    if depth > _MAX_JSON_DEPTH or nodes[0] > _MAX_JSON_NODES:
        raise InputError(f"{context} exceeds metadata complexity limits")
    if value is None or isinstance(value, bool | str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputError(f"{context} must not contain non-finite numbers")
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
    raise InputError(f"{context} must contain only JSON-compatible values")


def load_bundle(path: str | Path) -> Bundle:
    """Read and validate a UTF-8 JSON result bundle."""
    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise InputError(f"cannot read bundle {source}: {error}") from error
    if len(payload) > _MAX_JSON_BYTES:
        raise InputError(f"bundle exceeds {_MAX_JSON_BYTES} byte input limit")
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(f"invalid JSON bundle {source}: {error}") from error
    if not isinstance(data, Mapping):
        raise InputError("bundle root must be an object")
    return parse_bundle(
        data,
        source_hash=hashlib.sha256(payload).hexdigest(),
        source_path=str(source),
    )


def canonical_data(bundle: Bundle, *, frozen_from: str | None = None) -> dict[str, Any]:
    """Return stable, JSON-serializable bundle data."""
    run = dict(bundle.run)
    if frozen_from is not None:
        run["frozen_from_sha256"] = frozen_from
    points = []
    for point in sorted(bundle.points, key=lambda item: (case_label(item.case), item.sample)):
        points.append(
            {
                "case": dict(point.case),
                "sample": point.sample,
                "metrics": {
                    name: {"value": measurement.value, "unit": measurement.unit}
                    for name, measurement in sorted(point.metrics.items())
                },
            }
        )
    return {"schema_version": 1, "run": run, "points": points}


def freeze_bundle(bundle: Bundle, destination: str | Path, *, force: bool = False) -> Path:
    """Write a canonical baseline without modifying the source bundle."""
    target = Path(destination)
    if target.exists() and not force:
        raise OutputError(f"refusing to overwrite existing baseline: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            canonical_data(bundle, frozen_from=bundle.source_hash),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        target.write_text(text + "\n", encoding="utf-8")
    except OSError as error:
        raise OutputError(f"cannot write baseline {target}: {error}") from error
    return target


def case_label(case: CaseKey) -> str:
    """Render a stable human-readable case identifier."""
    return ",".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in case)
