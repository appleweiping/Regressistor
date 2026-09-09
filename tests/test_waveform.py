from dataclasses import FrozenInstanceError, replace
from fractions import Fraction

import pytest

from regressistor.errors import UnitError
from regressistor.waveform import (
    Waveform,
    WaveformBudgetError,
    WaveformDomainError,
    WaveformError,
    WaveformPolicy,
    compare_waveforms,
)


def trace(x=(0.0, 1.0), y=(0.0, 1.0), *, xu="s", yu="V"):
    return Waveform(x, y, xu, yu)


def test_exact_grid_pass_and_original_inputs_are_immutable() -> None:
    baseline = trace()
    result = compare_waveforms(baseline, baseline)
    assert result.passed and result.status == "pass"
    assert result.evaluated_breakpoints == 2
    assert result.failing_breakpoints == 0
    assert result.maximum_deviation.deviation == 0
    assert result.maximum_excess.axis == 0
    assert result.baseline_id == baseline.identity
    with pytest.raises(FrozenInstanceError):
        baseline.axis = (1.0, 2.0)


def test_relative_only_endpoints_do_not_hide_zero_crossing_failure() -> None:
    baseline = trace(y=(-1.0, 1.0))
    candidate = trace(y=(-1.125, 0.875))
    discrete = compare_waveforms(baseline, candidate, WaveformPolicy(relative=0.125))
    continuous = compare_waveforms(
        baseline, candidate, WaveformPolicy(relative=0.125, grid="linear")
    )
    assert discrete.passed
    assert not continuous.passed and continuous.status == "fail"
    assert continuous.evaluated_breakpoints == 3
    assert continuous.maximum_excess.axis == Fraction(1, 2)
    assert continuous.maximum_excess.excess == Fraction(1, 8)


def test_nonzero_floor_crossings_are_exact_and_do_not_round() -> None:
    result = compare_waveforms(
        trace(y=(-2.0, 2.0)),
        trace(y=(-2.5, 1.5)),
        WaveformPolicy(relative=0.5, relative_floor=0.25, grid="linear"),
    )
    assert result.evaluated_breakpoints == 4
    assert result.maximum_excess.axis == Fraction(7, 16)
    assert result.maximum_excess.excess == Fraction(3, 8)
    assert result.failing_breakpoints == 2


def test_mixed_unit_scaling_is_decimal_exact() -> None:
    result = compare_waveforms(trace(), trace((0, 1000), (0, 1000), xu="ms", yu="mV"))
    assert result.passed
    assert result.maximum_deviation.deviation == 0


def test_intersection_is_never_misreported_as_a_full_pass() -> None:
    baseline, candidate = trace((0, 2), (0, 2)), trace((1, 3), (1, 3))
    with pytest.raises(WaveformDomainError, match="endpoints"):
        compare_waveforms(baseline, candidate, WaveformPolicy(grid="linear"))
    result = compare_waveforms(
        baseline, candidate, WaveformPolicy(grid="linear", domain="intersection")
    )
    assert result.domain == (1, 2)
    assert result.excluded_baseline == (1, 0)
    assert result.excluded_candidate == (0, 1)
    assert result.status == "partial_pass" and not result.passed
    failure = compare_waveforms(
        baseline,
        replace(candidate, values=(2, 4)),
        WaveformPolicy(grid="linear", domain="intersection"),
    )
    assert failure.status == "partial_fail" and not failure.passed


def test_extreme_finite_endpoint_width_never_overflows() -> None:
    baseline = trace((-1e308, 1e308), (-1e308, 1e308))
    candidate = trace((-1e308, 0, 1e308), (-1e308, 0, 1e308))
    result = compare_waveforms(baseline, candidate, WaveformPolicy(grid="linear"))
    assert result.passed and result.maximum_deviation.deviation == 0


def test_truncated_diagnostics_preserve_all_failures_and_observation_hash() -> None:
    baseline = trace((0, 1, 2, 3, 4), (0, 0, 0, 0, 0))
    candidate = replace(baseline, values=(1, 2, 3, 4, 5))
    small = compare_waveforms(baseline, candidate, WaveformPolicy(max_diagnostics=1))
    full = compare_waveforms(baseline, candidate)
    assert small.failing_breakpoints == full.failing_breakpoints == 5
    assert small.maximum_deviation.axis == 4
    assert len(small.diagnostics) == 1
    assert small.observations_sha256 == full.observations_sha256


@pytest.mark.parametrize("values", [(0, float("nan")), (0, float("inf")), (0, True), (0, 2**100)])
def test_invalid_samples_fail_closed(values) -> None:
    with pytest.raises(WaveformError):
        trace(y=values)


@pytest.mark.parametrize("x", [(0, 0), (1, 0), (0,), [0, 1], (0, 1, 2)])
def test_invalid_grid_and_shape_fail_closed(x) -> None:
    with pytest.raises(WaveformError):
        trace(x)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"absolute": -1},
        {"relative": True},
        {"relative_floor": float("inf")},
        {"grid": "spline"},
        {"domain": "extend"},
        {"max_breakpoints": True},
        {"max_breakpoints": 400001},
        {"max_diagnostics": 0},
    ],
)
def test_invalid_policies_fail_closed(kwargs) -> None:
    with pytest.raises(WaveformError):
        WaveformPolicy(**kwargs)


def test_incompatible_units_grids_domains_and_work_budgets_are_explicit() -> None:
    with pytest.raises(UnitError):
        compare_waveforms(trace(), trace(yu="A"))
    with pytest.raises(UnitError):
        trace(yu="rad")
    with pytest.raises(WaveformDomainError, match="identical"):
        compare_waveforms(trace(), trace((0, 0.5, 1), (0, 0.5, 1)))
    with pytest.raises(WaveformDomainError, match="interval"):
        compare_waveforms(trace(), trace((2, 3)))
    with pytest.raises(WaveformBudgetError, match="preflight"):
        compare_waveforms(trace(), trace(), WaveformPolicy(max_breakpoints=1))
