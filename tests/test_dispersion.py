"""Run-to-run scatter, and the verdicts it changes."""

from __future__ import annotations

import json
import math
import random
import statistics

import pytest

from regressistor.dispersion import (
    DEFAULT_MIN_NOISE_SAMPLES,
    MIN_NOISE_SAMPLES,
    Dispersion,
    assess,
    describe,
    difference_standard_error,
    standardize,
    welch_degrees_of_freedom,
)
from regressistor.errors import InputError
from regressistor.gate import compare
from regressistor.model import (
    Bundle,
    Direction,
    Measurement,
    MetricPolicy,
    Point,
    Policy,
    Reducer,
    RegressionBudget,
    Severity,
    Status,
)
from regressistor.policy import parse_policy
from regressistor.report import report_from_dict

# ---------------------------------------------------------------------------
# Describing one side.
# ---------------------------------------------------------------------------


def test_the_summary_matches_the_textbook_statistics() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    summary = describe(values)
    assert summary.count == 4
    assert summary.mean == pytest.approx(statistics.fmean(values))
    assert summary.deviation == pytest.approx(statistics.stdev(values))
    assert summary.span == pytest.approx(3.0)


def test_the_deviation_carries_the_bessel_correction() -> None:
    # Dividing by n rather than n-1 would give 0.5 here, not 0.5 * sqrt(2).
    assert describe([1.0, 2.0]).deviation == pytest.approx(math.sqrt(0.5))


def test_a_single_measurement_reports_no_scatter_rather_than_a_fabricated_one() -> None:
    summary = describe([3.5])
    assert summary.count == 1
    assert summary.deviation == 0.0
    assert summary.span == 0.0
    assert summary.mean == 3.5


def test_repeats_shrink_the_uncertainty_of_the_mean() -> None:
    few = describe([1.0, 2.0, 3.0])
    many = describe([1.0, 2.0, 3.0] * 4)
    assert many.deviation == pytest.approx(few.deviation, rel=0.2)
    assert many.standard_error < few.standard_error


def test_the_standard_error_is_the_deviation_over_the_root_count() -> None:
    summary = describe([2.0, 4.0, 6.0, 8.0])
    assert summary.standard_error == pytest.approx(summary.deviation / math.sqrt(4))


def test_relative_scatter_around_zero_is_reported_as_unbounded() -> None:
    # A small absolute scatter about a mean of zero is not a small relative one.
    assert describe([-1.0, 1.0]).relative_deviation == math.inf
    assert describe([0.0, 0.0]).relative_deviation == 0.0
    assert describe([100.0, 102.0]).relative_deviation == pytest.approx(
        statistics.stdev([100.0, 102.0]) / 101.0
    )


def test_an_empty_or_non_finite_set_is_refused() -> None:
    with pytest.raises(InputError, match="empty"):
        describe([])
    with pytest.raises(InputError, match="non-finite"):
        describe([1.0, float("nan")])


# ---------------------------------------------------------------------------
# Combining two sides.
# ---------------------------------------------------------------------------


def test_the_combined_error_adds_variances_rather_than_pooling_them() -> None:
    left = describe([1.0, 2.0, 3.0])
    right = describe([10.0, 20.0, 30.0])
    assert difference_standard_error(left, right) == pytest.approx(
        math.sqrt(left.standard_error**2 + right.standard_error**2)
    )


def test_the_combined_error_is_dominated_by_the_noisier_side() -> None:
    """Welch's point: a precise side cannot rescue a scattered one."""

    precise = describe([1.0, 1.0, 1.0, 1.000001])
    scattered = describe([1.0, 5.0, 9.0, 2.0])
    combined = difference_standard_error(precise, scattered)
    assert combined == pytest.approx(scattered.standard_error, rel=1e-3)


def test_equal_variances_and_counts_recover_the_pooled_degrees_of_freedom() -> None:
    # Welch-Satterthwaite reduces to 2n-2 when both sides match, which is the
    # classic two-sample figure and a check that the algebra is right.
    for count in (3, 6, 20):
        rng = random.Random(count)
        values = [rng.gauss(0.0, 1.0) for _ in range(count)]
        side = describe(values)
        assert welch_degrees_of_freedom(side, side) == pytest.approx(2 * count - 2)


def test_one_scattered_side_pulls_the_degrees_of_freedom_toward_its_own() -> None:
    precise = describe([1.0, 1.0, 1.0, 1.0, 1.0, 1.000001])
    scattered = describe([1.0, 9.0, 3.0])
    assert welch_degrees_of_freedom(precise, scattered) == pytest.approx(2.0, abs=0.5)


def test_two_exactly_repeating_sides_have_nothing_to_estimate() -> None:
    exact = describe([2.0, 2.0, 2.0])
    assert welch_degrees_of_freedom(exact, exact) == 0.0
    assert difference_standard_error(exact, exact) == 0.0


