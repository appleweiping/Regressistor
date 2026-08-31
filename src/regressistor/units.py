"""Small, explicit unit system for scalar analog measurements."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from regressistor.errors import UnitError


@dataclass(frozen=True, slots=True)
class UnitSpec:
    dimension: tuple[tuple[str, int], ...]
    scale: float


_BASE_UNITS: dict[str, tuple[str | None, float]] = {
    "1": (None, 1.0),
    "%": (None, 0.01),
    "V": ("voltage", 1.0),
    "A": ("current", 1.0),
    "s": ("time", 1.0),
    "Hz": ("frequency", 1.0),
    "F": ("capacitance", 1.0),
    "H": ("inductance", 1.0),
    "W": ("power", 1.0),
    "Ohm": ("resistance", 1.0),
    "ohm": ("resistance", 1.0),
    "deg": ("angle", 1.0),
    "rad": ("angle", 180.0 / math.pi),
    "dB": ("decibel", 1.0),
}

_PREFIXES: tuple[tuple[str, float], ...] = (
    ("f", 1e-15),
    ("p", 1e-12),
    ("n", 1e-9),
    ("u", 1e-6),
    ("µ", 1e-6),
    ("μ", 1e-6),
    ("m", 1e-3),
    ("k", 1e3),
    ("M", 1e6),
    ("G", 1e9),
    ("T", 1e12),
)


def _token(token: str) -> tuple[str | None, float]:
    if token in _BASE_UNITS:
        return _BASE_UNITS[token]
    for prefix, factor in _PREFIXES:
        if token.startswith(prefix) and token[len(prefix) :] in _BASE_UNITS:
            dimension, base_factor = _BASE_UNITS[token[len(prefix) :]]
            if dimension is None:
                break
            return dimension, factor * base_factor
    raise UnitError(f"unsupported unit token: {token!r}")


def _side(text: str, sign: int, dimensions: Counter[str]) -> float:
    if not text:
        raise UnitError("unit expression contains an empty side")
    scale = 1.0
    for raw_token in text.split("*"):
        token = raw_token.strip()
        dimension, factor = _token(token)
        scale *= factor**sign
        if not math.isfinite(scale) or scale <= 0.0:
            raise UnitError("unit scale overflowed or underflowed")
        if dimension is not None:
            dimensions[dimension] += sign
    return scale


@lru_cache(maxsize=256)
def parse_unit(unit: str) -> UnitSpec:
    """Parse a deliberately small unit grammar with at most one division."""
    if not isinstance(unit, str):
        raise UnitError("unit must be a string")
    expression = unit.strip() or "1"
    if expression.count("/") > 1:
        raise UnitError(f"unit may contain at most one '/': {unit!r}")

    numerator, separator, denominator = expression.partition("/")
    dimensions: Counter[str] = Counter()
    scale = _side(numerator, 1, dimensions)
    if separator:
        scale *= _side(denominator, -1, dimensions)
    if not math.isfinite(scale) or scale <= 0.0:
        raise UnitError("unit scale overflowed or underflowed")

    canonical_dimension = tuple(sorted((key, value) for key, value in dimensions.items() if value))
    return UnitSpec(canonical_dimension, scale)


def convert(value: float, source: str, target: str) -> float:
    """Convert between units that have identical dimensions."""
    source_spec = parse_unit(source)
    target_spec = parse_unit(target)
    if source_spec.dimension != target_spec.dimension:
        raise UnitError(
            f"incompatible units {source!r} and {target!r}: "
            f"{source_spec.dimension!r} != {target_spec.dimension!r}"
        )
    converted = value * source_spec.scale / target_spec.scale
    if not math.isfinite(converted):
        raise UnitError("unit conversion produced a non-finite value")
    if value != 0.0 and converted == 0.0:
        raise UnitError("unit conversion underflowed to zero")
    return converted


def validate_unit(unit: str) -> str:
    """Validate a unit and return its stripped spelling."""
    parse_unit(unit)
    return unit.strip() or "1"
