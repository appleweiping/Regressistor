from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from regressistor.errors import InputError
from regressistor.render import (
    console_summary,
    decision_text,
    junit_xml,
    markdown,
    safe_html_text,
    write_artifacts,
)
from regressistor.report import MAX_REPORT_BYTES, load_report, report_from_dict
from tests.test_gate import run_compare


def test_report_dict_and_json_round_trip(tmp_path: Path) -> None:
    report = run_compare(65.0, 64.5)
    data = report.to_dict()
    assert data["schema_version"] == 1
    assert data["passed"] is True
    assert data["counts"]["pass"] == 1

    path = tmp_path / "nested" / "report.json"
    assert report.write_json(path) == path
    loaded = load_report(path)
    assert loaded.to_dict() == data


def test_report_negative_128_digit_integer_round_trips(tmp_path: Path) -> None:
    data = run_compare(65.0, 64.5).to_dict()
    data["results"][0]["case"]["vdd"] = -int("9" * 128)
    report = report_from_dict(data)
    path = report.write_json(tmp_path / "negative-boundary.json")
    assert load_report(path).to_dict() == report.to_dict()


def test_console_markdown_and_decision_explanation() -> None:
    report = run_compare(65.0, 58.0)
    summary = console_summary(report)
    assert "FAIL" in summary
    assert "spec_fail=1" in summary
    text = decision_text(report.decisions[0])
    assert "metric: gain" in text
    assert "contract margin: -2" in text
    document = markdown(report)
    assert "Regressistor report: FAIL" in document
    assert "Diagnostics" in document
    assert "gain" in document


def test_markdown_pass_has_no_diagnostics() -> None:
    document = markdown(run_compare(65.0, 65.0))
    assert "No failures or warnings" in document


def test_junit_marks_only_blocking_decisions_as_failures() -> None:
    failed = run_compare(65.0, 58.0)
    root = ET.fromstring(junit_xml(failed))
    assert root.attrib["failures"] == "1"
    failure = root.find("./testcase/failure")
    assert failure is not None and failure.attrib["type"] == "spec_fail"


def test_write_artifacts_creates_all_formats(tmp_path: Path) -> None:
    report = run_compare(65.0, 64.5)
    paths = write_artifacts(report, tmp_path / "artifacts")
    assert [path.name for path in paths] == ["report.json", "summary.md", "junit.xml"]
    assert all(path.is_file() for path in paths)


def test_html_helper_escapes_untrusted_labels() -> None:
    assert safe_html_text('<script data-x="1">') == "&lt;script data-x=&quot;1&quot;&gt;"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(schema_version=True), "schema_version"),
        (lambda data: data.update(case_keys="corner"), "case_keys"),
        (lambda data: data.update(inputs=[]), "inputs"),
        (lambda data: data.update(results={}), "results"),
        (lambda data: data["results"][0].update(case=[]), "case object"),
        (lambda data: data["results"][0].update(status="unknown"), "invalid status"),
        (lambda data: data["results"][0].update(blocking="yes"), "blocking"),
        (lambda data: data["results"][0].update(baseline="one"), "numeric or null"),
    ],
)
def test_rejects_invalid_report(mutation: object, message: str) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    mutation(data)  # type: ignore[operator]
    with pytest.raises(InputError, match=message):
        report_from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["results"][0].update(candidate=float("nan")),
        lambda data: data.update(run={"nested": [float("inf")]}),
        lambda data: data["results"][0].update(status=None),
        lambda data: data["results"][0]["case"].update(vdd=[]),
    ],
)
def test_report_rejects_non_json_and_non_finite_values(mutation: object) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    mutation(data)  # type: ignore[operator]
    with pytest.raises(InputError):
        report_from_dict(data)


def test_report_rejects_untrusted_control_characters() -> None:
    data = run_compare(65.0, 58.0).to_dict()
    data["results"][0]["metric"] = "gain\n| injected"
    with pytest.raises(InputError, match="portable"):
        report_from_dict(data)


def test_markdown_escapes_all_active_dynamic_markup() -> None:
    data = run_compare(65.0, 58.0).to_dict()
    data["results"][0].update(
        metric="<b>*gain*`x`</b>",
        message="[click](javascript:alert(1)) # heading",
    )
    document = markdown(report_from_dict(data))
    assert "<b>" not in document
    assert "javascript:alert" in document
    assert "\\*gain\\*&#96;x&#96;" in document
    assert "\\[click\\](javascript:alert(1)) \\# heading" in document


