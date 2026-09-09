from __future__ import annotations

import hashlib
import json
import os
import runpy
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import regressistor
from regressistor.bundle import load_bundle
from regressistor.cli import main
from regressistor.errors import InputError, OutputError
from regressistor.liberty import (
    LibertyLimits,
    convert_liberty_nldm,
    load_liberty_nldm,
    write_liberty_bundle,
)

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_nldm.lib"


def minimal_liberty(*, table: str | None = None) -> bytes:
    selected_table = table or 'cell_rise (one_axis) { values ("1.0, 2.0"); }'
    return f"""\
library (small) {{
  time_unit : "1ns";
  voltage_unit : "1V";
  default_operating_conditions : tt;
  operating_conditions (tt) {{ process : 1; voltage : 1.0; temperature : 25; }}
  lu_table_template (one_axis) {{
    variable_1 : input_net_transition;
    index_1 ("0.1, 0.2");
  }}
  cell (BUF) {{
    pin (Y) {{
      timing () {{
        related_pin : "A";
        timing_type : combinational;
        {selected_table}
      }}
    }}
  }}
}}
""".encode()


def metric_points(conversion: object, name: str) -> list[object]:
    bundle = conversion.bundle  # type: ignore[attr-defined]
    return [point for point in bundle.points if name in point.metrics]


def test_fixture_converts_every_supported_family_with_provenance(tmp_path: Path) -> None:
    conversion = load_liberty_nldm(FIXTURE)

    assert len(conversion.bundle.points) == 33
    assert conversion.source_sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert conversion.source_name == FIXTURE.name
    assert conversion.bundle.source_hash == conversion.source_sha256
    assert conversion.bundle.run["contract"] == "regressistor.liberty-nldm/1"
    assert conversion.bundle.run["source"] == {
        "name": FIXTURE.name,
        "sha256": conversion.source_sha256,
    }
    assert conversion.bundle.run["operating_conditions"] == {
        "name": "typical",
        "process": 1.0,
        "voltage_v": 1.1,
        "temperature_c": 25.0,
    }
    assert conversion.bundle.run["units"] == {
        "capacitance": {"multiplier": 2.0, "unit": "pF"},
        "current": {"multiplier": 1.0, "unit": "mA"},
        "internal_energy": {"multiplier": 2e-12, "unit": "J"},
        "power": {"multiplier": 10.0, "unit": "uW"},
        "time": {"multiplier": 100.0, "unit": "ps"},
        "voltage": {"multiplier": 1.0, "unit": "V"},
    }

    rise = metric_points(conversion, "cell_rise")
    assert len(rise) == 4
    rise_line = next(
        index
        for index, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines(), start=1)
        if "cell_rise (delay_template)" in line
    )
    assert {point.sample for point in rise} == {f"L{rise_line}:C9"}  # type: ignore[union-attr]
    first_case = dict(rise[0].case)  # type: ignore[union-attr]
    assert first_case["cell"] == "INVX1"
    assert first_case["port"] == "Y"
    assert first_case["related_pin"] == "A"
    assert first_case["axis_1"] == "input_net_transition"
    assert first_case["axis_1_value"] == pytest.approx(2.0)
    assert first_case["axis_1_unit"] == "ps"
    assert first_case["axis_2_value"] == pytest.approx(1.0)
    assert first_case["axis_2_unit"] == "pF"
    assert rise[0].metrics["cell_rise"].value == pytest.approx(50.0)  # type: ignore[union-attr]
    assert rise[0].metrics["cell_rise"].unit == "ps"  # type: ignore[union-attr]

    power = metric_points(conversion, "rise_power")
    assert [point.metrics["rise_power"].value for point in power] == pytest.approx(  # type: ignore[union-attr]
        [0.2e-12, 0.4e-12], rel=1e-12, abs=0
    )
    assert all(point.metrics["rise_power"].unit == "J" for point in power)  # type: ignore[union-attr]
    assert dict(power[0].case)["when"] == "A"  # type: ignore[union-attr]
    assert dict(power[0].case)["power_level"] == "VDD"  # type: ignore[union-attr]
    assert dict(power[0].case)["mode_name"] == "functional"  # type: ignore[union-attr]
    assert dict(power[0].case)["mode_value"] == "enabled"  # type: ignore[union-attr]

    pulse_widths = metric_points(conversion, "min_pulse_width_high")
    direct_pulse = next(point for point in pulse_widths if "when" not in dict(point.case))  # type: ignore[union-attr]
    grouped_pulse = next(point for point in pulse_widths if dict(point.case).get("when") == "TEST")  # type: ignore[union-attr]
    assert direct_pulse.metrics["min_pulse_width_high"].value == pytest.approx(75.0)  # type: ignore[union-attr]
    assert grouped_pulse.metrics["min_pulse_width_high"].value == pytest.approx(80.0)  # type: ignore[union-attr]
    periods = metric_points(conversion, "minimum_period")
    direct_period = next(point for point in periods if "when" not in dict(point.case))  # type: ignore[union-attr]
    grouped_period = next(point for point in periods if dict(point.case).get("when") == "TEST")  # type: ignore[union-attr]
    assert direct_period.metrics["minimum_period"].value == pytest.approx(250.0)  # type: ignore[union-attr]
    assert grouped_period.metrics["minimum_period"].value == pytest.approx(275.0)  # type: ignore[union-attr]
    constraint_types = {
        dict(point.case).get("timing_type")
        for point in metric_points(conversion, "rise_constraint")
    }
    assert constraint_types == {"setup_rising", "hold_rising", "min_pulse_width", "minimum_period"}

    assert conversion.metric_sources
    assert {source.metric for source in conversion.metric_sources} >= {
        "cell_rise",
        "cell_fall",
        "rise_transition",
        "fall_transition",
        "rise_power",
        "fall_power",
        "rise_constraint",
        "fall_constraint",
    }
    assert all(
        point.sample.startswith("L") and ":C" in point.sample for point in conversion.bundle.points
    )

    output = tmp_path / "nested" / "bundle.json"
    assert write_liberty_bundle(conversion, output) == output
    loaded = load_bundle(output)
    assert loaded.points == conversion.bundle.points
    assert loaded.run == conversion.bundle.run
    assert json.loads(output.read_text(encoding="utf-8")) == conversion.canonical_data()


