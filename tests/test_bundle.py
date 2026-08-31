from __future__ import annotations

import copy
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


def simcairn_v2_bundle() -> dict[str, object]:
    data = bundle_dict()
    data["schema_version"] = 2
    data["run"] = {
        "producer": "SimCairn",
        "contract": "regressistor.measurement-bundle/2",
        "aggregate_activity_id": "a" * 64,
        "producer_identity": {
            "distribution": "simcairn",
            "version": "0.2.0",
            "package_tree_algorithm": "simcairn-python-source-tree/1",
            "package_tree_sha256": "b" * 64,
            "validation_implementation_sha256": "c" * 64,
            "adapter_implementation_sha256": "d" * 64,
        },
    }
    return data


def test_parses_bundle_and_preserves_run_metadata() -> None:
    bundle = parse_bundle(bundle_dict(), source_hash="a" * 64, source_path="memory")
    assert bundle.run == {"id": "test"}
    assert bundle.source_hash == "a" * 64
    assert bundle.source_path == "memory"
    point = bundle.points[0]
    assert dict(point.case) == {"process": "tt", "vdd": 1.0}
    assert point.metrics["gain"].value == 65.0


def test_bundle_provenance_digests_are_strict() -> None:
    with pytest.raises(InputError, match="source_hash"):
        parse_bundle(bundle_dict(), source_hash="not-a-digest")
    bundle = parse_bundle(bundle_dict())
    with pytest.raises(InputError, match="frozen_from"):
        canonical_data(bundle, frozen_from="not-a-digest")


def test_programmatic_bundle_rejects_non_string_object_keys() -> None:
    data = bundle_dict()
    data[1] = "not-json"  # type: ignore[index]
    with pytest.raises(InputError, match="keys must be strings"):
        parse_bundle(data)


@pytest.mark.parametrize("digits", [129, 5_001])
def test_programmatic_bundle_rejects_oversized_integers(digits: int) -> None:
    data = bundle_dict()
    data["run"] = {"sequence": 10**digits}
    with pytest.raises(InputError, match="oversized number"):
        parse_bundle(data)


def test_load_bundle_rejects_escaped_lone_surrogate(tmp_path: Path) -> None:
    path = tmp_path / "surrogate.json"
    path.write_text(
        '{"schema_version":1,"run":{},"points":[{"case":{"corner":"\\ud800"},'
        '"sample":"0","metrics":{"gain":{"value":1,"unit":"dB"}}}]}',
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="Unicode scalar"):
        load_bundle(path)


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
    bundle = parse_bundle(data, source_hash="a" * 64)
    canonical = canonical_data(bundle, frozen_from="a" * 64)
    assert canonical["run"]["frozen_from_sha256"] == "a" * 64
    assert canonical["points"][0]["case"]["process"] == "ss"
    assert list(canonical["points"][0]["metrics"]) == ["bandwidth", "gain"]


def test_freeze_refuses_overwrite_then_allows_force(tmp_path: Path) -> None:
    bundle = parse_bundle(bundle_dict(), source_hash="a" * 64)
    target = tmp_path / "nested" / "baseline.json"
    assert freeze_bundle(bundle, target) == target
    frozen = json.loads(target.read_text(encoding="utf-8"))
    assert frozen["run"]["frozen_from_sha256"] == "a" * 64
    with pytest.raises(OutputError, match="refusing"):
        freeze_bundle(bundle, target)
    freeze_bundle(bundle, target, force=True)


