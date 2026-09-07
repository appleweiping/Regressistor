"""Contract and adverse-regression comparison engine."""

from __future__ import annotations

import math
from collections.abc import Iterable

from regressistor.dispersion import DEFAULT_MIN_NOISE_SAMPLES, NoiseAssessment
from regressistor.dispersion import assess as assess_noise
from regressistor.errors import InputError
from regressistor.matching import index_bundle, sorted_cases
from regressistor.model import (
    Bundle,
    CaseKey,
    Contract,
    ContractKind,
    Decision,
    Direction,
    Measurement,
    MetricPolicy,
    MissingAction,
    Policy,
    RegressionBudget,
    Severity,
    Status,
    case_identity,
)
from regressistor.reducers import reduce_values
from regressistor.report import MAX_DECISIONS, Report
from regressistor.units import convert


def _converted(samples: Iterable[Measurement], metric: MetricPolicy) -> list[float]:
    return [convert(sample.value, sample.unit, metric.unit) for sample in samples]


def _measure(samples: Iterable[Measurement], metric: MetricPolicy) -> float:
    return reduce_values(_converted(samples, metric), metric.reducer)


def _contract_margin(value: float, contract: Contract) -> float:
    if contract.kind is ContractKind.MIN:
        margin = value - _required(contract.lower, "minimum contract limit")
    elif contract.kind is ContractKind.MAX:
        margin = _required(contract.upper, "maximum contract limit") - value
    elif contract.kind is ContractKind.RANGE:
        lower = _required(contract.lower, "range contract lower limit")
        upper = _required(contract.upper, "range contract upper limit")
        margin = min(value - lower, upper - value)
    else:
        target = _required(contract.target, "target contract target")
        tolerance = _required(contract.tolerance, "target contract tolerance")
        margin = tolerance - abs(value - target)
    return _finite(margin, "contract margin")


def _required(value: float | None, context: str) -> float:
    if value is None:
        raise InputError(f"{context} is missing")
    return value


def _finite(value: float, context: str) -> float:
    if not math.isfinite(value):
        raise InputError(f"{context} is non-finite")
    return value


def _regression_values(
    baseline: float, candidate: float, budget: RegressionBudget
) -> tuple[float, float, float]:
    if budget.direction is Direction.HIGHER:
        adverse = baseline - candidate
    elif budget.direction is Direction.LOWER:
        adverse = candidate - baseline
    else:
        target = _required(budget.target, "target regression target")
        adverse = abs(candidate - target) - abs(baseline - target)
    allowed = budget.absolute + budget.relative * max(abs(baseline), budget.relative_floor)
    return (
        _finite(adverse, "adverse change"),
        _finite(allowed, "allowed change"),
        _finite(allowed - adverse, "regression margin"),
    )


def _missing_decision(
    metric: MetricPolicy,
    case: CaseKey,
    action: MissingAction,
    message: str,
) -> Decision:
    severity = Severity.ERROR if action is MissingAction.ERROR else Severity.WARNING
    return Decision(
        metric=metric.name,
        unit=metric.unit,
        case=case,
        status=Status.MISSING,
        severity=severity,
        blocking=action is MissingAction.ERROR,
        message=f"{message}; missing policy is {action.value}",
    )


def _invalid_decision(metric: MetricPolicy, case: CaseKey, message: str) -> Decision:
    return Decision(
        metric=metric.name,
        unit=metric.unit,
        case=case,
        status=Status.INVALID,
        severity=metric.severity,
        blocking=metric.severity is Severity.ERROR,
        message=message,
    )


def _noise_verdict(
    noise: NoiseAssessment | None, budget: RegressionBudget | None
) -> tuple[bool, str]:
    """Decide whether run-to-run scatter explains an adverse change.

    Returns whether the change survives the noise test and the phrase that
    describes why. A budget that cannot be assessed does not silently pass:
    the magnitude test then stands alone, which can only make the gate stricter
    than the policy asked for, never looser, and the decision message says so.
    """

    if budget is None or not budget.noise_gated:
        return True, ""
    if noise is None or not noise.applicable:
        reason = noise.reason if noise else "no measurements to assess"
        return True, f"; noise budget not applied because {reason}"
    standardized = noise.standardized_change
    if standardized is None:
        # A report read back from JSON cannot carry an unbounded ratio, and the
        # magnitude test standing alone is the stricter of the two readings.
        return True, "; change could not be expressed in units of scatter"
    if standardized <= budget.noise:
        return False, (
            f"; change is {standardized:.4g} standard errors, within the "
            f"{budget.noise:.4g} the policy attributes to run-to-run scatter"
        )
    return True, f"; change is {standardized:.4g} standard errors of run-to-run scatter"


