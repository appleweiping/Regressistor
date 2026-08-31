from __future__ import annotations

import copy

from regressistor.bundle import parse_bundle
from regressistor.inspection import inspect_bundle, inspection_text
from regressistor.policy import parse_policy
from tests.helpers import bundle_dict
from tests.test_gate import policy_data


def test_complete_inspection_lists_cases_units_and_samples() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 60.0}))
    data = bundle_dict()
    repeated = copy.deepcopy(data["points"][0])
    repeated["sample"] = 1
    repeated["metrics"]["gain"] = {"value": 64.0, "unit": "dB"}
    data["points"].append(repeated)
    inspection = inspect_bundle(policy, parse_bundle(data, source_path="candidate.json"))
    assert inspection.complete
    assert inspection.case_count == 1
    assert inspection.point_count == 2
    assert inspection.source_path == "candidate.json"
    metric = inspection.metrics[0]
    assert metric.present_cases == 1
    assert metric.sample_values == 2
    assert metric.observed_units == ("dB",)
    assert inspection.case_values == {"process": ("tt",), "vdd": (1.0,)}
    assert inspection.as_dict()["complete"] is True
    assert "Bundle coverage: COMPLETE" in inspection_text(inspection)


def test_incomplete_inspection_reports_missing_and_unconfigured_metrics() -> None:
    policy = parse_policy(policy_data(contract={"kind": "min", "limit": 60.0}))
    data = bundle_dict(process="ss")
    missing = bundle_dict(process="tt")["points"][0]
    missing["metrics"] = {"offset": {"value": 2.0, "unit": "mV"}}
    data["points"].append(missing)
    inspection = inspect_bundle(policy, parse_bundle(data))
    assert not inspection.complete
    assert inspection.metrics[0].missing_cases == 1
    assert inspection.unconfigured_metrics == ("offset",)
    text = inspection_text(inspection)
    assert "INCOMPLETE" in text
    assert "Unconfigured metrics: offset" in text