# ---------------------------------------------------------------------------
# Standardizing a change.
# ---------------------------------------------------------------------------


def test_a_change_is_measured_in_standard_errors() -> None:
    assert standardize(0.6, 0.2) == pytest.approx(3.0)
    assert standardize(-0.6, 0.2) == pytest.approx(-3.0)


def test_no_change_against_no_scatter_is_zero_not_indeterminate() -> None:
    assert standardize(0.0, 0.0) == 0.0


def test_a_change_against_no_scatter_is_unbounded() -> None:
    # A quantity that reproduces to the last bit and then moves did not move by
    # chance, so there is no finite number of standard errors to report.
    assert standardize(0.5, 0.0) == math.inf
    assert standardize(-0.5, 0.0) == -math.inf


# ---------------------------------------------------------------------------
# Assessing a comparison.
# ---------------------------------------------------------------------------


def test_an_assessment_reports_both_sides_and_the_ratio() -> None:
    baseline = [10.0, 10.2, 9.8, 10.1]
    candidate = [9.0, 9.2, 8.8, 9.1]
    result = assess(baseline, candidate, 1.0, min_samples=3)
    assert result.applicable
    assert result.baseline is not None
    assert result.baseline.count == 4
    assert result.candidate.count == 4
    assert result.standardized_change is not None
    assert result.standardized_change > 5.0


def test_too_few_repeats_leave_the_scatter_unestimated() -> None:
    result = assess([1.0, 1.1], [2.0, 2.1, 2.2, 2.3], 1.0, min_samples=3)
    assert not result.applicable
    assert "2 repeat(s) on the thinner side" in result.reason
    # Everything measurable is still reported, because these are the numbers a
    # policy needs in order to choose a budget in the first place.
    assert result.baseline is not None
    assert result.standard_error is not None


def test_an_absent_baseline_cannot_be_assessed() -> None:
    result = assess(None, [1.0, 2.0, 3.0], 1.0)
    assert not result.applicable
    assert result.baseline is None
    assert "no baseline" in result.reason


def test_a_metric_without_a_regression_budget_is_not_assessed() -> None:
    result = assess([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], None)
    assert not result.applicable
    assert "no regression budget" in result.reason
    assert result.baseline is not None


def test_exactly_repeating_measurements_say_so_in_the_reason() -> None:
    result = assess([1.0] * 4, [0.9] * 4, 0.1)
    assert result.applicable
    assert result.standard_error == 0.0
    assert result.standardized_change == math.inf
    assert "did not vary" in result.reason


def test_a_minimum_below_two_samples_is_refused() -> None:
    with pytest.raises(InputError, match="at least"):
        assess([1.0, 2.0], [1.0, 2.0], 0.5, min_samples=1)


def test_the_default_minimum_is_the_smallest_meaningful_count() -> None:
    assert DEFAULT_MIN_NOISE_SAMPLES > MIN_NOISE_SAMPLES
    assert MIN_NOISE_SAMPLES == 2


def test_an_unbounded_ratio_serializes_as_null_with_a_zero_error() -> None:
    """JSON has no infinity, and the case must stay recoverable without one."""

    payload = assess([1.0] * 4, [0.9] * 4, 0.1).as_dict()
    assert payload["standardized_change"] is None
    assert payload["standard_error"] == 0.0
    assert payload["applicable"] is True
    assert "did not vary" in str(payload["reason"])
    json.dumps(payload, allow_nan=False)


def test_a_finite_ratio_survives_serialization() -> None:
    payload = assess([10.0, 10.2, 9.8], [9.0, 9.2, 8.8], 1.0).as_dict()
    assert isinstance(payload["standardized_change"], float)
    assert payload["standardized_change"] > 0.0


# ---------------------------------------------------------------------------
# What the gate does with it.
# ---------------------------------------------------------------------------


def bundle(values: list[float], tag: str, *, metric: str = "gain") -> Bundle:
    return Bundle(
        points=tuple(
            Point(
                case=(("process", "tt"),),
                metrics={metric: Measurement(value, "V")},
                sample=f"{tag}-{index}",
            )
            for index, value in enumerate(values)
        ),
        run={"id": tag},
        source_hash="",
        source_path="",
    )


def gate_policy(*, noise: float = 0.0, relative: float = 0.01, min_samples: int = 3) -> Policy:
    return Policy(
        case_keys=("process",),
        metrics=(
            MetricPolicy(
                name="gain",
                unit="V",
                reducer=Reducer.MEAN,
                severity=Severity.ERROR,
                regression=RegressionBudget(
                    direction=Direction.HIGHER,
                    relative=relative,
                    relative_floor=1.0,
                    noise=noise,
                    noise_min_samples=min_samples,
                ),
            ),
        ),
    )


