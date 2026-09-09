"""Independent adversarial checks for the waveform comparison kernel."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

import regressistor.waveform as waveform_module
from regressistor.waveform import (
    Waveform,
    WaveformBudgetError,
    WaveformError,
    WaveformPolicy,
    compare_waveforms,
)
from regressistor.waveform_json import waveform_comparison_data


class _OversizedIntSubclass(int):
    """Exercise the boundary without relying on a third-party numeric type."""


def _interpolate(
    axis: tuple[Fraction, ...], values: tuple[Fraction, ...], point: Fraction
) -> Fraction:
    """Independent barycentric interpolation oracle."""
    for left, right, low, high in zip(axis, axis[1:], values, values[1:], strict=True):
        if left <= point <= right:
            return ((right - point) * low + (point - left) * high) / (right - left)
    raise AssertionError("oracle was asked to extrapolate")


def _oracle_breakpoints(
    baseline_axis: tuple[Fraction, ...],
    baseline_values: tuple[Fraction, ...],
    candidate_axis: tuple[Fraction, ...],
    floor: Fraction,
) -> tuple[Fraction, ...]:
    """Union knots plus both baseline floor crossings, without production helpers."""
    points = set(baseline_axis) | set(candidate_axis)
    for left, right, low, high in zip(
        baseline_axis[:-1],
        baseline_axis[1:],
        baseline_values[:-1],
        baseline_values[1:],
        strict=True,
    ):
        if low == high:
            continue
        for threshold in {-floor, floor}:
            position = left + (right - left) * (threshold - low) / (high - low)
            if left < position < right:
                points.add(position)
    return tuple(sorted(points))


def test_exact_grid_budget_is_checked_before_proportional_grid_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny work budget must fail before inspecting every grid point."""
    trace = Waveform(
        axis=tuple(float(index) for index in range(128)),
        values=tuple(0.0 for _ in range(128)),
        axis_unit="s",
        value_unit="V",
    )
    original_x = waveform_module._Cursor.x

    def endpoint_only_x(cursor: object, index: int):
        if index not in (0, -1):
            pytest.fail("exact-grid scan began before the work-budget preflight")
        return original_x(cursor, index)  # type: ignore[arg-type]

    monkeypatch.setattr(waveform_module._Cursor, "x", endpoint_only_x)

    with pytest.raises(WaveformBudgetError, match="preflight budget"):
        compare_waveforms(trace, trace, WaveformPolicy(max_breakpoints=1))


def test_integer_subclass_cannot_bypass_exact_binary64_range_check() -> None:
    with pytest.raises(WaveformError, match="finite binary64|exact binary64"):
        Waveform(
            axis=(0.0, 1.0),
            values=(_OversizedIntSubclass(2**53 + 1), 0.0),
            axis_unit="s",
            value_unit="V",
        )


