"""Lock the required real-simulator CI path and execute its actual Python guard."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import venv
from pathlib import Path

import pytest


def _job() -> str:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    marker = "  waveform-simulator-integration:\n"
    assert workflow.count(marker) == 1
    return re.split(r"\n  [a-z][a-z0-9-]*:\n", workflow.split(marker, 1)[1])[0]


def _interpreter_guard() -> str:
    job = _job()
    command = "\"$RUNNER_TEMP/waveform-env/bin/python\" -I - <<'PY'\n"
    assert job.count(command) == 1
    body = job.split(command, 1)[1].split("          PY\n", 1)[0]
    return textwrap.dedent(body)


def test_actual_simulator_job_is_unconditional_read_only_and_has_pinned_inputs() -> None:
    header = Path(".github/workflows/ci.yml").read_text(encoding="utf-8").split("jobs:", 1)[0]
    assert "  push:\n" in header and "  pull_request:\n" in header
    assert "paths" not in header and "branches" not in header
    assert "pull_request_target" not in header
    job = _job()
    assert "runs-on: ubuntu-24.04\n" in job
    assert "timeout-minutes: 15\n" in job
    assert "ngspice=42+ds-3build1" in job
    assert "42+ds-3build1'" in job
    assert job.count("820658317b0b54035208da41936fd6871ce924036e5ea4113a4168b821b7fc45") == 2
    assert "59f2c73554a0318b817ff02f8fdd7d82c34911ef7d9752f111a64d9244cc1860" in job
    assert "https://github.com/appleweiping/SpiceTrellis/releases/download/v0.6.0/" in job
    assert "--proto '=https'" in job and "--fail --location" in job
    assert "--max-filesize 4194304" in job
    assert "sha256sum --check --strict" in job
    assert 'python-version: "3.11.16"' in job
    assert "continue-on-error" not in job and "needs:" not in job
    assert re.findall(r"^\s+if: (.*)$", job, re.MULTILINE) == ["always()"]
    assert "GH_TOKEN" not in job and "secrets." not in job
    assert "permissions:" not in job  # Inherit the workflow's contents:read only.
    assert "persist-credentials: false" in job
    for action in re.findall(r"uses: (\S+)", job):
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action)
    assert job.index("sha256sum --check --strict", job.index("curl --fail")) < job.index(
        "-m pip install"
    )


def test_harness_and_guard_use_the_same_isolated_interpreter_outside_the_checkout() -> None:
    job = _job()
    assert job.count("working-directory: ${{ runner.temp }}") == 2
    assert '"$RUNNER_TEMP/waveform-env/bin/python" -I \\\n' in job
    assert '"$GITHUB_WORKSPACE/benchmarks/waveform_simulator_oracle.py"' in job
    assert '--output "$RUNNER_TEMP/waveform-oracle-run"' in job
    assert '"$RUNNER_TEMP/waveform-env/bin/python" -I -m pip install' in job
    assert '--no-deps "$wheel"' in job
    assert '--no-deps --editable "$GITHUB_WORKSPACE"' in job
    assert "uv run" not in job and "PYTHONPATH" not in job
    assert 'report.json")" -le 8388608' in job
    assert "waveform-oracle-run/*/stdout.txt" in job
    assert "waveform-oracle-run/*/stderr.txt" in job
    assert "waveform-oracle-run/*/ngspice.log" in job
    assert "waveform-oracle-run/**" not in job


@pytest.fixture
def guard_environment(tmp_path: Path):
    environment = tmp_path / "waveform-env"
    venv.EnvBuilder(with_pip=False).create(environment)
    if sys.platform == "win32":
        python = environment / "Scripts/python.exe"
        site_packages = environment / "Lib/site-packages"
    else:
        python = environment / "bin/python"
        site_packages = (
            environment
            / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
    source = tmp_path / "checkout" / "src"
    source.joinpath("regressistor").mkdir(parents=True)
    source.joinpath("regressistor/__init__.py").write_text(
        "# original import-boundary fixture\n", encoding="ascii"
    )
    site_packages.joinpath("regressistor-source.pth").write_text(
        str(source) + "\n", encoding="utf-8"
    )
    site_packages.joinpath("spicetrellis").mkdir()
    site_packages.joinpath("spicetrellis/__init__.py").write_text(
        "# original wheel-boundary fixture\n", encoding="ascii"
    )
    metadata = site_packages / "spicetrellis-0.6.0.dist-info"
    metadata.mkdir()
    metadata.joinpath("METADATA").write_text(
        "Metadata-Version: 2.1\nName: spicetrellis\nVersion: 0.6.0\n", encoding="ascii"
    )
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_WORKSPACE": str(source.parent)}
    return python, environment, metadata, env


@pytest.mark.parametrize(
    "failure",
    [None, "wrong_prefix", "wrong_source", "wrong_version", "not_isolated", "shadow_spice"],
)
def test_actual_workflow_heredoc_accepts_only_the_expected_import_boundary(
    guard_environment, tmp_path: Path, failure: str | None
) -> None:
    python, _environment, metadata, env = guard_environment
    flags = ["-I"]
    if failure == "wrong_prefix":
        env["RUNNER_TEMP"] = str(tmp_path / "other-prefix")
    elif failure == "wrong_source":
        env["GITHUB_WORKSPACE"] = str(tmp_path / "other-checkout")
    elif failure == "wrong_version":
        metadata.joinpath("METADATA").write_text(
            "Metadata-Version: 2.1\nName: spicetrellis\nVersion: 0.5.0\n", encoding="ascii"
        )
    elif failure == "not_isolated":
        flags = []
    elif failure == "shadow_spice":
        shadow = tmp_path / "external-spice"
        shadow.joinpath("spicetrellis").mkdir(parents=True)
        shadow.joinpath("spicetrellis/__init__.py").write_text(
            "# outside the wheel environment\n", encoding="ascii"
        )
        metadata.parent.joinpath("shadow-spice.pth").write_text(
            f"import sys; sys.path.insert(0, {str(shadow)!r})\n", encoding="utf-8"
        )
    result = subprocess.run(
        [str(python), *flags, "-"],
        input=_interpreter_guard(),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert (result.returncode == 0) is (failure is None), result.stdout + result.stderr
    assert "ModuleNotFoundError" not in result.stderr