def test_conversion_is_byte_hash_bound_and_deterministic() -> None:
    payload = minimal_liberty()
    first = convert_liberty_nldm(payload, source_name="memory.lib")
    second = convert_liberty_nldm(payload, source_name="memory.lib")
    changed = convert_liberty_nldm(payload + b"\n", source_name="memory.lib")

    assert first.canonical_data() == second.canonical_data()
    assert first.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert changed.source_sha256 != first.source_sha256
    assert changed.bundle.points == first.bundle.points
    assert first.bundle.source_path == "memory.lib"


def test_public_api_exports_liberty_boundary() -> None:
    assert regressistor.convert_liberty_nldm is convert_liberty_nldm
    assert regressistor.load_liberty_nldm is load_liberty_nldm
    assert regressistor.write_liberty_bundle is write_liberty_bundle
    assert regressistor.LibertyLimits is LibertyLimits


def test_converted_fixture_conforms_to_public_bundle_v1_schema() -> None:
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "docs" / "schemas" / "measurement-bundle-1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(load_liberty_nldm(FIXTURE).canonical_data())


def test_generated_matrix_coordinates_have_cartesian_product_property() -> None:
    for rows in range(1, 4):
        for columns in range(1, 4):
            first = ", ".join(str(index + 1) for index in range(rows))
            second = ", ".join(str(index + 10) for index in range(columns))
            matrix_rows = []
            expected: dict[tuple[float, float], float] = {}
            for row in range(rows):
                values = []
                for column in range(columns):
                    value = float(row * 10 + column) / 10.0
                    values.append(str(value))
                    expected[(float(row + 1), float(column + 10))] = value
                matrix_rows.append(f'"{", ".join(values)}"')
            table = f"""cell_rise (matrix) {{ values ({", ".join(matrix_rows)}); }}"""
            payload = f"""\
library (matrix_lib) {{
 time_unit : "1ns"; voltage_unit : "1V"; capacitive_load_unit (1, pF);
 operating_conditions (tt) {{ process: 1; voltage: 1; temperature: 25; }}
 lu_table_template (matrix) {{ variable_1: input_net_transition;
   variable_2: total_output_net_capacitance; index_1 ("{first}"); index_2 ("{second}"); }}
 cell (BUF) {{ pin (Y) {{ timing () {{ related_pin: "A"; {table} }} }} }}
}}
""".encode()
            conversion = convert_liberty_nldm(payload)
            points = metric_points(conversion, "cell_rise")
            assert len(points) == rows * columns
            observed = {
                (
                    float(dict(point.case)["axis_1_value"]),  # type: ignore[union-attr]
                    float(dict(point.case)["axis_2_value"]),  # type: ignore[union-attr]
                ): point.metrics["cell_rise"].value  # type: ignore[union-attr]
                for point in points
            }
            assert observed == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"library(x) {", "unterminated Liberty group"),
        (b"/* never closed", "unterminated block comment"),
        (b'library ("never closed)', "unterminated string"),
        (b'library ("bad\\n") {}', "unsupported string escape"),
        (b"library(x) { value : ; }", "attribute value is empty"),
        (b"library(,x) {}", "empty Liberty argument"),
        (b"library(x,) {}", "empty Liberty argument"),
        (b"library(x) { foo : (x)); }", "unmatched"),
        (b"library(x) { foo }", "must be an attribute"),
        (b"library(x) { foo; }", "must be an attribute"),
        (b"library(x) {\x00}", "control character"),
    ],
)
def test_lexical_and_structural_failures_are_located(payload: bytes, message: str) -> None:
    with pytest.raises(InputError, match=message) as caught:
        convert_liberty_nldm(payload)
    assert "Liberty L" in str(caught.value)


