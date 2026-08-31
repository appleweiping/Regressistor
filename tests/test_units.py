from __future__ import annotations

import math

import pytest

from regressistor.errors import UnitError
from regressistor.units import convert, parse_unit, validate_unit


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (1000.0, "mV", "V", 1.0),
        (2.5, "MHz", "Hz", 2.5e6),
        (12.0, "V/us", "V/s", 12e6),
        (50.0, "%", "1", 0.5),
        (math.pi, "rad", "deg", 180.0),
        (3.0, "mA/V", "A/V", 0.003),
        (4.0, "", "1", 4.0),
        (7.0, "kohm", "Ohm", 7000.0),
    ],
)
def test_converts_supported_units(value: float, source: str, target: str, expected: float) -> None:
    assert convert(value, source, target) == pytest.approx(expected)


def test_unit_parser_caches_and_canonicalizes_dimension() -> None:
    first = parse_unit("V/A")
    second = parse_unit("V/A")
    assert first is second
    assert first.dimension == (("current", -1), ("voltage", 1))


@pytest.mark.parametrize("unit", ["widgets", "V//s", "/V", "V/", "m%"])
def test_rejects_invalid_unit_expressions(unit: str) -> None:
    with pytest.raises(UnitError):
        parse_unit(unit)


def test_rejects_non_string_unit() -> None:
    with pytest.raises(UnitError, match="string"):
        parse_unit(42)  # type: ignore[arg-type]


def test_rejects_incompatible_dimensions() -> None:
    with pytest.raises(UnitError, match="incompatible"):
        convert(1.0, "V", "A")


def test_rejects_non_finite_conversion() -> None:
    with pytest.raises(UnitError, match="non-finite"):
        convert(float("inf"), "V", "V")


def test_rejects_nonzero_conversion_underflow() -> None:
    source = "*".join(["fV"] * 20)
    target = "*".join(["TV"] * 20)
    with pytest.raises(UnitError, match="underflowed to zero"):
        convert(1.0, source, target)


def test_validate_unit_returns_normalized_dimensionless_spelling() -> None:
    assert validate_unit("   ") == "1"


@pytest.mark.parametrize("unit", ["*".join(["fF"] * 30), "*".join(["THz"] * 30)])
def test_rejects_unit_scale_underflow_and_overflow(unit: str) -> None:
    with pytest.raises(UnitError, match="overflowed or underflowed"):
        parse_unit(unit)
