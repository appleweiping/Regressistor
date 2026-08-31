"""Validation and canonical serialization for measurement bundles."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from regressistor._strict_data import MAX_DOCUMENT_BYTES, check_data_complexity, load_json_path
from regressistor.errors import InputError, OutputError
from regressistor.model import Bundle, CaseKey, Measurement, Point, Scalar, case_identity
from regressistor.units import validate_unit

_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000
_SIMCAIRN_CONTRACT_V1 = "regressistor.measurement-bundle/1"
_SIMCAIRN_CONTRACT_V2 = "regressistor.measurement-bundle/2"
_HEX_DIGEST = frozenset("0123456789abcdef")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}")
_PRODUCER_IDENTITY_FIELDS = {
    "distribution",
    "version",
    "package_tree_algorithm",
    "package_tree_sha256",
    "validation_implementation_sha256",
    "adapter_implementation_sha256",
}


def _portable_text(value: str) -> bool:
    return bool(value) and value == value.strip() and value.isprintable()


def _digest(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_DIGEST for character in value)
    ):
        raise InputError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _producer_identity(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _PRODUCER_IDENTITY_FIELDS:
        raise InputError("SimCairn producer_identity fields are invalid")
    if value["distribution"] != "simcairn":
        raise InputError("SimCairn producer_identity distribution is invalid")
    version = value["version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise InputError("SimCairn producer_identity version is invalid")
    if value["package_tree_algorithm"] != "simcairn-python-source-tree/1":
        raise InputError("SimCairn producer_identity algorithm is invalid")
    for field in (
        "package_tree_sha256",
        "validation_implementation_sha256",
        "adapter_implementation_sha256",
    ):
        _digest(value[field], f"SimCairn producer_identity.{field}")
    return {key: str(value[key]) for key in sorted(_PRODUCER_IDENTITY_FIELDS)}


def _scalar(value: Any, context: str) -> Scalar:
    if isinstance(value, str):
        if not _portable_text(value) or len(value) > 256:
            raise InputError(f"{context} must be a portable string of at most 256 characters")
        return value
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InputError(f"{context} must be a finite JSON scalar")


def _case(raw: Any, context: str) -> CaseKey:
    if not isinstance(raw, Mapping) or not raw:
        raise InputError(f"{context} must be a non-empty object")
    result: list[tuple[str, Scalar]] = []
    if not all(isinstance(key, str) and _portable_text(key) and len(key) <= 256 for key in raw):
        raise InputError(f"{context} keys must be portable strings of at most 256 characters")
    folded = [key.casefold() for key in raw]
    if len(folded) != len(set(folded)):
        raise InputError(f"{context} keys must be unique ignoring case")
    for key in sorted(raw):
        result.append((key, _scalar(raw[key], f"{context}.{key}")))
    return tuple(result)


def _measurement(raw: Any, context: str) -> Measurement:
    if not isinstance(raw, Mapping):
        raise InputError(f"{context} must be an object with value and unit")
    unknown = sorted(set(raw) - {"value", "unit"})
    if unknown:
        raise InputError(f"{context} has unknown fields: {', '.join(unknown)}")
    missing = sorted({"value", "unit"} - set(raw))
    if missing:
        raise InputError(f"{context} is missing required fields: {', '.join(missing)}")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"{context}.value must be numeric")
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise InputError(f"{context}.value must be a finite number") from error
    if not math.isfinite(value):
        raise InputError(f"{context}.value must be finite")
    unit = raw["unit"]
    if not isinstance(unit, str) or not _portable_text(unit) or len(unit) > 256:
        raise InputError(f"{context}.unit must be a portable string of at most 256 characters")
    return Measurement(value, validate_unit(unit))


def parse_bundle(
    data: Mapping[str, Any], *, source_hash: str = "", source_path: str = ""
) -> Bundle:
    """Build a validated bundle from decoded JSON."""
    if source_hash and (
        len(source_hash) != 64 or any(character not in _HEX_DIGEST for character in source_hash)
    ):
        raise InputError("bundle source_hash must be empty or a lowercase SHA-256 digest")
    check_data_complexity(data, "bundle")
    unknown = sorted(set(data) - {"schema_version", "run", "points"})
    if unknown:
        raise InputError(f"bundle has unknown fields: {', '.join(unknown)}")
    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise InputError("bundle.schema_version must be 1 or 2")
    if schema_version not in {1, 2}:
        raise InputError("bundle.schema_version must be 1 or 2")
    if "run" not in data:
        raise InputError("bundle.run is required")
    run = data["run"]
    if not isinstance(run, dict):
        raise InputError("bundle.run must be an object")
    _json_safe(run, "bundle.run", [0])
    normalized_run = dict(run)
    contract = run.get("contract")
    if schema_version == 1 and contract == _SIMCAIRN_CONTRACT_V1:
        provenance_fields = {"producer", "contract", "aggregate_activity_id"}
        frozen_fields = provenance_fields | {"frozen_from_sha256"}
        if set(run) not in (provenance_fields, frozen_fields):
            raise InputError("SimCairn bundle.run fields are invalid")
        activity_id = run.get("aggregate_activity_id")
        if run.get("producer") != "SimCairn":
            raise InputError("SimCairn bundle.run provenance is invalid")
        _digest(activity_id, "SimCairn provenance aggregate_activity_id")
        frozen_from = run.get("frozen_from_sha256")
        if frozen_from is not None:
            _digest(frozen_from, "SimCairn frozen bundle provenance")
    elif schema_version == 2:
        if contract != _SIMCAIRN_CONTRACT_V2:
            raise InputError(
                "bundle.schema_version 2 requires the SimCairn measurement-bundle/2 contract"
            )
        provenance_fields = {
            "producer",
            "producer_identity",
            "contract",
            "aggregate_activity_id",
        }
        frozen_fields = provenance_fields | {"frozen_from_sha256"}
        if set(run) not in (provenance_fields, frozen_fields):
            raise InputError("SimCairn v2 bundle.run fields are invalid")
        if run.get("producer") != "SimCairn":
            raise InputError("SimCairn v2 bundle.run provenance is invalid")
        _digest(run.get("aggregate_activity_id"), "SimCairn aggregate_activity_id")
        normalized_run["producer_identity"] = _producer_identity(run.get("producer_identity"))
        frozen_from = run.get("frozen_from_sha256")
        if frozen_from is not None:
            _digest(frozen_from, "SimCairn frozen_from_sha256")
    elif isinstance(contract, str) and contract.startswith("regressistor.measurement-bundle/"):
        raise InputError("SimCairn bundle contract and schema_version are inconsistent")

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
        if "sample" not in raw_point:
            raise InputError(f"{context}.sample is required")
        sample_raw = raw_point["sample"]
        if isinstance(sample_raw, bool) or not isinstance(sample_raw, str | int):
            raise InputError(f"{context}.sample must be a string or integer")
        sample = str(sample_raw)
        if not _portable_text(sample) or len(sample) > 256:
            raise InputError(f"{context}.sample must be a printable non-empty identifier")
        identity = (case_identity(case), sample)
        if identity in identities:
            raise InputError(f"duplicate case/sample at {context}: {dict(case)!r}, {sample!r}")
        identities.add(identity)

        raw_metrics = raw_point.get("metrics")
        if not isinstance(raw_metrics, Mapping) or not raw_metrics:
            raise InputError(f"{context}.metrics must be a non-empty object")
        metrics: dict[str, Measurement] = {}
        folded_metrics: set[str] = set()
        for name, raw_measurement in raw_metrics.items():
            if not isinstance(name, str) or not _portable_text(name) or len(name) > 256:
                raise InputError(
                    f"{context}.metrics keys must be portable strings of at most 256 characters"
                )
            if name.casefold() in folded_metrics:
                raise InputError(f"{context}.metrics keys must be unique ignoring case")
            folded_metrics.add(name.casefold())
            metrics[name] = _measurement(raw_measurement, f"{context}.metrics.{name}")
        points.append(Point(case, metrics, sample))
    return Bundle(tuple(points), normalized_run, source_hash, source_path)


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
    source, payload, data = load_json_path(path, context="bundle")
    if not isinstance(data, Mapping):
        raise InputError("bundle root must be an object")
    return parse_bundle(
        data,
        source_hash=hashlib.sha256(payload).hexdigest(),
        source_path=str(source),
    )


def canonical_data(bundle: Bundle, *, frozen_from: str | None = None) -> dict[str, Any]:
    """Return stable, JSON-serializable bundle data."""
    if frozen_from is not None and (
        len(frozen_from) != 64 or any(character not in _HEX_DIGEST for character in frozen_from)
    ):
        raise InputError("frozen_from must be a lowercase SHA-256 digest")
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
    schema_version = 2 if run.get("contract") == _SIMCAIRN_CONTRACT_V2 else 1
    return {"schema_version": schema_version, "run": run, "points": points}


def freeze_bundle(bundle: Bundle, destination: str | Path, *, force: bool = False) -> Path:
    """Write a canonical baseline without modifying the source bundle."""
    target = Path(destination)
    try:
        data = canonical_data(bundle, frozen_from=bundle.source_hash)
        parse_bundle(data)
        payload = (
            json.dumps(
                data,
                indent=2,
                sort_keys=True,
                allow_nan=False,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise OutputError(f"baseline exceeds {MAX_DOCUMENT_BYTES} byte serialized output limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        if force:
            target.write_bytes(payload)
        else:
            try:
                with target.open("xb") as stream:
                    stream.write(payload)
            except FileExistsError as error:
                raise OutputError(f"refusing to overwrite existing baseline: {target}") from error
    except OSError as error:
        raise OutputError(f"cannot write baseline {target}: {error}") from error
    except (TypeError, ValueError) as error:
        raise OutputError(f"cannot serialize baseline {target}: {error}") from error
    return target


def case_label(case: CaseKey) -> str:
    """Render a stable human-readable case identifier."""
    return ",".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in case)