def test_linear_preflight_precedes_any_interpolation(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = Waveform((0.0, 1.0), (0.0, 1.0), "s", "V")
    candidate = Waveform((0.0, 0.5, 1.0), (0.0, 0.5, 1.0), "s", "V")

    def forbidden_interpolation(*_args: object, **_kwargs: object) -> Fraction:
        pytest.fail("interpolation began before the work-budget preflight")

    monkeypatch.setattr(waveform_module._Cursor, "at", forbidden_interpolation)
    with pytest.raises(WaveformBudgetError, match="preflight budget"):
        compare_waveforms(
            baseline,
            candidate,
            WaveformPolicy(grid="linear", max_breakpoints=1),
        )


def test_waveform_size_preflight_precedes_sample_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = tuple(0.0 for _ in range(waveform_module.MAX_WAVEFORM_POINTS + 1))

    def forbidden_normalization(*_args: object, **_kwargs: object) -> float:
        pytest.fail("sample normalization began before the size preflight")

    monkeypatch.setattr(waveform_module, "_number", forbidden_normalization)
    with pytest.raises(WaveformError, match="matched samples"):
        Waveform(oversized, oversized, "s", "V")


def test_both_relative_floor_crossings_match_independent_nonuniform_oracle() -> None:
    bx = tuple(map(Fraction, (0, 1, 4, 10)))
    by = tuple(map(Fraction, (-4, 4, -4, 4)))
    cx = tuple(map(Fraction, (0, 2, 7, 10)))
    cy = tuple(map(Fraction, (-3, 5, -5, 5)))
    absolute, relative, floor = Fraction(1, 4), Fraction(3, 8), Fraction(3, 2)
    points = _oracle_breakpoints(bx, by, cx, floor)
    expected = []
    for point in points:
        baseline_value = _interpolate(bx, by, point)
        candidate_value = _interpolate(cx, cy, point)
        deviation = abs(candidate_value - baseline_value)
        allowed = absolute + relative * max(abs(baseline_value), floor)
        expected.append((point, deviation, deviation - allowed))

    actual = compare_waveforms(
        Waveform(tuple(map(float, bx)), tuple(map(float, by)), "s", "V"),
        Waveform(tuple(map(float, cx)), tuple(map(float, cy)), "s", "V"),
        WaveformPolicy(
            absolute=float(absolute),
            relative=float(relative),
            relative_floor=float(floor),
            grid="linear",
        ),
    )

    assert actual.evaluated_breakpoints == len(points)
    assert actual.failing_breakpoints == sum(excess > 0 for _, _, excess in expected)
    assert actual.maximum_deviation.deviation == max(row[1] for row in expected)
    assert actual.maximum_excess.excess == max(row[2] for row in expected)
    assert actual.maximum_excess.axis == next(
        point for point, _, excess in expected if excess == actual.maximum_excess.excess
    )

    # Convexity proof sampled independently inside every proof interval.
    for left, right in zip(points[:-1], points[1:], strict=True):
        for weight in (Fraction(1, 11), Fraction(1, 2), Fraction(10, 11)):
            point = (1 - weight) * left + weight * right
            baseline_value = _interpolate(bx, by, point)
            candidate_value = _interpolate(cx, cy, point)
            excess = (
                abs(candidate_value - baseline_value)
                - absolute
                - relative * max(abs(baseline_value), floor)
            )
            assert excess <= actual.maximum_excess.excess


def test_extreme_exact_si_conversions_preserve_grid_and_values() -> None:
    baseline = Waveform((0, 1, 2), (0, 10**12, 2 * 10**12), "s", "V")
    candidate = Waveform(
        (0, 10**15, 2 * 10**15),
        (0, 1, 2),
        "fs",
        "TV",
    )
    result = compare_waveforms(baseline, candidate)
    assert result.passed
    assert result.maximum_deviation.deviation == 0
    assert result.domain == (0, 2)


def test_all_supported_micro_spellings_have_the_same_exact_scale() -> None:
    ascii_micro = Waveform((0, 1), (1, 2), "us", "uV")
    micro_sign = Waveform((0, 1), (1, 2), "µs", "µV")
    greek_mu = Waveform((0, 1), (1, 2), "μs", "μV")
    assert compare_waveforms(ascii_micro, micro_sign).passed
    assert compare_waveforms(ascii_micro, greek_mu).passed


def test_converted_intersection_reports_exact_tails_and_never_passes() -> None:
    baseline = Waveform((0, 4), (0, 4), "s", "V")
    candidate = Waveform((1000, 3000), (1000, 3000), "ms", "mV")
    result = compare_waveforms(
        baseline,
        candidate,
        WaveformPolicy(grid="linear", domain="intersection"),
    )
    assert result.domain == (1, 3)
    assert result.excluded_baseline == (1, 1)
    assert result.excluded_candidate == (0, 0)
    assert result.status == "partial_pass"
    assert not result.passed


def test_diagnostic_cap_does_not_truncate_counts_hash_or_late_samples() -> None:
    axis = tuple(float(index) for index in range(80))
    baseline = Waveform(axis, (0.0,) * 80, "s", "V")
    candidate = Waveform(axis, (1.0,) * 80, "s", "V")
    one = compare_waveforms(baseline, candidate, WaveformPolicy(max_diagnostics=1))
    all_available = compare_waveforms(
        baseline,
        candidate,
        WaveformPolicy(max_diagnostics=waveform_module.MAX_DIAGNOSTICS),
    )
    late_change = compare_waveforms(
        baseline,
        replace(candidate, values=(*candidate.values[:-1], 2.0)),
        WaveformPolicy(max_diagnostics=1),
    )

    assert one.failing_breakpoints == all_available.failing_breakpoints == 80
    assert len(one.diagnostics) == 1
    assert len(all_available.diagnostics) == waveform_module.MAX_DIAGNOSTICS
    assert one.observations_sha256 == all_available.observations_sha256
    assert late_change.failing_breakpoints == one.failing_breakpoints
    assert late_change.observations_sha256 != one.observations_sha256
    assert late_change.maximum_deviation.axis == 79


@pytest.mark.parametrize(
    ("baseline", "candidate", "policy"),
    [
        (None, Waveform((0, 1), (0, 1), "s", "V"), None),
        (Waveform((0, 1), (0, 1), "s", "V"), object(), None),
        (Waveform((0, 1), (0, 1), "s", "V"), Waveform((0, 1), (0, 1), "s", "V"), object()),
    ],
)
def test_compare_rejects_typed_misuse(baseline: object, candidate: object, policy: object) -> None:
    with pytest.raises(WaveformError):
        compare_waveforms(baseline, candidate, policy)  # type: ignore[arg-type]


def _failing_comparison(max_diagnostics: int = 3):
    baseline = Waveform((0, 1, 2), (0, 0, 0), "s", "V")
    candidate = Waveform((0, 1, 2), (1, 2, 3), "s", "V")
    return compare_waveforms(
        baseline,
        candidate,
        WaveformPolicy(max_diagnostics=max_diagnostics),
    )


@pytest.mark.parametrize(
    "result",
    [
        replace(_failing_comparison(), evaluated_breakpoints=-1),
        replace(_failing_comparison(), failing_breakpoints=-1),
        replace(_failing_comparison(), failing_breakpoints=4),
    ],
)
def test_comparison_wire_rejects_impossible_counts(result: object) -> None:
    with pytest.raises(WaveformError, match="count|breakpoint"):
        waveform_comparison_data(result)  # type: ignore[arg-type]


def test_comparison_wire_rejects_diagnostics_beyond_policy_cap() -> None:
    result = _failing_comparison(max_diagnostics=1)
    forged = replace(result, diagnostics=result.diagnostics * 2)
    with pytest.raises(WaveformError, match="diagnostic"):
        waveform_comparison_data(forged)


def test_comparison_wire_rejects_allowed_value_inconsistent_with_policy() -> None:
    result = _failing_comparison()
    forged_observation = replace(
        result.maximum_excess,
        allowed=result.maximum_excess.allowed + 1,
    )
    forged = replace(result, maximum_excess=forged_observation)
    with pytest.raises(WaveformError, match="allowed|policy|observation"):
        waveform_comparison_data(forged)