def test_report_rejects_deep_json_without_recursion_error() -> None:
    data = run_compare(65.0, 65.0).to_dict()
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    data["inputs"]["baseline_run"] = {"nested": nested}
    with pytest.raises(InputError, match="complexity limits"):
        report_from_dict(data)


def test_maximum_bundle_metadata_depth_survives_report_round_trip(tmp_path: Path) -> None:
    nested: object = "leaf"
    for _ in range(62):
        nested = [nested]
    data = run_compare(65.0, 65.0).to_dict()
    data["inputs"]["baseline_run"] = {"nested": nested}
    report = report_from_dict(data)
    path = report.write_json(tmp_path / "deep-valid-report.json")
    assert load_report(path).to_dict() == report.to_dict()


def test_load_report_rejects_missing_and_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="cannot read"):
        load_report(tmp_path / "missing.json")
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(InputError, match="cannot read"):
        load_report(path)


@pytest.mark.parametrize(
    "replacement",
    [
        '"schema_version":1,"schema_version":1',
        '"passed":NaN',
        '"passed":Infinity',
        '"passed":1e999',
        '"passed":' + "9" * 129,
    ],
)
def test_load_report_rejects_ambiguous_json(tmp_path: Path, replacement: str) -> None:
    path = run_compare(65.0, 65.0).write_json(tmp_path / "report.json")
    text = path.read_text(encoding="utf-8")
    if replacement.startswith('"schema_version"'):
        text = text.replace('"schema_version": 1', replacement)
    else:
        text = text.replace('"passed": true', replacement)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InputError):
        load_report(path)


def test_load_report_rejects_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_bytes(b" " * (MAX_REPORT_BYTES + 1))
    with pytest.raises(InputError, match="byte input limit"):
        load_report(path)


def test_report_rejects_unknown_fields_and_inconsistent_summary() -> None:
    data = run_compare(65.0, 65.0).to_dict()
    data["unknown"] = True
    with pytest.raises(InputError, match="fields"):
        report_from_dict(data)

    data = run_compare(65.0, 65.0).to_dict()
    data["passed"] = False
    with pytest.raises(InputError, match="summary"):
        report_from_dict(data)

    data = run_compare(65.0, 65.0).to_dict()
    data["results"][0]["unknown"] = True
    with pytest.raises(InputError, match="fields"):
        report_from_dict(data)


def test_report_rejects_empty_or_semantically_inconsistent_results() -> None:
    data = run_compare(65.0, 65.0).to_dict()
    data["results"] = []
    data["counts"]["pass"] = 0
    with pytest.raises(InputError, match="results"):
        report_from_dict(data)

    data = run_compare(65.0, 65.0).to_dict()
    data["results"][0]["blocking"] = True
    data["passed"] = False
    with pytest.raises(InputError, match="inconsistent"):
        report_from_dict(data)

    data = run_compare(65.0, 65.0).to_dict()
    data["results"].append(dict(data["results"][0]))
    data["counts"]["pass"] = 2
    with pytest.raises(InputError, match="duplicates"):
        report_from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: result.update(unit="bananas"),
        lambda result: result.update(candidate=None),
        lambda result: result.update(
            status="missing", candidate=None, contract_margin=None, baseline=1.0
        ),
        lambda result: result.update(regression_margin=999.0),
        lambda result: result.update(
            status="regression",
            contract_margin=None,
            regression_margin=1.0,
            adverse_change=0.0,
            allowed_change=1.0,
        ),
    ],
)
def test_report_rejects_impossible_decision_semantics(mutation: object) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    mutation(data["results"][0])  # type: ignore[operator]
    if data["results"][0]["status"] != "pass":
        data["counts"]["pass"] = 0
        data["counts"][data["results"][0]["status"]] = 1
        data["passed"] = not data["results"][0]["blocking"]
    with pytest.raises(InputError):
        report_from_dict(data)


@pytest.mark.parametrize("keys", [[], ["VDD", "vdd"], ["\x1b[31mVDD"]])
def test_report_rejects_impossible_case_key_sets(keys: list[str]) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    data["case_keys"] = keys
    data["results"][0]["case"] = {key: 1.0 for key in keys}
    with pytest.raises(InputError, match="case_keys"):
        report_from_dict(data)


@pytest.mark.parametrize("digits", [129, 5_001])
def test_programmatic_report_rejects_oversized_integers(digits: int) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    data["inputs"]["baseline_run"] = {"sequence": 10**digits}
    with pytest.raises(InputError, match="oversized number"):
        report_from_dict(data)


def test_report_json_is_deterministic(tmp_path: Path) -> None:
    report = run_compare(65.0, 64.5)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    report.write_json(first)
    report.write_json(second)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8"))["passed"] is True