SCATTERED_BASELINE = [1.00, 1.03, 0.97, 1.02, 0.98]
SCATTERED_CANDIDATE = [0.985, 1.015, 0.955, 1.005, 0.965]


def test_a_change_inside_the_scatter_is_no_longer_a_regression() -> None:
    """The false alarm the feature exists to remove.

    The candidate mean is below the baseline by more than the one percent the
    policy calls material, but the two sets overlap heavily.
    """

    plain = compare(
        gate_policy(), bundle(SCATTERED_BASELINE, "b"), bundle(SCATTERED_CANDIDATE, "c")
    ).decisions[0]
    gated = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    ).decisions[0]
    assert plain.status is Status.REGRESSION
    assert gated.status is Status.PASS
    assert "not distinguishable from noise" in gated.message


def test_a_change_beyond_the_scatter_is_still_a_regression() -> None:
    gated = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle([0.70, 0.73, 0.67, 0.72, 0.68], "c"),
    ).decisions[0]
    assert gated.status is Status.REGRESSION
    assert "standard errors" in gated.message


def test_an_exactly_repeating_measurement_that_moves_is_a_regression() -> None:
    # The ordinary case for a deterministic simulator: no scatter to hide in.
    gated = compare(
        gate_policy(noise=3.0), bundle([1.0] * 4, "b"), bundle([0.97] * 4, "c")
    ).decisions[0]
    assert gated.status is Status.REGRESSION
    assert gated.noise is not None
    assert gated.noise.standard_error == 0.0


def test_a_contract_breach_is_not_excused_by_scatter() -> None:
    """A contract is about the measurement, not about a change between two."""

    from regressistor.model import Contract, ContractKind

    policy = Policy(
        case_keys=("process",),
        metrics=(
            MetricPolicy(
                name="gain",
                unit="V",
                reducer=Reducer.MEAN,
                severity=Severity.ERROR,
                contract=Contract(kind=ContractKind.MIN, lower=5.0),
                regression=RegressionBudget(
                    direction=Direction.HIGHER, relative=0.01, relative_floor=1.0, noise=99.0
                ),
            ),
        ),
    )
    decision = compare(policy, bundle([1.0] * 4, "b"), bundle([1.0] * 4, "c")).decisions[0]
    assert decision.status is Status.SPEC_FAIL


def test_too_few_repeats_fall_back_to_the_magnitude_test_and_say_so() -> None:
    """The fallback can only make the gate stricter, never looser."""

    decision = compare(
        gate_policy(noise=3.0, min_samples=6),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    ).decisions[0]
    assert decision.status is Status.REGRESSION
    assert "noise budget not applied because" in decision.message
    assert "below the 6 required" in decision.message


def test_exactly_the_required_number_of_repeats_is_enough() -> None:
    # The floor is inclusive: five repeats satisfy a floor of five.
    decision = compare(
        gate_policy(noise=3.0, min_samples=5),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    ).decisions[0]
    assert decision.noise is not None
    assert decision.noise.applicable
    assert decision.status is Status.PASS


def test_a_metric_without_a_noise_budget_is_unaffected() -> None:
    decision = compare(
        gate_policy(), bundle(SCATTERED_BASELINE, "b"), bundle(SCATTERED_CANDIDATE, "c")
    ).decisions[0]
    assert decision.status is Status.REGRESSION
    assert "standard errors" not in decision.message


def test_the_scatter_is_reported_even_without_a_noise_budget() -> None:
    # The numbers a policy needs to choose a budget exist before the budget.
    decision = compare(
        gate_policy(), bundle(SCATTERED_BASELINE, "b"), bundle(SCATTERED_CANDIDATE, "c")
    ).decisions[0]
    assert decision.noise is not None
    assert decision.noise.baseline is not None
    assert decision.noise.baseline.count == 5
    assert decision.noise.standard_error is not None


# ---------------------------------------------------------------------------
# Policy parsing.
# ---------------------------------------------------------------------------


def policy_document(**regression: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_keys": ["process"],
        "metrics": [
            {
                "name": "gain",
                "unit": "V",
                "reduce": "mean",
                "severity": "error",
                "regression": {"direction": "higher", "relative_budget": 0.01, **regression},
            }
        ],
    }


def test_a_noise_budget_is_parsed_with_its_sample_floor() -> None:
    policy = parse_policy(policy_document(noise_budget=2.5, noise_min_samples=8))
    budget = policy.metrics[0].regression
    assert budget is not None
    assert budget.noise == 2.5
    assert budget.noise_min_samples == 8
    assert budget.noise_gated