def test_invalid_utf8_and_source_names_are_rejected() -> None:
    with pytest.raises(InputError, match="UTF-8"):
        convert_liberty_nldm(b"\xff")
    with pytest.raises(InputError, match="source name"):
        convert_liberty_nldm(minimal_liberty(), source_name="bad\nname")
    with pytest.raises(InputError, match="NFC"):
        convert_liberty_nldm(minimal_liberty(), source_name="e\u0301.lib")
    with pytest.raises(TypeError, match="bytes"):
        convert_liberty_nldm("not bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="source_name"):
        convert_liberty_nldm(minimal_liberty(), source_name=3)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("library (small)", "library (small) {} library (small)", "library group"),
        (
            'time_unit : "1ns";',
            'time_unit : "1ns"; time_unit : "1ns";',
            "duplicate time_unit",
        ),
        ('time_unit : "1ns";', 'time_unit : "1V";', "wrong physical dimension"),
        ('time_unit : "1ns";', 'time_unit : "0ns";', "multiplier must be positive"),
        ('time_unit : "1ns";', 'time_unit : "1parsec";', "unsupported Liberty unit"),
        ("process : 1", "process : 1e999", "non-finite"),
        ("process : 1", "process : 1e-999", "underflows"),
        ("voltage : 1.0", "voltage : 0", "voltage must be positive"),
        (
            "default_operating_conditions : tt;",
            "default_operating_conditions : missing;",
            "does not name exactly one",
        ),
        ("cell (BUF)", "cell (BUF) {} cell (buf)", "duplicate cell"),
        ("pin (Y)", "pin (Y) {} pin (y)", "duplicate port"),
        ("cell_rise (one_axis)", "cell_rise (missing)", "unknown lookup template"),
        ("variable_1 : input_net_transition;", "variable_1 : mystery_axis;", "ambiguous"),
        ('values ("1.0, 2.0")', 'values ("1.0", "2.0")', "do not match index_1"),
        ('values ("1.0, 2.0")', 'values ("1.0, NaN")', "invalid finite decimal"),
        ('values ("1.0, 2.0")', 'values ("1.0,")', "empty numeric field"),
        ('index_1 ("0.1, 0.2")', 'index_1 ("0.2, 0.1")', "strictly increasing"),
        (
            'cell_rise (one_axis) { values ("1.0, 2.0"); }',
            'cell_rise (one_axis) { values ("1.0, 2.0"); } '
            'cell_rise (one_axis) { values ("1.0, 2.0"); }',
            "duplicate or ambiguous",
        ),
    ],
)
def test_semantic_ambiguity_and_numeric_errors_are_rejected(
    old: str, new: str, message: str
) -> None:
    payload = minimal_liberty().decode().replace(old, new).encode()
    with pytest.raises(InputError, match=message):
        convert_liberty_nldm(payload)


