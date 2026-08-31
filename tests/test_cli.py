from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from regressistor.cli import build_parser, main
from regressistor.report import report_from_dict
from tests.helpers import write_inputs
from tests.test_gate import run_compare


def test_parser_exposes_all_commands() -> None:
    help_text = build_parser().format_help()
    for command in ("validate", "check", "freeze", "inspect", "explain"):
        assert command in help_text


def test_validate_policy_and_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, baseline, candidate = write_inputs(tmp_path)
    result = main(
        [
            "validate",
            "--policy",
            str(policy),
            "--bundle",
            str(baseline),
            "--bundle",
            str(candidate),
        ]
    )
    assert result == 0
    assert "2 bundle(s)" in capsys.readouterr().out


def test_check_pass_and_fail_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, baseline, passing = write_inputs(tmp_path / "pass", candidate_gain=64.5)
    pass_out = tmp_path / "pass-artifacts"
    assert (
        main(
            [
                "check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(passing),
                "--out",
                str(pass_out),
            ]
        )
        == 0
    )
    assert (pass_out / "junit.xml").is_file()

    policy, baseline, failing = write_inputs(tmp_path / "fail", candidate_gain=58.0)
    fail_out = tmp_path / "fail-artifacts"
    assert (
        main(
            [
                "check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(failing),
                "--out",
                str(fail_out),
            ]
        )
        == 1
    )
    assert "PASS" in capsys.readouterr().out
    assert json.loads((fail_out / "report.json").read_text(encoding="utf-8"))["passed"] is False


def test_freeze_and_refuse_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, _baseline, candidate = write_inputs(tmp_path)
    target = tmp_path / "frozen.json"
    args = [
        "freeze",
        "--policy",
        str(policy),
        "--candidate",
        str(candidate),
        "--out",
        str(target),
    ]
    assert main(args) == 0
    assert "Frozen" in capsys.readouterr().out
    assert main(args) == 3
    assert "refusing" in capsys.readouterr().err
    assert main([*args, "--force"]) == 0


def test_inspect_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, _baseline, candidate = write_inputs(tmp_path)
    base_args = [
        "inspect",
        "--policy",
        str(policy),
        "--bundle",
        str(candidate),
    ]
    assert main(base_args) == 0
    assert "Bundle coverage: COMPLETE" in capsys.readouterr().out
    assert main([*base_args, "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["case_count"] == 1
    assert output["metrics"][0]["name"] == "gain"


def test_explain_filters_metric_and_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy, baseline, candidate = write_inputs(tmp_path, candidate_gain=58.0)
    out = tmp_path / "artifacts"
    assert (
        main(
            [
                "check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--out",
                str(out),
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        main(
            [
                "explain",
                "--report",
                str(out / "report.json"),
                "--metric",
                "gain",
                "--case",
                "process=tt",
                "--case",
                "vdd=1.0",
            ]
        )
        == 0
    )
    assert "status: spec_fail" in capsys.readouterr().out


@pytest.mark.parametrize(
    "case_filter",
    ["bad", "vdd=[]", "vdd=null", "vdd=NaN", "vdd=Infinity", "vdd=1e999"],
)
def test_explain_rejects_bad_case_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case_filter: str
) -> None:
    policy, baseline, candidate = write_inputs(tmp_path)
    out = tmp_path / "artifacts"
    assert (
        main(
            [
                "check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["explain", "--report", str(out / "report.json"), "--case", case_filter]) == 2
    assert "input error" in capsys.readouterr().err


def test_explain_reports_no_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy, baseline, candidate = write_inputs(tmp_path)
    out = tmp_path / "artifacts"
    main(
        [
            "check",
            "--policy",
            str(policy),
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--out",
            str(out),
        ]
    )
    capsys.readouterr()
    result = main(["explain", "--report", str(out / "report.json"), "--metric", "missing"])
    assert result == 2
    assert "no report decision" in capsys.readouterr().err


def test_explain_rejects_duplicate_case_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy, baseline, candidate = write_inputs(tmp_path)
    out = tmp_path / "artifacts"
    assert (
        main(
            [
                "check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "explain",
                "--report",
                str(out / "report.json"),
                "--case",
                "vdd=1.0",
                "--case",
                "VDD=1.0",
            ]
        )
        == 2
    )
    assert "duplicate" in capsys.readouterr().err


def test_explain_is_safe_under_ascii_stdout(tmp_path: Path) -> None:
    data = run_compare(65.0, 65.0).to_dict()
    data["results"][0]["case"]["process"] = "😀"
    report_path = report_from_dict(data).write_json(tmp_path / "unicode-report.json")
    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "regressistor",
            "explain",
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="ascii",
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )
    assert completed.returncode == 0
    assert "Traceback" not in completed.stderr
    assert "\\U0001f600" in completed.stdout


def test_cli_invalid_input_and_output_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = main(["validate", "--policy", str(tmp_path / "missing.toml")])
    assert result == 2
    assert "input error" in capsys.readouterr().err

    policy, baseline, candidate = write_inputs(tmp_path / "valid")
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("occupied", encoding="utf-8")
    result = main(
        [
            "check",
            "--policy",
            str(policy),
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--out",
            str(output_file),
        ]
    )
    assert result == 3
    assert "output error" in capsys.readouterr().err


def test_cli_contains_toml_parser_recursion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "deep-policy.toml"
    policy.write_text("value = " + "[" * 2_000 + "0" + "]" * 2_000, encoding="utf-8")
    assert main(["validate", "--policy", str(policy)]) == 2
    captured = capsys.readouterr()
    assert "input error" in captured.err
    assert "Traceback" not in captured.err