def test_an_absent_noise_budget_leaves_the_gate_unchanged() -> None:
    budget = parse_policy(policy_document()).metrics[0].regression
    assert budget is not None
    assert budget.noise == 0.0
    assert not budget.noise_gated
    assert budget.noise_min_samples == DEFAULT_MIN_NOISE_SAMPLES


@pytest.mark.parametrize("value", [-1.0, "3", True])
def test_an_invalid_noise_budget_is_refused(value: object) -> None:
    with pytest.raises(InputError, match="noise_budget"):
        parse_policy(policy_document(noise_budget=value))


def test_a_non_finite_noise_budget_is_refused_before_the_field_is_read() -> None:
    # The document-level scan rejects non-finite numbers anywhere, which is a
    # stronger guard than the per-field one and reports itself as such.
    with pytest.raises(InputError, match="non-finite"):
        parse_policy(policy_document(noise_budget=float("nan")))


@pytest.mark.parametrize("value", [1, 0, -3, 2.5, "4", True])
def test_an_invalid_sample_floor_is_refused(value: object) -> None:
    with pytest.raises(InputError, match="noise_min_samples"):
        parse_policy(policy_document(noise_min_samples=value))


@pytest.mark.parametrize("reducer", ["max", "min", "median", "p05", "p95"])
def test_a_noise_budget_is_refused_for_a_reducer_it_does_not_describe(reducer: str) -> None:
    """The limitation, enforced where the reader can act on it.

    The budget counts standard errors of the mean. An extreme moves far more
    between identical runs than the mean does, so applying the mean's error to
    it would understate the noise and fire on scatter.
    """

    document = policy_document(noise_budget=3.0)
    metrics = document["metrics"]
    assert isinstance(metrics, list)
    metrics[0]["reduce"] = reducer
    with pytest.raises(InputError, match='requires reduce = "mean"'):
        parse_policy(document)


def test_those_reducers_remain_available_without_a_noise_budget() -> None:
    document = policy_document()
    metrics = document["metrics"]
    assert isinstance(metrics, list)
    metrics[0]["reduce"] = "p95"
    assert parse_policy(document).metrics[0].reducer is Reducer.P95


def test_an_unknown_regression_field_is_still_refused() -> None:
    with pytest.raises(InputError, match="unknown"):
        parse_policy(policy_document(noise_bugdet=3.0))


# ---------------------------------------------------------------------------
# The report carries it.
# ---------------------------------------------------------------------------


def round_trip(policy: Policy, baseline: Bundle, candidate: Bundle) -> object:
    report = compare(policy, baseline, candidate)
    payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    return report_from_dict(payload).decisions[0].noise


def test_the_noise_block_survives_the_report_round_trip() -> None:
    noise = round_trip(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    )
    assert noise is not None
    assert noise.applicable  # type: ignore[union-attr]
    assert noise.baseline is not None  # type: ignore[union-attr]
    assert noise.baseline.count == 5  # type: ignore[union-attr]


def test_an_unbounded_ratio_round_trips_as_a_null_with_its_reason() -> None:
    noise = round_trip(gate_policy(noise=3.0), bundle([1.0] * 4, "b"), bundle([0.97] * 4, "c"))
    assert noise is not None
    assert noise.standardized_change is None  # type: ignore[union-attr]
    assert noise.standard_error == 0.0  # type: ignore[union-attr]
    assert "did not vary" in noise.reason  # type: ignore[union-attr]


def test_a_report_with_an_inconsistent_standard_error_is_refused() -> None:
    """The stored error is derived, so it is recomputed rather than trusted."""

    report = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    )
    payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    payload["results"][0]["noise"]["candidate"]["standard_error"] = 999.0
    with pytest.raises(InputError, match="disagrees"):
        report_from_dict(payload)


def test_a_report_claiming_scatter_from_one_measurement_is_refused() -> None:
    report = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    )
    payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    payload["results"][0]["noise"]["candidate"] = Dispersion(
        count=1, mean=1.0, deviation=0.5, span=0.5
    ).as_dict()
    with pytest.raises(InputError, match="single measurement"):
        report_from_dict(payload)


def test_a_report_applicable_without_a_baseline_is_refused() -> None:
    report = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    )
    payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    payload["results"][0]["noise"]["baseline"] = None
    with pytest.raises(InputError, match="applicable without a baseline"):
        report_from_dict(payload)


def test_an_unknown_noise_field_is_refused() -> None:
    report = compare(
        gate_policy(noise=3.0),
        bundle(SCATTERED_BASELINE, "b"),
        bundle(SCATTERED_CANDIDATE, "c"),
    )
    payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    payload["results"][0]["noise"]["extra"] = 1
    with pytest.raises(InputError, match="fields are invalid"):
        report_from_dict(payload)
