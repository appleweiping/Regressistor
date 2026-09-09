from pathlib import Path
from runpy import run_path

import pytest

from regressistor import compare_waveforms, load_waveform, load_waveform_policy
from regressistor.cli import main


def test_original_example_runs_and_protects_its_generated_inputs(tmp_path: Path) -> None:
    generate = run_path("examples/waveform/generate.py")["generate"]
    destination = tmp_path / "example"
    generate(destination)
    policy = destination / "policy.waveform.json"
    baseline = destination / "baseline.waveform.json"
    candidate = destination / "candidate.waveform.json"
    result = compare_waveforms(
        load_waveform(baseline), load_waveform(candidate), load_waveform_policy(policy)
    )
    assert result.status == "fail"
    assert result.evaluated_breakpoints == 3
    assert result.failing_breakpoints == 1
    generated = {path.name: path.read_bytes() for path in destination.iterdir()}
    report = tmp_path / "cli-report.json"
    assert (
        main(
            [
                "waveform-check",
                "--policy",
                str(policy),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--out",
                str(report),
            ]
        )
        == 1
    )
    assert report.read_bytes() == generated["comparison.json"]
    with pytest.raises(FileExistsError):
        generate(destination)
    assert generated == {path.name: path.read_bytes() for path in destination.iterdir()}
