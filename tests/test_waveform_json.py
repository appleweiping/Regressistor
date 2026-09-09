import json
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from regressistor import (
    Waveform,
    WaveformPolicy,
    compare_waveforms,
    dumps_waveform,
    load_waveform,
    load_waveform_policy,
    loads_waveform,
    waveform_comparison_data,
    waveform_data,
    waveform_from_data,
    waveform_policy_data,
    waveform_policy_from_data,
    write_waveform,
    write_waveform_comparison,
    write_waveform_policy,
)
from regressistor.cli import main
from regressistor.errors import InputError, OutputError
from regressistor.waveform import WaveformError


def example():
    return Waveform((0, 1), (-1, 1), "s", "V", "original")


def test_canonical_wire_roundtrip_and_content_identity() -> None:
    source = example()
    encoded = dumps_waveform(source)
    assert loads_waveform(encoded) == source
    assert dumps_waveform(loads_waveform(encoded)) == encoded
    assert waveform_from_data(waveform_data(source)) == source
    assert '"axis":["0x0.0p+0","0x1.0000000000000p+0"]' in encoded
    assert "-1.000" not in encoded


@pytest.mark.parametrize(
    "bad",
    [
        True,
        0,
        0.0,
        "0x1p+0",
        "-0x0.0p+0",
        "nan",
        "inf",
        "0x1.0000000000000p+9999",
        "0x1.0000000000001p-9999",
        "0x1.00000000000001p+0",
        "garbage",
        "x" * 65,
    ],
)
def test_lossy_noncanonical_and_numeric_json_samples_are_rejected(bad) -> None:
    data = waveform_data(example())
    data["axis"][0] = bad
    with pytest.raises(WaveformError):
        waveform_from_data(data)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "different"},
        {"version": True},
        {"version": 1.0},
        {"version": 2},
        {"unknown": 0},
        {"identity": "0" * 64},
        {"identity": 0},
        {"name": None},
        {"axis": None},
        {"values": []},
        {"axis": ["0x0.0p+0"]},
    ],
)
def test_wire_fields_types_identity_and_array_bounds_fail_closed(change) -> None:
    with pytest.raises(WaveformError):
        waveform_from_data({**waveform_data(example()), **change})


@pytest.mark.parametrize(
    "text",
    ['{"schema":1,"schema":2}', "[NaN]", "[Infinity]", "[" * 7 + "0" + "]" * 7, '"\\ud800"', "{"],
)
def test_strict_json_rejects_ambiguous_or_overdeep_documents(text) -> None:
    with pytest.raises(InputError):
        loads_waveform(text)


def test_policy_roundtrip_and_hash_binding(tmp_path) -> None:
    policy = WaveformPolicy(absolute=0.125, relative=0.5, relative_floor=1, grid="linear")
    data = waveform_policy_data(policy)
    assert waveform_policy_from_data(data) == policy
    destination = tmp_path / "policy.json"
    write_waveform_policy(policy, destination)
    assert load_waveform_policy(destination) == policy
    data["relative"] = float(1).hex()
    with pytest.raises(WaveformError, match="identity"):
        waveform_policy_from_data(data)


@pytest.mark.parametrize(
    "change",
    [
        {"max_breakpoints": True},
        {"max_diagnostics": 1.0},
        {"grid": []},
        {"domain": None},
        {"absolute": "-0x1.0000000000000p+0"},
        {"identity": "incorrect"},
        {"relative_floor": "0.1"},
    ],
)
def test_policy_does_not_coerce_types_or_silently_change_tolerance(change) -> None:
    with pytest.raises(WaveformError):
        waveform_policy_from_data({**waveform_policy_data(WaveformPolicy()), **change})


def test_report_records_complete_counts_exact_numbers_and_content_hash() -> None:
    report = compare_waveforms(
        example(),
        replace(example(), values=(-1.125, 0.875)),
        WaveformPolicy(relative=0.125, grid="linear"),
    )
    data = waveform_comparison_data(report)
    assert data["status"] == "fail" and data["passed"] is False
    assert data["scope"] == "continuous_piecewise_linear"
    assert data["maximum_excess"]["axis"] == {"numerator": "0x1", "denominator": "0x2"}
    assert data["maximum_excess"]["excess"] == {"numerator": "0x1", "denominator": "0x8"}
    identity = data.pop("identity")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )
    assert identity == sha256(canonical).hexdigest()


def test_all_three_packaged_structural_schemas_validate_actual_outputs_offline() -> None:
    root = files("regressistor").joinpath("schemas")
    schemas = [
        json.loads(root.joinpath(f"{name}-v1.schema.json").read_text(encoding="utf-8"))
        for name in ("waveform", "waveform-policy", "waveform-comparison")
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    source = example()
    policy = WaveformPolicy(grid="linear")
    values = (
        waveform_data(source),
        waveform_policy_data(policy),
        waveform_comparison_data(compare_waveforms(source, source, policy)),
    )
    for schema, value in zip(schemas, values, strict=True):
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, registry=registry).validate(value)


def test_input_writer_and_report_writer_never_clobber_protected_paths(tmp_path) -> None:
    source = example()
    path = tmp_path / "wave.json"
    write_waveform(source, path)
    assert load_waveform(path) == source
    original = path.read_bytes()
    with pytest.raises(OutputError):
        write_waveform(source, path)
    with pytest.raises(OutputError, match="aliases"):
        write_waveform_comparison(
            compare_waveforms(source, source), path, force=True, protected=(path,)
        )
    assert path.read_bytes() == original
    report = tmp_path / "report.json"
    write_waveform_comparison(compare_waveforms(source, source), report)
    assert json.loads(report.read_text())["passed"] is True


def test_cli_end_to_end_pass_failure_partial_and_input_output_codes(tmp_path, capsys) -> None:
    baseline, candidate, policy = (
        tmp_path / name for name in ("base.json", "candidate.json", "policy.json")
    )
    write_waveform(example(), baseline)
    write_waveform(example(), candidate)
    write_waveform_policy(WaveformPolicy(grid="linear", relative=0.125), policy)
    args = [
        "waveform-check",
        "--baseline",
        str(baseline),
        "--candidate",
        str(candidate),
        "--policy",
        str(policy),
        "--out",
        str(tmp_path / "report.json"),
    ]
    assert main(args) == 0
    assert "Waveform pass" in capsys.readouterr().out
    assert main(args) == 3
    write_waveform(replace(example(), values=(-1.125, 0.875)), candidate, force=True)
    assert main([*args, "--force"]) == 1
    assert "Waveform fail" in capsys.readouterr().out
    write_waveform(Waveform((0.5, 1.5), (0, 2), "s", "V"), candidate, force=True)
    write_waveform_policy(WaveformPolicy(grid="linear", domain="intersection"), policy, force=True)
    assert main([*args, "--force"]) == 1
    assert "Waveform partial_pass" in capsys.readouterr().out
    policy.write_text("{}")
    assert main([*args, "--force"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"baseline_id": "x"},
        {"domain": (Fraction(1), Fraction(0))},
        {"excluded_baseline": (Fraction(-1), Fraction(0))},
        {"domain": (0, 1)},
        {"maximum_deviation": None},
        {"diagnostics": []},
        {"domain": (Fraction(0), Fraction(1 << 32769))},
    ],
)
def test_report_output_boundary_rejects_inconsistent_or_unbounded_dtos(change) -> None:
    result = compare_waveforms(example(), example())
    with pytest.raises(WaveformError):
        waveform_comparison_data(replace(result, **change))
