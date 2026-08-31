from __future__ import annotations

import json
import runpy
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import regressistor
from regressistor.bundle import freeze_bundle, load_bundle
from tests.helpers import bundle_dict

ROOT = Path(__file__).parents[1]


def _imported_tree_sha256() -> str:
    assert regressistor.__file__ is not None
    root = Path(regressistor.__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def test_public_measurement_schemas_are_valid_and_accept_their_contracts() -> None:
    schema_v1 = json.loads(
        (ROOT / "docs" / "schemas" / "measurement-bundle-1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema_v1)
    Draft202012Validator(schema_v1).validate(bundle_dict())

    schema_v2 = json.loads(
        (ROOT / "docs" / "schemas" / "measurement-bundle-2.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema_v2)
    validator = Draft202012Validator(schema_v2)
    for name in (
        "simcairn-offline-golden.json",
        "simcairn-ngspice-42.json",
        "simcairn-sky130-ngspice42.json",
        "simcairn-gf180-ngspice42.json",
    ):
        fixture = json.loads((ROOT / "benchmarks" / "fixtures" / name).read_text(encoding="utf-8"))
        assert list(validator.iter_errors(fixture)) == []


def test_public_schema_accepts_a_frozen_bundle_that_round_trips(tmp_path: Path) -> None:
    schema = json.loads(
        (ROOT / "docs" / "schemas" / "measurement-bundle-2.schema.json").read_text(encoding="utf-8")
    )
    source = load_bundle(ROOT / "benchmarks" / "fixtures" / "simcairn-ngspice-42.json")
    frozen_path = freeze_bundle(source, tmp_path / "frozen.json")
    frozen_data = json.loads(frozen_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(frozen_data)
    reloaded = load_bundle(frozen_path)
    assert reloaded.run["frozen_from_sha256"] == source.source_hash


def test_scaling_benchmark_smoke_has_stable_non_timing_invariants() -> None:
    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            str(ROOT / "benchmarks" / "benchmark.py"),
            "--points",
            "3,9",
            "--repetitions",
            "1",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["schema_version"] == 1
    assert len(result["environment_sha256"]) == 64
    assert len(result["workload_sha256"]) == 64
    assert result["distribution_version"] == regressistor.__version__
    assert result["package_tree_sha256"] == _imported_tree_sha256()
    assert (
        result["harness_sha256"]
        == sha256((ROOT / "benchmarks" / "benchmark.py").read_bytes()).hexdigest()
    )
    assert [item["invariants"] for item in result["results"]] == [
        {"decision_count": 3, "passed": True},
        {"decision_count": 9, "passed": True},
    ]
    assert set(result["results"][0]["stages"]) == {"strict_load", "index", "compare"}


def test_public_manifest_matches_the_full_scaling_workload() -> None:
    benchmark_run = runpy.run_path(str(ROOT / "benchmarks" / "benchmark.py"))["run"]
    result = benchmark_run((10, 100, 1000), 1)
    manifest = json.loads((ROOT / "benchmarks" / "manifest.json").read_text(encoding="utf-8"))
    assert result["workload_sha256"] == manifest["expected"]["scaling_workload_sha256"]
    assert manifest["runtime"] == {
        "distribution_version": result["distribution_version"],
        "package_tree_sha256": result["package_tree_sha256"],
        "harness_sha256": result["harness_sha256"],
    }
    assert [item["invariants"]["decision_count"] for item in result["results"]] == [10, 100, 1000]


def test_scaling_benchmark_rejects_non_integer_parameters() -> None:
    benchmark_run = runpy.run_path(str(ROOT / "benchmarks" / "benchmark.py"))["run"]
    for point_counts in ((1.5,), (True,)):
        with pytest.raises(ValueError, match="point counts"):
            benchmark_run(point_counts, 1)
    for repetitions in (1.5, True):
        with pytest.raises(ValueError, match="repetitions"):
            benchmark_run((1,), repetitions)


def test_scaling_cli_rejects_non_integer_count_without_traceback() -> None:
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(ROOT / "benchmarks" / "benchmark.py"), "--points", "1.5"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "points" in completed.stderr
    assert "Traceback" not in completed.stderr
