from __future__ import annotations

import copy

import pytest

from regressistor.bundle import parse_bundle
from regressistor.errors import InputError
from regressistor.gate import compare
from regressistor.matching import index_bundle, project_case, sorted_cases
from regressistor.model import Status
from regressistor.policy import parse_policy
from tests.helpers import bundle_dict


def policy_data(
    *,
    contract: dict[str, object] | None = None,
    regression: dict[str, object] | None = None,
    severity: str = "error",
    missing: dict[str, str] | None = None,
    reducer: str = "mean",
    unit: str = "dB",
) -> dict[str, object]:
    metric: dict[str, object] = {
        "name": "gain",
        "unit": unit,
        "reduce": reducer,
        "severity": severity,
    }
    if contract is not None:
        metric["contract"] = contract
    if regression is not None:
        metric["regression"] = regression
    return {
        "schema_version": 1,
        "case_keys": ["process", "vdd"],
        "missing": missing or {},
        "metrics": [metric],
    }


def run_compare(
    baseline_value: float,
    candidate_value: float,
    *,
    policy: dict[str, object] | None = None,
):
    parsed_policy = parse_policy(
        policy
        or policy_data(
            contract={"kind": "min", "limit": 60.0},
            regression={"direction": "higher", "absolute_budget": 1.0},
        )
    )
    return compare(
        parsed_policy,
        parse_bundle(bundle_dict(baseline_value), source_hash="base"),
        parse_bundle(bundle_dict(candidate_value), source_hash="candidate"),
    )


def test_passing_comparison_has_provenance_and_values() -> None:
    report = run_compare(65.0, 64.5)
    assert report.passed
    assert report.baseline_sha256 == "base"
    assert report.candidate_sha256 == "candidate"
    decision = report.decisions[0]
    assert decision.status is Status.PASS
    assert decision.contract_margin == pytest.approx(4.5)
    assert decision.regression_margin == pytest.approx(0.5)


def test_boolean_and_integer_case_values_are_distinct() -> None:
    policy = parse_policy(
        {
            "schema_version": 1,
            "case_keys": ["process", "vdd"],
            "metrics": [{"name": "gain", "unit": "dB", "contract": {"kind": "min", "limit": 0}}],
        }
    )
    baseline_data = bundle_dict(65.0)
    baseline_data["points"][0]["case"]["process"] = True
    second = copy.deepcopy(baseline_data["points"][0])
    second["case"]["process"] = 1
    second["sample"] = "integer"
    baseline_data["points"].append(second)
    candidate_data = copy.deepcopy(baseline_data)
    report = compare(policy, parse_bundle(baseline_data), parse_bundle(candidate_data))
    assert len(report.decisions) == 2
    assert {type(dict(decision.case)["process"]) for decision in report.decisions} == {bool, int}


def test_contract_failure_precedes_regression() -> None:
    decision = run_compare(65.0, 58.0).decisions[0]
    assert decision.status is Status.SPEC_FAIL
    assert decision.blocking
    assert decision.contract_margin == -2.0


def test_higher_direction_detects_adverse_regression() -> None:
    policy = policy_data(
        contract={"kind": "min", "limit": 50.0},
        regression={
            "direction": "higher",
            "absolute_budget": 1.0,
            "relative_budget": 0.01,
            "relative_floor": 1.0,
        },
    )
    decision = run_compare(100.0, 97.0, policy=policy).decisions[0]
    assert decision.status is Status.REGRESSION
    assert decision.adverse_change == 3.0
    assert decision.allowed_change == 2.0


def test_improvement_is_not_a_regression() -> None:
    report = run_compare(65.0, 70.0)
    assert report.passed
    assert report.decisions[0].adverse_change == -5.0


def test_lower_direction_and_relative_floor() -> None:
    policy = policy_data(
        contract={"kind": "max", "limit": 10.0},
        regression={
            "direction": "lower",
            "relative_budget": 0.1,
            "relative_floor": 5.0,
        },
        unit="mW",
    )
    baseline = bundle_dict(0.0, unit="mW")
    candidate = bundle_dict(0.6, unit="mW")
    decision = compare(
        parse_policy(policy), parse_bundle(baseline), parse_bundle(candidate)
    ).decisions[0]
    assert decision.status is Status.REGRESSION
    assert decision.allowed_change == 0.5


def test_target_contract_and_regression() -> None:
    policy = policy_data(
        contract={"kind": "target", "target": 5.0, "tolerance": 2.0},
        regression={"direction": "target", "target": 5.0, "absolute_budget": 0.25},
        unit="V",
    )
    baseline = parse_bundle(bundle_dict(5.1, unit="V"))
    candidate = parse_bundle(bundle_dict(5.6, unit="V"))
    decision = compare(parse_policy(policy), baseline, candidate).decisions[0]
    assert decision.status is Status.REGRESSION
    assert decision.adverse_change == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("contract", "candidate", "status"),
    [
        ({"kind": "max", "limit": 2.0}, 2.1, Status.SPEC_FAIL),
        ({"kind": "range", "lower": 1.0, "upper": 3.0}, 2.0, Status.PASS),
        ({"kind": "range", "lower": 1.0, "upper": 3.0}, 3.2, Status.SPEC_FAIL),
        ({"kind": "target", "target": 2.0, "tolerance": 0.5}, 1.4, Status.SPEC_FAIL),
    ],
)
def test_contract_kinds(contract: dict[str, object], candidate: float, status: Status) -> None:
    policy = policy_data(contract=contract, unit="V")
    report = compare(
        parse_policy(policy),
        parse_bundle(bundle_dict(2.0, unit="V")),
        parse_bundle(bundle_dict(candidate, unit="V")),
    )
    assert report.decisions[0].status is status