def test_operating_condition_selection_rejects_ambiguity_and_missing_voltage_unit() -> None:
    ambiguous = (
        minimal_liberty()
        .decode()
        .replace(
            "default_operating_conditions : tt;",
            "operating_conditions (ss) { process : 0.8; voltage : 0.9; temperature : 125; }",
        )
    )
    with pytest.raises(InputError, match="missing or ambiguous"):
        convert_liberty_nldm(ambiguous.encode())

    missing_unit = minimal_liberty().decode().replace('  voltage_unit : "1V";\n', "")
    with pytest.raises(InputError, match="voltage_unit"):
        convert_liberty_nldm(missing_unit.encode())


def test_nominal_pvt_attributes_are_a_strict_fallback() -> None:
    payload = (
        minimal_liberty()
        .decode()
        .replace("  default_operating_conditions : tt;\n", "")
        .replace(
            "  operating_conditions (tt) { process : 1; voltage : 1.0; temperature : 25; }",
            "  nom_process : 0.9; nom_voltage : 1.2; nom_temperature : 85;",
        )
        .encode()
    )
    conversion = convert_liberty_nldm(payload)
    assert conversion.bundle.run["operating_conditions"] == {
        "name": "nominal",
        "process": 0.9,
        "voltage_v": 1.2,
        "temperature_c": 85.0,
    }
    assert {dict(point.case)["operating_condition"] for point in conversion.bundle.points} == {
        "nominal"
    }

    missing = payload.decode().replace("nom_temperature : 85;", "")
    with pytest.raises(InputError, match="nom_temperature"):
        convert_liberty_nldm(missing.encode())


def test_template_cannot_silently_renumber_a_second_axis() -> None:
    payload = (
        minimal_liberty().replace(b"variable_1", b"variable_2").replace(b"index_1", b"index_2")
    )
    with pytest.raises(InputError, match="axes must be contiguous"):
        convert_liberty_nldm(payload)


def test_template_and_scalar_shape_boundaries() -> None:
    incomplete = minimal_liberty().decode().replace('    index_1 ("0.1, 0.2");\n', "")
    with pytest.raises(InputError, match="incomplete"):
        convert_liberty_nldm(incomplete.encode())

    scalar_with_axis = minimal_liberty(
        table='cell_rise (scalar) { variable_1 : input_net_transition; index_1 ("1"); values ("1"); }'
    )
    with pytest.raises(InputError, match="scalar.*no axes"):
        convert_liberty_nldm(scalar_with_axis)

    three_dimensional = (
        minimal_liberty()
        .decode()
        .replace(
            '    index_1 ("0.1, 0.2");',
            '    index_1 ("0.1, 0.2"); variable_3 : time; index_3 ("1");',
        )
    )
    with pytest.raises(InputError, match="Three-dimensional|three-dimensional"):
        convert_liberty_nldm(three_dimensional.encode())

    duplicate_values = minimal_liberty(
        table='cell_rise (one_axis) { values ("1, 2"); values ("1, 2"); }'
    )
    with pytest.raises(InputError, match="must appear exactly once"):
        convert_liberty_nldm(duplicate_values)


def test_table_can_declare_a_local_axis_without_a_template() -> None:
    payload = minimal_liberty(
        table='cell_rise () { variable_1 : input_net_transition; index_1 ("0.3, 0.4"); '
        'values ("3, 4"); }'
    )
    conversion = convert_liberty_nldm(payload)
    points = metric_points(conversion, "cell_rise")
    assert [dict(point.case)["axis_1_value"] for point in points] == [0.3, 0.4]  # type: ignore[union-attr]
    assert [point.metrics["cell_rise"].value for point in points] == [3.0, 4.0]  # type: ignore[union-attr]