def test_freeze_exclusive_create_closes_the_overwrite_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = parse_bundle(bundle_dict(), source_hash="a" * 64)
    target = tmp_path / "raced.json"
    original_open = Path.open

    def raced_open(path: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if path == target and mode == "xb" and not target.exists():
            target.write_bytes(b"created-by-racer")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", raced_open)
    with pytest.raises(OutputError, match="refusing"):
        freeze_bundle(bundle, target)
    assert target.read_bytes() == b"created-by-racer"


def test_freeze_simcairn_bundle_round_trips_strict_provenance(tmp_path: Path) -> None:
    data = bundle_dict()
    data["run"] = {
        "producer": "SimCairn",
        "contract": "regressistor.measurement-bundle/1",
        "aggregate_activity_id": "a" * 64,
    }
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    candidate = load_bundle(source)

    frozen_path = freeze_bundle(candidate, tmp_path / "frozen.json")
    frozen = load_bundle(frozen_path)

    assert frozen.run["frozen_from_sha256"] == candidate.source_hash
    assert frozen.run["aggregate_activity_id"] == "a" * 64


def test_simcairn_v2_producer_identity_round_trips_without_input_mutation(
    tmp_path: Path,
) -> None:
    data = simcairn_v2_bundle()
    original = copy.deepcopy(data)
    bundle = parse_bundle(data)
    assert data == original
    assert bundle.run["producer_identity"]["package_tree_sha256"] == "b" * 64
    assert canonical_data(bundle)["schema_version"] == 2

    source = tmp_path / "candidate-v2.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    candidate = load_bundle(source)
    frozen = load_bundle(freeze_bundle(candidate, tmp_path / "frozen-v2.json"))
    assert frozen.run["producer_identity"] == candidate.run["producer_identity"]
    assert frozen.run["frozen_from_sha256"] == candidate.source_hash


def test_freeze_high_unicode_output_always_reloads(tmp_path: Path) -> None:
    data = bundle_dict()
    data["run"] = {"note": "😀" * 150_000}
    bundle = parse_bundle(data, source_hash="a" * 64)
    path = freeze_bundle(bundle, tmp_path / "unicode.json")
    frozen = load_bundle(path)
    assert frozen.run["note"] == bundle.run["note"]
    assert frozen.run["frozen_from_sha256"] == "a" * 64
    assert path.stat().st_size < 1_048_576


def test_freeze_negative_128_digit_integer_always_reloads(tmp_path: Path) -> None:
    data = bundle_dict()
    data["points"][0]["case"]["sequence"] = -int("9" * 128)
    bundle = parse_bundle(data, source_hash="a" * 64)
    path = freeze_bundle(bundle, tmp_path / "negative-boundary.json")
    assert load_bundle(path).points == bundle.points


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version=1),
        lambda data: data["run"].update(contract="regressistor.measurement-bundle/1"),
        lambda data: data["run"].update(extra=True),
        lambda data: data["run"].pop("producer_identity"),
        lambda data: data["run"]["producer_identity"].update(distribution="other"),
        lambda data: data["run"]["producer_identity"].update(version=True),
        lambda data: data["run"]["producer_identity"].update(package_tree_algorithm="other"),
        lambda data: data["run"]["producer_identity"].update(package_tree_sha256="bad"),
        lambda data: data["run"]["producer_identity"].update(extra="bad"),
    ],
)
def test_simcairn_v2_rejects_ambiguous_producer_identity(mutation: object) -> None:
    data = simcairn_v2_bundle()
    mutation(data)  # type: ignore[operator]
    with pytest.raises(InputError):
        parse_bundle(data)


def test_case_label_quotes_strings_and_keeps_numbers() -> None:
    bundle = parse_bundle(bundle_dict())
    assert case_label(bundle.points[0].case) == 'process="tt",vdd=1.0'


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(schema_version=True), "schema_version"),
        (lambda data: data.update(extra=True), "unknown fields"),
        (lambda data: data.pop("run"), "run is required"),
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