def test_warning_failure_is_non_blocking() -> None:
    policy = policy_data(contract={"kind": "min", "limit": 60.0}, severity="warning")
    report = run_compare(65.0, 50.0, policy=policy)
    assert report.passed
    assert report.decisions[0].status is Status.SPEC_FAIL
    assert not report.decisions[0].blocking


def test_candidate_case_and_metric_missing() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 60.0}))
    baseline = parse_bundle(bundle_dict())
    different_case = parse_bundle(bundle_dict(process="ss"))
    report = compare(policy, baseline, different_case)
    assert [item.status for item in report.decisions] == [Status.PASS, Status.MISSING]
    assert not report.passed

    no_gain = bundle_dict()
    no_gain["points"][0]["metrics"] = {"other": {"value": 1.0, "unit": "1"}}
    missing_metric = compare(policy, baseline, parse_bundle(no_gain)).decisions[0]
    assert missing_metric.status is Status.MISSING
    assert "candidate metric" in missing_metric.message


def test_baseline_missing_can_warn_or_be_ignored() -> None:
    base = bundle_dict()
    base["points"][0]["metrics"] = {"other": {"value": 1.0, "unit": "1"}}
    candidate = parse_bundle(bundle_dict(65.0))
    warning_policy = parse_policy(
        policy_data(
            contract={"kind": "min", "limit": 60.0},
            regression={"direction": "higher"},
            missing={"baseline_metric": "warning"},
        )
    )
    warning_report = compare(warning_policy, parse_bundle(base), candidate)
    assert warning_report.passed
    assert warning_report.decisions[0].status is Status.MISSING

    ignore_policy = parse_policy(
        policy_data(
            contract={"kind": "min", "limit": 60.0},
            regression={"direction": "higher"},
            missing={"baseline_metric": "ignore"},
        )
    )
    ignored_report = compare(ignore_policy, parse_bundle(base), candidate)
    assert ignored_report.decisions[0].status is Status.PASS
    assert ignored_report.decisions[0].baseline is None


def test_contract_only_metric_does_not_require_baseline_case() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 60.0}))
    report = compare(
        policy,
        parse_bundle(bundle_dict(process="ss")),
        parse_bundle(bundle_dict(process="tt")),
    )
    assert any(decision.status is Status.PASS for decision in report.decisions)


def test_incompatible_unit_is_an_invalid_decision() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 1.0}, unit="V"))
    report = compare(
        policy,
        parse_bundle(bundle_dict(2.0, unit="V")),
        parse_bundle(bundle_dict(2.0, unit="A")),
    )
    assert report.decisions[0].status is Status.INVALID
    assert not report.passed


def test_repeated_samples_are_converted_then_reduced() -> None:
    policy = parse_policy(
        policy_data(contract={"kind": "min", "limit": 1.4}, unit="V", reducer="mean")
    )
    baseline_data = bundle_dict(1.5, unit="V")
    candidate_data = copy.deepcopy(baseline_data)
    candidate_data["points"][0]["metrics"]["gain"] = {"value": 1000.0, "unit": "mV"}
    second = copy.deepcopy(candidate_data["points"][0])
    second["sample"] = 1
    second["metrics"]["gain"] = {"value": 2.0, "unit": "V"}
    candidate_data["points"].append(second)
    report = compare(policy, parse_bundle(baseline_data), parse_bundle(candidate_data))
    assert report.decisions[0].candidate == 1.5
    assert report.passed


def test_case_projection_sorting_and_duplicate_detection() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 1.0}))
    bundle_data = bundle_dict()
    bundle_data["points"][0]["case"]["extra"] = "first"
    second = copy.deepcopy(bundle_data["points"][0])
    second["case"]["extra"] = "second"
    bundle_data["points"].append(second)
    with pytest.raises(InputError, match="duplicate projected"):
        index_bundle(parse_bundle(bundle_data), policy)

    assert project_case((("vdd", 1.0), ("process", "tt")), policy.case_keys) == (
        ("process", "tt"),
        ("vdd", 1.0),
    )
    with pytest.raises(InputError, match="missing policy keys"):
        project_case((("process", "tt"),), policy.case_keys)
    cases = {(("process", "tt"), ("vdd", 1.0)), (("process", "ss"), ("vdd", 0.9))}
    assert dict(sorted_cases(cases)[0])["process"] == "ss"
