from __future__ import annotations

import json
from pathlib import Path

import pytest

from regressistor.bundle import (
    canonical_data,
    case_label,
    freeze_bundle,
    load_bundle,
    parse_bundle,
)
from regressistor.errors import InputError, OutputError
from tests.helpers import bundle_dict


def test_parses_bundle_and_preserves_run_metadata() -> None:
    bundle = parse_bundle(bundle_dict(), source_hash="abc", source_path="memory")
    assert bundle.run == {"id": "test"}
    assert bundle.source_hash == "abc"
    assert bundle.source_path == "memory"
    point = bundle.points[0]
    assert dict(point.case) == {"process": "tt", "vdd": 1.0}
    assert point.metrics["gain"].value == 65.0


def test_load_bundle_hashes_bytes(tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle_dict()), encoding="utf-8")
    bundle = load_bundle(path)
    assert len(bundle.source_hash) == 64
    assert bundle.source_path == str(path)


def test_canonical_data_sorts_points_and_metrics() -> None:
    data = bundle_dict()
    second = bundle_dict(process="ss", gain=61.0)["points"][0]
    second["metrics"]["bandwidth"] = {"value": 20.0, "unit": "MHz"}
    data["points"] = [data["points"][0], second]
    bundle = parse_bundle(data, source_hash="source")
    canonical = canonical_data(bundle, frozen_from="source")
    assert canonical["run"]["frozen_from_sha256"] == "source"
    assert canonical["points"][0]["case"]["process"] == "ss"
    assert list(canonical["points"][0]["metrics"]) == ["bandwidth", "gain"]


def test_freeze_refuses_overwrite_then_allows_force(tmp_path: Path) -> None:
    bundle = parse_bundle(bundle_dict(), source_hash="abc")
    target = tmp_path / "nested" / "baseline.json"
    assert freeze_bundle(bundle, target) == target
    frozen = json.loads(target.read_text(encoding="utf-8"))
    assert frozen["run"]["frozen_from_sha256"] == "abc"
    with pytest.raises(OutputError, match="refusing"):
        freeze_bundle(bundle, target)
    freeze_bundle(bundle, target, force=True)


def test_case_label_quotes_strings_and_keeps_numbers() -> None:
    bundle = parse_bundle(bundle_dict())
    assert case_label(bundle.points[0].case) == 'process="tt",vdd=1.0'


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(schema_version=True), "schema_version"),
        (lambda data: data.update(extra=True), "unknown fields"),
        (lambda data: data.update(run=[]), "run must"),
        (lambda data: data.update(points=[]), "non-empty array"),
        (lambda data: data["points"][0].update(extra=True), "unknown fields"),
        (lambda data: data["points"][0].update(case={}), "non-empty object"),
        (lambda data: data["points"][0].update(sample=True), "string or integer"),
        (lambda data: data["points"][0].update(metrics={}), "non-empty object"),
    ],
)
def test_rejects_invalid_bundle_structure(mutation: object, message: str) -> None:
    data = bundle_dict()
    mutation(data)  # type: ignore[operator]
    with pytest.raises(InputError, match=message):
        parse_bundle(data)


def test_rejects_duplicate_case_and_sample() -> None:
    data = bundle_dict()
    data["points"].append(dict(data["points"][0]))
    with pytest.raises(InputError, match="duplicate case/sample"):
        parse_bundle(data)


@pytest.mark.parametrize(
    ("measurement", "message"),
    [
        (3.0, "must be an object"),
        ({"value": True, "unit": "dB"}, "must be numeric"),
        ({"value": float("nan"), "unit": "dB"}, "must be finite"),
        ({"value": 1.0, "unit": 4}, "unit must be a string"),
        ({"value": 1.0, "unit": "unknown"}, "unsupported unit"),
        ({"value": 1.0, "unit": "dB", "note": "x"}, "unknown fields"),
    ],
)
def test_rejects_invalid_measurements(measurement: object, message: str) -> None:
    data = bundle_dict()
    data["points"][0]["metrics"]["gain"] = measurement
    with pytest.raises(InputError, match=message):
        parse_bundle(data)


def test_rejects_non_scalar_case_value() -> None:
    data = bundle_dict()
    data["points"][0]["case"]["process"] = ["tt"]
    with pytest.raises(InputError, match="finite JSON scalar"):
        parse_bundle(data)


def test_rejects_mixed_type_case_keys_without_sorting_error() -> None:
    data = bundle_dict()
    data["points"][0]["case"] = {"process": "tt", 1: "bad"}
    with pytest.raises(InputError, match="keys must be non-empty strings"):
        parse_bundle(data)


def test_rejects_deep_run_metadata_without_recursion_error() -> None:
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    data = bundle_dict()
    data["run"] = {"nested": nested}
    with pytest.raises(InputError, match="complexity limits"):
        parse_bundle(data)


def test_rejects_non_json_run_metadata_and_huge_measurement() -> None:
    data = bundle_dict()
    data["run"] = {"nested": [1, float("nan")]}
    with pytest.raises(InputError, match="finite"):
        parse_bundle(data)

    data = bundle_dict()
    data["points"][0]["metrics"]["gain"]["value"] = 10**1000
    with pytest.raises(InputError, match="finite number"):
        parse_bundle(data)


def test_rejects_invalid_json_missing_file_and_non_object(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="cannot read"):
        load_bundle(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(InputError, match="invalid JSON"):
        load_bundle(broken)
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(InputError, match="root must"):
        load_bundle(array)