def test_internal_tables_are_energy_not_leakage_power() -> None:
    # The table's 0.15 uses C_unit * V_unit**2, not leakage_power_unit.
    payload = FIXTURE.read_bytes()
    original = load_liberty_nldm(FIXTURE)
    energy = metric_points(original, "fall_power")[0].metrics["fall_power"]
    assert energy.unit == "J"
    assert energy.value == pytest.approx(0.15 * 2e-12, rel=1e-12, abs=0)
    changed = convert_liberty_nldm(payload.replace(b'"10uw"', b'"100mW"'))
    assert metric_points(changed, "fall_power")[0].metrics["fall_power"] == energy
    without_leakage = convert_liberty_nldm(payload.replace(b'leakage_power_unit : "10uw";', b""))
    assert metric_points(without_leakage, "fall_power")[0].metrics["fall_power"] == energy


def test_internal_energy_scales_capacitance_and_squared_voltage_not_pvt() -> None:
    payload = FIXTURE.read_bytes().replace(b'"1v"', b'"100mV"').replace(b"(2, pf)", b"(2, ff)")
    conversion = convert_liberty_nldm(payload)
    energy = metric_points(conversion, "fall_power")[0].metrics["fall_power"]
    assert energy.value == pytest.approx(0.15 * 2e-15 * 0.1**2, rel=1e-12, abs=0)
    changed = convert_liberty_nldm(payload.replace(b"voltage : 1.1", b"voltage : 9.0"))
    assert metric_points(changed, "fall_power")[0].metrics["fall_power"] == energy


@pytest.mark.parametrize("voltage_unit", [b"1e200V", b"1e-200V"])
def test_internal_energy_scale_rejects_floating_range_loss(voltage_unit: bytes) -> None:
    payload = FIXTURE.read_bytes().replace(b'"1v"', b'"' + voltage_unit + b'"')
    with pytest.raises(InputError, match="energy unit scale overflowed or underflowed"):
        convert_liberty_nldm(payload)


def test_internal_energy_requires_declared_capacitance_unit() -> None:
    payload = minimal_liberty(
        table='cell_rise (one_axis) { values ("1,2"); } } '
        'internal_power () { related_pin : "A"; rise_power (scalar) { values ("1"); }'
    )
    with pytest.raises(InputError, match="capacitance unit is required"):
        convert_liberty_nldm(payload)


def test_standard_constraint_groups_reject_missing_and_duplicate_values() -> None:
    fixture = FIXTURE.read_text(encoding="utf-8")
    empty_pulse = fixture.replace(
        "        constraint_high : 0.80;\n        constraint_low : 0.70;\n", ""
    )
    with pytest.raises(InputError, match="has no constraint"):
        convert_liberty_nldm(empty_pulse.encode())

    duplicate_pulse = fixture.replace(
        "        constraint_high : 0.80;",
        "        constraint_high : 0.80;\n        constraint_high : 0.81;",
    )
    with pytest.raises(InputError, match="duplicate constraint_high"):
        convert_liberty_nldm(duplicate_pulse.encode())

    missing_period = fixture.replace("        constraint : 2.75;\n", "")
    with pytest.raises(InputError, match="must appear exactly once"):
        convert_liberty_nldm(missing_period.encode())


def test_bus_ports_and_related_port_qualifiers_are_preserved() -> None:
    payload = (
        minimal_liberty()
        .decode()
        .replace("pin (Y)", "bus (Y)")
        .replace(
            'related_pin : "A";',
            'related_pin : "A"; related_output_pin : "QN"; related_bus_pins : "DATA";',
        )
        .encode()
    )
    conversion = convert_liberty_nldm(payload)
    case = dict(conversion.bundle.points[0].case)
    assert case["port_kind"] == "bus"
    assert case["related_output_pin"] == "QN"
    assert case["related_bus_pins"] == "DATA"