def _evaluate(
    metric: MetricPolicy,
    case: CaseKey,
    baseline_samples: list[Measurement] | None,
    candidate_samples: list[Measurement],
    epsilon: float,
) -> Decision:
    try:
        candidate_values = _converted(candidate_samples, metric)
        baseline_values = _converted(baseline_samples, metric) if baseline_samples else None
        candidate = reduce_values(candidate_values, metric.reducer)
        baseline = reduce_values(baseline_values, metric.reducer) if baseline_values else None
    except InputError as error:
        return _invalid_decision(metric, case, str(error))

    try:
        contract_margin = (
            _contract_margin(candidate, metric.contract) if metric.contract is not None else None
        )
        adverse: float | None = None
        allowed: float | None = None
        regression_margin: float | None = None
        if metric.regression is not None and baseline is not None:
            adverse, allowed, regression_margin = _regression_values(
                baseline, candidate, metric.regression
            )
    except (ArithmeticError, InputError) as error:
        return _invalid_decision(metric, case, str(error))

    try:
        noise = assess_noise(
            baseline_values,
            candidate_values,
            adverse,
            min_samples=(
                metric.regression.noise_min_samples
                if metric.regression is not None
                else DEFAULT_MIN_NOISE_SAMPLES
            ),
        )
    except InputError as error:
        return _invalid_decision(metric, case, str(error))
    beyond_noise, noise_note = _noise_verdict(noise, metric.regression)

    if contract_margin is not None and contract_margin < -epsilon:
        # A contract is a statement about the measurement, not about the change
        # between two of them, so scatter does not excuse breaching one.
        status = Status.SPEC_FAIL
        message = f"contract margin {contract_margin:.12g} {metric.unit} is below zero"
    elif regression_margin is not None and regression_margin < -epsilon and beyond_noise:
        status = Status.REGRESSION
        message = (
            f"adverse change {adverse:.12g} exceeds allowed change {allowed:.12g} "
            f"{metric.unit}{noise_note}"
        )
    elif regression_margin is not None and regression_margin < -epsilon:
        status = Status.PASS
        message = (
            f"adverse change {adverse:.12g} exceeds allowed change {allowed:.12g} "
            f"{metric.unit} but is not distinguishable from noise{noise_note}"
        )
    else:
        status = Status.PASS
        message = f"contract and regression checks passed{noise_note}"

    blocking = status is not Status.PASS and metric.severity is Severity.ERROR
    return Decision(
        metric=metric.name,
        unit=metric.unit,
        case=case,
        status=status,
        severity=metric.severity,
        blocking=blocking,
        message=message,
        baseline=baseline,
        candidate=candidate,
        contract_margin=contract_margin,
        regression_margin=regression_margin,
        adverse_change=adverse,
        allowed_change=allowed,
        noise=noise,
    )


def compare(policy: Policy, baseline: Bundle, candidate: Bundle) -> Report:
    """Compare candidate measurements against policy and baseline."""
    baseline_index = index_bundle(baseline, policy)
    candidate_index = index_bundle(candidate, policy)
    identities = set(baseline_index) | set(candidate_index)
    expanded_decisions = len(identities) * len(policy.metrics)
    if expanded_decisions > MAX_DECISIONS:
        raise InputError(
            f"comparison expands to {expanded_decisions} decisions; limit is {MAX_DECISIONS}"
        )
    cases_by_identity = {
        identity: (candidate_index.get(identity) or baseline_index[identity])[0]
        for identity in identities
    }
    cases = sorted_cases(cases_by_identity.values())
    decisions: list[Decision] = []

    for case in cases:
        identity = case_identity(case)
        baseline_bucket = baseline_index.get(identity)
        candidate_bucket = candidate_index.get(identity)
        baseline_metrics = baseline_bucket[1] if baseline_bucket else None
        candidate_metrics = candidate_bucket[1] if candidate_bucket else None
        for metric in policy.metrics:
            if candidate_metrics is None:
                decisions.append(
                    _missing_decision(
                        metric,
                        case,
                        policy.missing.candidate_case,
                        "candidate case is absent",
                    )
                )
                continue
            candidate_samples = candidate_metrics.get(metric.name)
            if not candidate_samples:
                decisions.append(
                    _missing_decision(
                        metric,
                        case,
                        policy.missing.candidate_metric,
                        "candidate metric is absent",
                    )
                )
                continue

            baseline_samples = baseline_metrics.get(metric.name) if baseline_metrics else None
            if metric.regression is not None and not baseline_samples:
                action = (
                    policy.missing.baseline_case
                    if baseline_metrics is None
                    else policy.missing.baseline_metric
                )
                if action is not MissingAction.IGNORE:
                    decisions.append(
                        _missing_decision(metric, case, action, "regression baseline is absent")
                    )
                    continue
            decisions.append(
                _evaluate(
                    metric,
                    case,
                    baseline_samples,
                    candidate_samples,
                    policy.numeric_epsilon,
                )
            )

    if not decisions:
        raise InputError("comparison produced no decisions")
    return Report(
        tuple(decisions),
        policy.case_keys,
        policy.source_hash,
        baseline.source_hash,
        candidate.source_hash,
        dict(baseline.run),
        dict(candidate.run),
    )
