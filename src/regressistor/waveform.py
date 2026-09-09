"""Exact, bounded comparison of finite scalar piecewise-linear trajectories.

The arithmetic profile compares the exact binary values supplied by the caller,
with exact decimal SI-prefix conversion. It does not smooth, extrapolate, unwrap
phase, align events, or silently drop unmatched domain tails.
"""

from __future__ import annotations

import math
import struct
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from typing import Literal, Protocol, cast

from regressistor.errors import InputError, UnitError
from regressistor.units import parse_unit

MAX_WAVEFORM_POINTS = 100_000
MAX_COMPARISON_BREAKPOINTS = 400_000
MAX_DIAGNOSTICS = 64


class WaveformError(InputError):
    """A waveform or comparison policy is outside the declared profile."""


class WaveformDomainError(WaveformError):
    """A complete comparison cannot be made without changing the domain/grid."""


class WaveformBudgetError(WaveformError):
    """The preflight or actual comparison work would exceed its budget."""


def _text(value: object, label: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise WaveformError(f"{label} must be nonempty text of at most {maximum} characters")
    if unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value
    ):
        raise WaveformError(f"{label} must be NFC text without controls")
    return value


def _number(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise WaveformError(f"{label} must be a finite binary64 number")
    number = cast(int | float, value)
    if type(number) is int and abs(number) > 2**53:
        raise WaveformError(f"{label} integer exceeds the exact binary64 integer range")
    result = float(number)
    if not math.isfinite(result):
        raise WaveformError(f"{label} must be finite")
    return 0.0 if result == 0 else result


_BASES = frozenset({"1", "V", "A", "s", "Hz", "F", "H", "W", "J", "Ohm", "ohm", "deg", "dB"})
_PREFIXES = {
    "f": -15,
    "p": -12,
    "n": -9,
    "u": -6,
    "µ": -6,
    "μ": -6,
    "m": -3,
    "k": 3,
    "M": 6,
    "G": 9,
    "T": 12,
}


def _unit_scale(unit: str) -> Fraction:
    """Restrict the shared unit grammar to exact linear SI scaling."""
    _text(unit, "unit", 128)
    parse_unit(unit)
    sides = unit.strip().split("/")
    tokens = [
        (token.strip(), 1 if index == 0 else -1)
        for index, side in enumerate(sides)
        for token in side.split("*")
    ]
    if len(tokens) > 16:
        raise WaveformError("waveform unit exceeds the 16-factor budget")
    result = Fraction(1)
    for factor_text, sign in tokens:
        if factor_text in _BASES:
            factor = Fraction(1)
        elif factor_text == "%":
            factor = Fraction(1, 100)
        elif factor_text[:1] in _PREFIXES and factor_text[1:] in _BASES - {"1"}:
            factor = Fraction(10) ** _PREFIXES[factor_text[0]]
        else:
            raise UnitError("waveforms require exact linear SI units; radians are not converted")
        result *= factor**sign
    return result


def _conversion(source: str, target: str) -> Fraction:
    if parse_unit(source).dimension != parse_unit(target).dimension:
        raise UnitError(f"incompatible waveform units {source!r} and {target!r}")
    return _unit_scale(source) / _unit_scale(target)


@dataclass(frozen=True, slots=True)
class Waveform:
    """A continuous scalar trace, with immutable, strictly increasing samples."""

    axis: tuple[float, ...]
    values: tuple[float, ...]
    axis_unit: str
    value_unit: str
    name: str = "trace"

    def __post_init__(self) -> None:
        if type(self.axis) is not tuple or type(self.values) is not tuple:
            raise WaveformError("axis and values must be immutable tuples")
        if not 2 <= len(self.axis) <= MAX_WAVEFORM_POINTS or len(self.values) != len(self.axis):
            raise WaveformError(f"waveforms require 2..{MAX_WAVEFORM_POINTS} matched samples")
        _text(self.name, "name", 256)
        _unit_scale(self.axis_unit)
        _unit_scale(self.value_unit)
        axis = tuple(_number(value, "axis sample") for value in self.axis)
        if any(right <= left for left, right in zip(axis, axis[1:], strict=False)):
            raise WaveformError(
                "axis must be strictly increasing; repeated/event samples are unsupported"
            )
        values = tuple(_number(value, "value sample") for value in self.values)
        object.__setattr__(self, "axis", axis)
        object.__setattr__(self, "values", values)

    @property
    def identity(self) -> str:
        digest = sha256(b"org.regressistor.waveform\x00v1\x00")
        for value in (self.name, self.axis_unit, self.value_unit):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        digest.update(len(self.axis).to_bytes(4, "big"))
        for x, y in zip(self.axis, self.values, strict=True):
            digest.update(struct.pack(">dd", x, y))
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class WaveformPolicy:
    """Tolerance is in baseline value units, relative to the baseline only."""

    absolute: float = 0.0
    relative: float = 0.0
    relative_floor: float = 0.0
    grid: Literal["exact", "linear"] = "exact"
    domain: Literal["full", "intersection"] = "full"
    max_breakpoints: int = MAX_COMPARISON_BREAKPOINTS
    max_diagnostics: int = 16

    def __post_init__(self) -> None:
        for name in ("absolute", "relative", "relative_floor"):
            value = _number(getattr(self, name), name)
            if value < 0:
                raise WaveformError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        if (
            type(self.grid) is not str
            or type(self.domain) is not str
            or self.grid not in ("exact", "linear")
            or self.domain not in ("full", "intersection")
        ):
            raise WaveformError("unsupported grid or domain policy")
        for name, maximum in (
            ("max_breakpoints", MAX_COMPARISON_BREAKPOINTS),
            ("max_diagnostics", MAX_DIAGNOSTICS),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise WaveformError(f"{name} must be an integer in [1, {maximum}]")


@dataclass(frozen=True, slots=True)
class WaveformObservation:
    axis: Fraction
    baseline: Fraction
    candidate: Fraction
    allowed: Fraction

    @property
    def deviation(self) -> Fraction:
        return abs(self.candidate - self.baseline)

    @property
    def excess(self) -> Fraction:
        return self.deviation - self.allowed


@dataclass(frozen=True, slots=True)
class WaveformComparison:
    baseline_id: str
    candidate_id: str
    policy: WaveformPolicy
    axis_unit: str
    value_unit: str
    domain: tuple[Fraction, Fraction]
    excluded_baseline: tuple[Fraction, Fraction]
    excluded_candidate: tuple[Fraction, Fraction]
    evaluated_breakpoints: int
    failing_breakpoints: int
    maximum_deviation: WaveformObservation
    maximum_excess: WaveformObservation
    diagnostics: tuple[WaveformObservation, ...]
    observations_sha256: str

    @property
    def full_domain_covered(self) -> bool:
        return not any((*self.excluded_baseline, *self.excluded_candidate))

    @property
    def status(self) -> Literal["pass", "fail", "partial_pass", "partial_fail"]:
        if self.full_domain_covered:
            return "fail" if self.failing_breakpoints else "pass"
        return "partial_fail" if self.failing_breakpoints else "partial_pass"

    @property
    def passed(self) -> bool:
        """True only for a successful, complete-domain comparison."""
        return self.status == "pass"


class _Cursor:
    """Monotone interpolation cursor; never materializes a second trace."""

    def __init__(self, waveform: Waveform, axis_scale: Fraction, value_scale: Fraction):
        self.waveform = waveform
        self.axis_scale = axis_scale
        self.value_scale = value_scale
        self.index = 0

    def x(self, index: int) -> Fraction:
        return Fraction(self.waveform.axis[index]) * self.axis_scale

    def y(self, index: int) -> Fraction:
        return Fraction(self.waveform.values[index]) * self.value_scale

    def at(self, x: Fraction) -> Fraction:
        while self.index + 1 < len(self.waveform.axis) - 1 and self.x(self.index + 1) < x:
            self.index += 1
        left, right = self.x(self.index), self.x(self.index + 1)
        if not left <= x <= right:
            raise WaveformDomainError("extrapolation is forbidden")
        low, high = self.y(self.index), self.y(self.index + 1)
        return low + (high - low) * (x - left) / (right - left)


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


def _hash_fraction(digest: _Digest, value: Fraction) -> None:
    for integer in (value.numerator, value.denominator):
        # Binary framing avoids Python's decimal-integer rendering limit.
        size = max(1, (integer.bit_length() + 8) // 8)
        data = integer.to_bytes(size, "big", signed=True)
        digest.update(len(data).to_bytes(4, "big"))
        digest.update(data)


def compare_waveforms(
    baseline: Waveform, candidate: Waveform, policy: WaveformPolicy | None = None
) -> WaveformComparison:
    """Compare all samples, or every necessary continuous-linear breakpoint."""
    if not isinstance(baseline, Waveform) or not isinstance(candidate, Waveform):
        raise WaveformError("baseline and candidate must be Waveform objects")
    if policy is None:
        policy = WaveformPolicy()
    if not isinstance(policy, WaveformPolicy):
        raise WaveformError("policy must be a WaveformPolicy")
    b = _Cursor(baseline, Fraction(1), Fraction(1))
    c = _Cursor(
        candidate,
        _conversion(candidate.axis_unit, baseline.axis_unit),
        _conversion(candidate.value_unit, baseline.value_unit),
    )
    start, end = max(b.x(0), c.x(0)), min(b.x(-1), c.x(-1))
    if start >= end:
        raise WaveformDomainError("waveforms must have a nonempty interval in common")
    excluded_b = (start - b.x(0), b.x(-1) - end)
    excluded_c = (start - c.x(0), c.x(-1) - end)
    if policy.domain == "full" and any((*excluded_b, *excluded_c)):
        raise WaveformDomainError("complete domain endpoints differ; extrapolation is forbidden")
    upper_bound = (
        len(baseline.axis)
        if policy.grid == "exact"
        else 3 * len(baseline.axis) + len(candidate.axis)
    )
    if upper_bound > policy.max_breakpoints:
        raise WaveformBudgetError("worst-case breakpoint count exceeds the preflight budget")
    if policy.grid == "exact" and (
        len(baseline.axis) != len(candidate.axis)
        or any(b.x(index) != c.x(index) for index in range(len(baseline.axis)))
    ):
        raise WaveformDomainError("exact-grid comparison requires identical converted grids")
    absolute, relative, floor = map(
        Fraction, (policy.absolute, policy.relative, policy.relative_floor)
    )
    digest = sha256(b"org.regressistor.waveform-observations\x00v1\x00")
    diagnostics: list[WaveformObservation] = []
    count = failures = 0
    maximum_error: WaveformObservation | None = None
    maximum_excess: WaveformObservation | None = None

    def observe(x: Fraction) -> None:
        nonlocal count, failures, maximum_error, maximum_excess
        bv, cv = b.at(x), c.at(x)
        observation = WaveformObservation(x, bv, cv, absolute + relative * max(abs(bv), floor))
        count += 1
        if count > policy.max_breakpoints:
            raise WaveformBudgetError("actual breakpoint count exceeds the budget")
        for value in (x, bv, cv, observation.allowed):
            _hash_fraction(digest, value)
        if maximum_error is None or observation.deviation > maximum_error.deviation:
            maximum_error = observation
        if maximum_excess is None or observation.excess > maximum_excess.excess:
            maximum_excess = observation
        if observation.excess > 0:
            failures += 1
            if len(diagnostics) < policy.max_diagnostics:
                diagnostics.append(observation)

    if policy.grid == "exact":
        for index in range(len(baseline.axis)):
            observe(b.x(index))
    else:
        x = start
        bi = ci = 0
        observe(x)
        while x < end:
            while bi < len(baseline.axis) - 1 and b.x(bi) <= x:
                bi += 1
            while ci < len(candidate.axis) - 1 and c.x(ci) <= x:
                ci += 1
            right = min(b.x(bi), c.x(ci), end)
            low, high = b.at(x), b.at(right)
            crossings: set[Fraction] = set()
            if low != high and relative:
                for threshold in (-floor, floor):
                    if min(low, high) < threshold < max(low, high):
                        crossings.add(x + (right - x) * (threshold - low) / (high - low))
            for crossing in sorted(crossings):
                observe(crossing)
            observe(right)
            x = right
    if maximum_error is None or maximum_excess is None:
        raise WaveformError("comparison evaluated no breakpoints")
    return WaveformComparison(
        baseline.identity,
        candidate.identity,
        policy,
        baseline.axis_unit,
        baseline.value_unit,
        (start, end),
        excluded_b,
        excluded_c,
        count,
        failures,
        maximum_error,
        maximum_excess,
        tuple(diagnostics),
        digest.hexdigest(),
    )
