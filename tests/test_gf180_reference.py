from __future__ import annotations

import itertools
import json
from copy import deepcopy
from pathlib import Path

from regressistor.bundle import load_bundle, parse_bundle
from regressistor.gate import compare
from regressistor.policy import load_policy

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "benchmarks" / "fixtures" / "simcairn-gf180-ngspice42.json"
POLICY = ROOT / "benchmarks" / "gf180-policy.toml"
METRIC_UNITS = {
    "gain_100khz": "1",
    "output_pp_v": "V",
    "output_v": "V",
    "power_w": "W",
    "supply_current_a": "A",
}


def test_gf180_reference_has_all_identities_metrics_units_and_passes_self_gate() -> None:
    bundle = load_bundle(FIXTURE)
    expected = {
        (corner, vdd, temperature)
        for corner, vdd, temperature in itertools.product(
            ("tt", "ss", "ff"), ("2.97", "3.30", "3.63"), ("-40", "27", "125")
        )
    }
    observed = {
        (dict(point.case)["CORNER"], dict(point.case)["VDD"], dict(point.case)["TEMP_C"])
        for point in bundle.points
    }
    assert len(bundle.points) == 27
    assert observed == expected
    for point in bundle.points:
        assert point.sample == "0"
        assert {
            name: measurement.unit for name, measurement in point.metrics.items()
        } == METRIC_UNITS
    report = compare(load_policy(POLICY), bundle, bundle)
    assert report.passed is True
    assert len(report.decisions) == 27 * len(METRIC_UNITS)


def test_gf180_policy_rejects_numeric_regression_and_missing_case() -> None:
    baseline = load_bundle(FIXTURE)
    policy = load_policy(POLICY)
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))

    perturbed = deepcopy(raw)
    perturbed["points"][0]["metrics"]["gain_100khz"]["value"] *= 0.5
    regression = compare(policy, baseline, parse_bundle(perturbed))
    assert regression.passed is False
    assert any(decision.blocking for decision in regression.decisions)

    incomplete = deepcopy(raw)
    incomplete["points"].pop()
    missing = compare(policy, baseline, parse_bundle(incomplete))
    assert missing.passed is False
    assert sum(decision.status.value == "missing" for decision in missing.decisions) == len(
        METRIC_UNITS
    )