def test_timing_inside_bus_member_pin_is_imported_with_bus_lineage() -> None:
    payload = (
        minimal_liberty()
        .decode()
        .replace(
            "pin (Y)",
            "bus (DATA) { pin (DATA[1:0])",
        )
        .replace(
            "      }\n    }\n  }\n}",
            "      }\n    } }\n  }\n}",
        )
        .encode()
    )

    conversion = convert_liberty_nldm(payload)
    points = metric_points(conversion, "cell_rise")

    assert len(points) == 2
    assert {dict(point.case)["bus"] for point in points} == {"DATA"}  # type: ignore[union-attr]
    assert {dict(point.case)["port"] for point in points} == {"DATA[1:0]"}  # type: ignore[union-attr]
    assert {dict(point.case)["port_kind"] for point in points} == {"pin"}  # type: ignore[union-attr]


def test_supported_parent_groups_reject_unrepresented_arguments_and_bad_modes() -> None:
    timing_argument = minimal_liberty().decode().replace("timing ()", "timing (named)")
    with pytest.raises(InputError, match="does not accept arguments"):
        convert_liberty_nldm(timing_argument.encode())

    bad_mode = (
        minimal_liberty()
        .decode()
        .replace('related_pin : "A";', 'related_pin : "A"; mode (only_one);')
    )
    with pytest.raises(InputError, match="mode requires exactly two"):
        convert_liberty_nldm(bad_mode.encode())


def test_no_measurements_and_no_cells_are_rejected() -> None:
    no_cells = b"""library(x) { voltage_unit:"1V"; operating_conditions(tt) {
      process:1; voltage:1; temperature:25; } }"""
    with pytest.raises(InputError, match="no cell"):
        convert_liberty_nldm(no_cells)

    no_measurements = (
        minimal_liberty()
        .decode()
        .replace('cell_rise (one_axis) { values ("1.0, 2.0"); }', "direction : output;")
    )
    with pytest.raises(InputError, match="no supported"):
        convert_liberty_nldm(no_measurements.encode())


