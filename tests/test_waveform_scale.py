from runpy import run_path

import pytest


@pytest.mark.parametrize("profile", ["exact", "floor-crossings"])
def test_scale_harness_locks_independent_breakpoint_and_maximum_oracles(profile: str) -> None:
    run = run_path("benchmarks/waveform_scale.py")["run"]
    report = run(8, profile)
    assert report["all_analytic_oracles_passed"] is True
    assert report["evaluated_breakpoints"] == (8 if profile == "exact" else 22)
    assert report["failing_breakpoints"] == (0 if profile == "exact" else 14)
    assert report["memory"]["peak_process_bytes"] > 0


@pytest.mark.parametrize(
    "points,profile", [(True, "exact"), (1, "exact"), (100001, "exact"), (10, "unknown"), (10, [])]
)
def test_scale_preflight_rejects_invalid_workloads(points, profile) -> None:
    run = run_path("benchmarks/waveform_scale.py")["run"]
    with pytest.raises(ValueError):
        run(points, profile)
