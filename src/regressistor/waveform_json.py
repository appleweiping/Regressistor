"""Strict lossless wire formats for waveform inputs, policies and reports.

Binary64 values are canonical hexadecimal strings, not JSON numbers. This
avoids silently rounding large integers or underflowing tiny decimal tokens.
Exact report fractions use hexadecimal integer numerators and denominators.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from regressistor._output import atomic_write_bytes
from regressistor._strict_data import load_json_path, load_json_text
from regressistor.waveform import (
    MAX_WAVEFORM_POINTS,
    Waveform,
    WaveformComparison,
    WaveformError,
    WaveformObservation,
    WaveformPolicy,
    _unit_scale,
)

MAX_WAVEFORM_JSON_BYTES = 16 * 1024 * 1024
_WAVE_SCHEMA = "org.regressistor.waveform"
_POLICY_SCHEMA = "org.regressistor.waveform-policy"
_REPORT_SCHEMA = "org.regressistor.waveform-comparison"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
MAX_REPORT_INTEGER_BITS = 32_768


def _canonical(data: object) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _object(value: object, fields: set[str], schema: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise WaveformError(f"{schema} fields do not match the versioned wire contract")
    data = cast(dict[str, object], value)
    if data["schema"] != schema or type(data["version"]) is not int or data["version"] != 1:
        raise WaveformError(f"unsupported {schema} schema/version")
    return data


def _hex_float(value: object) -> float:
    if type(value) is not str or len(value) > 64:
        raise WaveformError("wire samples must be canonical binary64 hexadecimal strings")
    try:
        number = float.fromhex(value)
    except (ValueError, OverflowError) as exc:
        raise WaveformError("invalid hexadecimal binary64 sample") from exc
    if (
        not math.isfinite(number)
        or number.hex() != value
        or (number == 0 and value.startswith("-"))
    ):
        raise WaveformError("wire sample is not a canonical finite binary64 value")
    return number


def waveform_data(waveform: Waveform) -> dict[str, object]:
    if not isinstance(waveform, Waveform):
        raise WaveformError("expected a Waveform")
    return {
        "schema": _WAVE_SCHEMA,
        "version": 1,
        "identity": waveform.identity,
        "name": waveform.name,
        "axis_unit": waveform.axis_unit,
        "value_unit": waveform.value_unit,
        "axis": [value.hex() for value in waveform.axis],
        "values": [value.hex() for value in waveform.values],
    }


def waveform_from_data(value: object) -> Waveform:
    data = _object(
        value,
        {"schema", "version", "identity", "name", "axis_unit", "value_unit", "axis", "values"},
        _WAVE_SCHEMA,
    )
    axis, values = data["axis"], data["values"]
    if type(axis) is not list or type(values) is not list:
        raise WaveformError("wire axis and values must be arrays")
    if not 2 <= len(axis) <= MAX_WAVEFORM_POINTS or len(values) != len(axis):
        raise WaveformError("wire sample counts are unequal or exceed their bounds")
    for field in ("name", "axis_unit", "value_unit", "identity"):
        if type(data[field]) is not str:
            raise WaveformError(f"wire {field} must be text")
    result = Waveform(
        tuple(_hex_float(item) for item in axis),
        tuple(_hex_float(item) for item in values),
        cast(str, data["axis_unit"]),
        cast(str, data["value_unit"]),
        cast(str, data["name"]),
    )
    if data["identity"] != result.identity:
        raise WaveformError("waveform identity does not match the exact input content")
    return result


def _decode(text: str) -> object:
    if type(text) is not str:
        raise WaveformError("waveform JSON must be text")
    return load_json_text(
        text,
        context="waveform JSON",
        max_bytes=MAX_WAVEFORM_JSON_BYTES,
        max_depth=4,
        max_nodes=2 * MAX_WAVEFORM_POINTS + 64,
        max_text_bytes=MAX_WAVEFORM_JSON_BYTES,
    )


def loads_waveform(text: str) -> Waveform:
    return waveform_from_data(_decode(text))


def load_waveform(path: str | Path) -> Waveform:
    _, _, value = load_json_path(
        path,
        context="waveform",
        max_bytes=MAX_WAVEFORM_JSON_BYTES,
        max_depth=4,
        max_nodes=2 * MAX_WAVEFORM_POINTS + 64,
        max_text_bytes=MAX_WAVEFORM_JSON_BYTES,
    )
    return waveform_from_data(value)


def dumps_waveform(waveform: Waveform) -> str:
    return _canonical(waveform_data(waveform)).decode("ascii") + "\n"


def write_waveform(
    waveform: Waveform,
    destination: str | Path,
    *,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> Path:
    return atomic_write_bytes(
        destination,
        dumps_waveform(waveform).encode("ascii"),
        context="waveform",
        force=force,
        protected=protected,
    )


def waveform_policy_data(policy: WaveformPolicy) -> dict[str, object]:
    if not isinstance(policy, WaveformPolicy):
        raise WaveformError("expected a WaveformPolicy")
    body: dict[str, object] = {
        "schema": _POLICY_SCHEMA,
        "version": 1,
        "absolute": policy.absolute.hex(),
        "relative": policy.relative.hex(),
        "relative_floor": policy.relative_floor.hex(),
        "grid": policy.grid,
        "domain": policy.domain,
        "max_breakpoints": policy.max_breakpoints,
        "max_diagnostics": policy.max_diagnostics,
    }
    return {**body, "identity": sha256(_canonical(body)).hexdigest()}


def waveform_policy_from_data(value: object) -> WaveformPolicy:
    data = _object(
        value,
        {
            "schema",
            "version",
            "identity",
            "absolute",
            "relative",
            "relative_floor",
            "grid",
            "domain",
            "max_breakpoints",
            "max_diagnostics",
        },
        _POLICY_SCHEMA,
    )
    if type(data["grid"]) is not str or type(data["domain"]) is not str:
        raise WaveformError("wire grid and domain modes must be text")
    if type(data["max_breakpoints"]) is not int or type(data["max_diagnostics"]) is not int:
        raise WaveformError("wire resource limits must be integers")
    result = WaveformPolicy(
        _hex_float(data["absolute"]),
        _hex_float(data["relative"]),
        _hex_float(data["relative_floor"]),
        cast(Literal["exact", "linear"], data["grid"]),
        cast(Literal["full", "intersection"], data["domain"]),
        data["max_breakpoints"],
        data["max_diagnostics"],
    )
    if data != waveform_policy_data(result):
        raise WaveformError("waveform policy identity does not match its content")
    return result


def load_waveform_policy(path: str | Path) -> WaveformPolicy:
    _, _, value = load_json_path(
        path,
        context="waveform policy",
        max_bytes=8192,
        max_depth=2,
        max_nodes=32,
        max_text_bytes=4096,
    )
    return waveform_policy_from_data(value)


def write_waveform_policy(
    policy: WaveformPolicy,
    destination: str | Path,
    *,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> Path:
    return atomic_write_bytes(
        destination,
        _canonical(waveform_policy_data(policy)) + b"\n",
        context="waveform policy",
        force=force,
        protected=protected,
    )


def _rational(value: Fraction) -> dict[str, str]:
    return {"numerator": hex(value.numerator), "denominator": hex(value.denominator)}


def _observation(value: WaveformObservation) -> dict[str, object]:
    return {
        "axis": _rational(value.axis),
        "baseline": _rational(value.baseline),
        "candidate": _rational(value.candidate),
        "allowed": _rational(value.allowed),
        "deviation": _rational(value.deviation),
        "excess": _rational(value.excess),
    }


def _validate_comparison(result: WaveformComparison) -> None:
    """Check intrinsic DTO consistency, not authenticity or trace replay."""
    if type(result) is not WaveformComparison or type(result.policy) is not WaveformPolicy:
        raise WaveformError("expected a typed WaveformComparison with a WaveformPolicy")
    for value in (result.baseline_id, result.candidate_id, result.observations_sha256):
        if type(value) is not str or _DIGEST.fullmatch(value) is None:
            raise WaveformError("comparison identities must be lowercase SHA-256 digests")
    _unit_scale(result.axis_unit)
    _unit_scale(result.value_unit)

    def fraction(value: object) -> Fraction:
        if type(value) is not Fraction:
            raise WaveformError("comparison arithmetic fields must be exact Fractions")
        if (
            max(value.numerator.bit_length(), value.denominator.bit_length())
            > MAX_REPORT_INTEGER_BITS
        ):
            raise WaveformError("comparison fraction exceeds the integer-bit budget")
        return value

    def pair(value: object) -> tuple[Fraction, Fraction]:
        if type(value) is not tuple or len(value) != 2:
            raise WaveformError("comparison domains/exclusions must be immutable pairs")
        return fraction(value[0]), fraction(value[1])

    left, right = pair(result.domain)
    if left >= right:
        raise WaveformError("comparison domain must have positive width")
    tails = (*pair(result.excluded_baseline), *pair(result.excluded_candidate))
    if any(item < 0 for item in tails):
        raise WaveformError("excluded domain lengths must be nonnegative")
    if (tails[0] > 0 and tails[2] > 0) or (tails[1] > 0 and tails[3] > 0):
        raise WaveformError("intersection cannot exclude both traces at the same endpoint")
    if (result.policy.domain == "full" or result.policy.grid == "exact") and any(tails):
        raise WaveformError("complete/exact policy cannot exclude domain tails")
    count, failures = result.evaluated_breakpoints, result.failing_breakpoints
    if type(count) is not int or not 2 <= count <= result.policy.max_breakpoints:
        raise WaveformError("evaluated breakpoint count is outside the policy budget")
    if type(failures) is not int or not 0 <= failures <= count:
        raise WaveformError("failing breakpoint count is inconsistent")
    if type(result.diagnostics) is not tuple or len(result.diagnostics) != min(
        failures, result.policy.max_diagnostics
    ):
        raise WaveformError("diagnostic count does not match the bounded failure sample")
    absolute, relative, floor = map(
        Fraction, (result.policy.absolute, result.policy.relative, result.policy.relative_floor)
    )
    witnesses: dict[Fraction, WaveformObservation] = {}

    def observation(value: object) -> WaveformObservation:
        if type(value) is not WaveformObservation:
            raise WaveformError("comparison observations have an invalid type")
        x, b, _c, allowed = (
            fraction(value.axis),
            fraction(value.baseline),
            fraction(value.candidate),
            fraction(value.allowed),
        )
        if not left <= x <= right or allowed != absolute + relative * max(abs(b), floor):
            raise WaveformError("observation axis or allowed error conflicts with the policy")
        if x in witnesses and witnesses[x] != value:
            raise WaveformError("observations at the same axis are inconsistent")
        witnesses[x] = value
        return value

    maximum_error = observation(result.maximum_deviation)
    maximum_excess = observation(result.maximum_excess)
    if (maximum_excess.excess > 0) != (failures > 0):
        raise WaveformError("maximum excess conflicts with the failure count")
    if (
        maximum_error.deviation < maximum_excess.deviation
        or maximum_excess.excess < maximum_error.excess
    ):
        raise WaveformError("comparison maxima are mutually inconsistent")
    previous: Fraction | None = None
    for diagnostic in result.diagnostics:
        item = observation(diagnostic)
        if (
            item.excess <= 0
            or item.excess > maximum_excess.excess
            or item.deviation > maximum_error.deviation
            or (previous is not None and item.axis <= previous)
        ):
            raise WaveformError("diagnostic order/value conflicts with the comparison maxima")
        previous = item.axis
    if len(witnesses) > count or sum(item.excess > 0 for item in witnesses.values()) > failures:
        raise WaveformError("distinct observation witnesses exceed the reported breakpoint counts")


def waveform_comparison_data(result: WaveformComparison) -> dict[str, object]:
    _validate_comparison(result)
    body: dict[str, object] = {
        "schema": _REPORT_SCHEMA,
        "version": 1,
        "baseline_id": result.baseline_id,
        "candidate_id": result.candidate_id,
        "policy": waveform_policy_data(result.policy),
        "scope": "samples" if result.policy.grid == "exact" else "continuous_piecewise_linear",
        "status": result.status,
        "passed": result.passed,
        "full_domain_covered": result.full_domain_covered,
        "axis_unit": result.axis_unit,
        "value_unit": result.value_unit,
        "domain": [_rational(item) for item in result.domain],
        "excluded_baseline": [_rational(item) for item in result.excluded_baseline],
        "excluded_candidate": [_rational(item) for item in result.excluded_candidate],
        "evaluated_breakpoints": result.evaluated_breakpoints,
        "failing_breakpoints": result.failing_breakpoints,
        "maximum_deviation": _observation(result.maximum_deviation),
        "maximum_excess": _observation(result.maximum_excess),
        "diagnostics": [_observation(item) for item in result.diagnostics],
        "diagnostics_truncated": result.failing_breakpoints > len(result.diagnostics),
        "observations_sha256": result.observations_sha256,
    }
    return {**body, "identity": sha256(_canonical(body)).hexdigest()}


def write_waveform_comparison(
    result: WaveformComparison,
    destination: str | Path,
    *,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> Path:
    payload = _canonical(waveform_comparison_data(result)) + b"\n"
    return atomic_write_bytes(
        destination, payload, context="waveform comparison", force=force, protected=protected
    )