def test_resource_limits_fail_closed() -> None:
    payload = minimal_liberty()
    with pytest.raises(InputError, match="byte limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_bytes=10))
    with pytest.raises(InputError, match="token limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_tokens=10))
    with pytest.raises(InputError, match="token exceeds byte limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_token_bytes=3))
    with pytest.raises(InputError, match="entry limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_entries=3))
    with pytest.raises(InputError, match="numeric value limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_numeric_values=3))
    with pytest.raises(InputError, match="output point limit"):
        convert_liberty_nldm(payload, limits=LibertyLimits(max_output_points=1))

    nested = payload.decode().replace(
        "cell (BUF) {",
        "wrapper(a) { wrapper(b) { wrapper(c) { } } } cell (BUF) {",
    )
    with pytest.raises(InputError, match="nesting"):
        convert_liberty_nldm(nested.encode(), limits=LibertyLimits(max_nesting=2))

    two_tables = minimal_liberty(
        table='cell_rise (one_axis) { values ("1,2"); } cell_fall (one_axis) { values ("1,2"); }'
    )
    with pytest.raises(InputError, match="table limit"):
        convert_liberty_nldm(two_tables, limits=LibertyLimits(max_tables=1))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_limits_require_positive_integers(value: object) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        LibertyLimits(max_tokens=value)  # type: ignore[arg-type]


def test_limits_cannot_raise_hard_safety_maxima() -> None:
    with pytest.raises(ValueError, match="hard safety maxima"):
        LibertyLimits(max_nesting=65)


def test_writer_refuses_overwrite_and_force_is_explicit(tmp_path: Path) -> None:
    conversion = convert_liberty_nldm(minimal_liberty())
    target = tmp_path / "bundle.json"
    target.write_text("racer", encoding="utf-8")
    with pytest.raises(OutputError, match="refusing"):
        write_liberty_bundle(conversion, target)
    assert target.read_text(encoding="utf-8") == "racer"
    write_liberty_bundle(conversion, target, force=True)
    assert load_bundle(target).points == conversion.bundle.points
    assert not list(tmp_path.glob(".*.tmp"))


def test_liberty_writer_never_overwrites_a_protected_input(tmp_path: Path) -> None:
    conversion = convert_liberty_nldm(minimal_liberty())
    source = tmp_path / "source.lib"
    original = minimal_liberty()
    source.write_bytes(original)
    with pytest.raises(OutputError, match="aliases an input"):
        write_liberty_bundle(conversion, source, force=True, protected=(source,))
    assert source.read_bytes() == original

    hardlink = tmp_path / "source-alias.lib"
    os.link(source, hardlink)
    with pytest.raises(OutputError, match="aliases an input"):
        write_liberty_bundle(conversion, hardlink, force=True, protected=(source,))
    assert source.read_bytes() == original


def test_liberty_writer_revalidates_mutable_bundle_before_publishing(tmp_path: Path) -> None:
    conversion = convert_liberty_nldm(minimal_liberty(), source_name="memory.lib")
    conversion.bundle.points[0].metrics.clear()
    target = tmp_path / "invalid.json"

    with pytest.raises(InputError, match="metrics must be a non-empty object"):
        conversion.canonical_data()
    with pytest.raises(OutputError, match="cannot serialize Liberty bundle"):
        write_liberty_bundle(conversion, target)
    assert not target.exists()


def test_liberty_writer_binds_nested_and_bundle_source_provenance(tmp_path: Path) -> None:
    conversion = convert_liberty_nldm(minimal_liberty(), source_name="memory.lib")
    source = conversion.bundle.run["source"]
    assert isinstance(source, dict)
    source["sha256"] = "0" * 64
    with pytest.raises(InputError, match="provenance does not match"):
        conversion.canonical_data()
    with pytest.raises(OutputError, match="provenance does not match"):
        write_liberty_bundle(conversion, tmp_path / "forged.json")

    fresh = convert_liberty_nldm(minimal_liberty(), source_name="memory.lib")
    mismatched_bundle = replace(fresh.bundle, source_hash="f" * 64)
    mismatched = replace(fresh, bundle=mismatched_bundle)
    with pytest.raises(OutputError, match="source digest does not match"):
        write_liberty_bundle(mismatched, tmp_path / "mismatched.json")


def test_loader_reports_missing_file_and_cli_exit_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(InputError, match="cannot read Liberty"):
        load_liberty_nldm(tmp_path / "missing.lib")

    output = tmp_path / "converted.json"
    assert main(["import-liberty", "--input", str(FIXTURE), "--out", str(output)]) == 0
    captured = capsys.readouterr()
    assert "Converted 33 Liberty NLDM point(s)" in captured.out
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() in captured.out
    assert load_bundle(output).run["contract"] == "regressistor.liberty-nldm/1"

    assert main(["import-liberty", "--input", str(FIXTURE), "--out", str(output)]) == 3
    assert "refusing to overwrite" in capsys.readouterr().err
    assert main(["import-liberty", "--input", str(FIXTURE), "--out", str(output), "--force"]) == 0

    broken = tmp_path / "broken.lib"
    broken.write_text("library(x) {", encoding="utf-8")
    assert main(["import-liberty", "--input", str(broken), "--out", str(tmp_path / "x")]) == 2
    assert "unterminated Liberty group" in capsys.readouterr().err


def test_import_liberty_cli_never_clobbers_its_input_even_when_forced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.lib"
    original = minimal_liberty()
    source.write_bytes(original)
    assert (
        main(
            [
                "import-liberty",
                "--input",
                str(source),
                "--out",
                str(source),
                "--force",
            ]
        )
        == 3
    )
    assert "aliases an input" in capsys.readouterr().err
    assert source.read_bytes() == original


def test_liberty_benchmark_has_stable_non_timing_invariants() -> None:
    benchmark_path = Path(__file__).parents[1] / "benchmarks" / "liberty_nldm_benchmark.py"
    benchmark_run = runpy.run_path(str(benchmark_path))["run"]
    result = benchmark_run(FIXTURE, 2)
    assert result["schema_version"] == 1
    assert result["benchmark"] == "regressistor-liberty-nldm-import-v1"
    assert result["source_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert result["harness_sha256"] == hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    assert result["invariants"] == {"points": 33, "metric_sources": 17}
    assert result["timing_policy"].startswith("Informational only")

    with pytest.raises(ValueError, match="repetitions"):
        benchmark_run(FIXTURE, True)