@pytest.mark.parametrize("field", ["sample", "unit"])
def test_rejects_missing_explicit_sample_or_unit(field: str) -> None:
    data = bundle_dict()
    if field == "sample":
        del data["points"][0]["sample"]
    else:
        del data["points"][0]["metrics"]["gain"]["unit"]
    with pytest.raises(InputError, match=field):
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
        ({"value": float("nan"), "unit": "dB"}, "non-finite"),
        ({"value": 1.0, "unit": 4}, "unit must be a portable string"),
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
    with pytest.raises(InputError, match="keys must be strings"):
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
    with pytest.raises(InputError, match="oversized number"):
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


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":1,"schema_version":1,"run":{},"points":[]}',
        '{"schema_version":1,"run":{"value":NaN},"points":[]}',
        '{"schema_version":1,"run":{"value":Infinity},"points":[]}',
        '{"schema_version":1,"run":{"value":1e999},"points":[]}',
        '{"schema_version":1,"run":{"value":' + "9" * 129 + '},"points":[]}',
    ],
)
def test_load_bundle_rejects_ambiguous_json(text: str, tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InputError):
        load_bundle(path)


def test_load_bundle_rejects_truncation_oversize_and_node_exhaustion(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.json"
    truncated.write_text('{"schema_version":1,"run":', encoding="utf-8")
    with pytest.raises(InputError, match="invalid JSON"):
        load_bundle(truncated)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 1_048_577)
    with pytest.raises(InputError, match="byte input limit"):
        load_bundle(oversized)

    data = bundle_dict()
    data["run"] = {"nodes": [0] * 10_001}
    with pytest.raises(InputError, match="complexity limits"):
        parse_bundle(data)

    deeply_nested = tmp_path / "deeply-nested.json"
    deeply_nested.write_text(
        '{"schema_version":1,"run":' + "[" * 50_000 + "0" + "]" * 50_000 + ',"points":[]}',
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="complexity limits"):
        load_bundle(deeply_nested)


def test_json_depth_precheck_ignores_brackets_and_escaped_quotes_in_strings(
    tmp_path: Path,
) -> None:
    data = bundle_dict()
    data["run"] = {"description": '[{\\"nested-looking\\": true}]' * 100}
    path = tmp_path / "quoted-structure.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_bundle(path).run == data["run"]


def test_rejects_casefold_collisions_and_unsafe_sample_or_metric() -> None:
    data = bundle_dict()
    data["points"][0]["case"] = {"VDD": 1.0, "vdd": 1.1}
    with pytest.raises(InputError, match="ignoring case"):
        parse_bundle(data)


@pytest.mark.parametrize("target", ["case", "metric", "unit"])
def test_bundle_rejects_oversized_identity_text(target: str) -> None:
    data = bundle_dict()
    oversized = "x" * 257
    if target == "case":
        data["points"][0]["case"] = {oversized: "tt"}
    elif target == "metric":
        measurement = data["points"][0]["metrics"].pop("gain")
        data["points"][0]["metrics"][oversized] = measurement
    else:
        data["points"][0]["metrics"]["gain"]["unit"] = oversized
    with pytest.raises(InputError, match="256"):
        parse_bundle(data)

    data = bundle_dict()
    data["points"][0]["sample"] = "bad\nterminal"
    with pytest.raises(InputError, match="sample"):
        parse_bundle(data)

    data = bundle_dict()
    data["points"][0]["metrics"]["GAIN"] = {"value": 1.0, "unit": "dB"}
    with pytest.raises(InputError, match="ignoring case"):
        parse_bundle(data)


def test_simcairn_contract_requires_bound_provenance() -> None:
    data = bundle_dict()
    data["run"] = {
        "producer": "SimCairn",
        "contract": "regressistor.measurement-bundle/1",
        "aggregate_activity_id": "not-a-digest",
    }
    with pytest.raises(InputError, match="provenance"):
        parse_bundle(data)


def test_simcairn_contract_rejects_unknown_or_invalid_freeze_provenance() -> None:
    data = bundle_dict()
    data["run"] = {
        "producer": "SimCairn",
        "contract": "regressistor.measurement-bundle/1",
        "aggregate_activity_id": "a" * 64,
        "unexpected": "field",
    }
    with pytest.raises(InputError, match="fields are invalid"):
        parse_bundle(data)

    data["run"].pop("unexpected")
    data["run"]["frozen_from_sha256"] = "not-a-digest"
    with pytest.raises(InputError, match="frozen bundle provenance"):
        parse_bundle(data)
