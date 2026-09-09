"""Opt-in, bounded real-ngspice RC waveform and public SpiceTrellis wire oracle.

This is integration evidence, not a simulator backend or a runtime dependency.
It executes only the three original fixed experiments defined below. Parsed raw
and JSON artifacts are data; neither parser is allowed to execute their content.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import regressistor
from regressistor import Waveform, WaveformPolicy, compare_waveforms
from regressistor.waveform_json import (
    dumps_waveform,
    loads_waveform,
    waveform_comparison_data,
)

SPICE_VERSION = "0.6.0"
SPICE_WHEEL_SHA256 = "59f2c73554a0318b817ff02f8fdd7d82c34911ef7d9752f111a64d9244cc1860"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PROCESS_BYTES = 24 * 1024 * 1024
MAX_WHEEL_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
CASES = {
    "nominal-coarse": (1000, "50u", 81),
    "nominal-fine": (1000, "5u", 161),
    "changed-resistance": (2000, "5u", 161),
}


class OracleError(RuntimeError):
    """A fixed experiment or its evidence failed a declared gate."""


def _bytes(path: Path, *, maximum: int = MAX_FILE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OracleError(f"expected a regular, non-symlink file: {path}")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise OracleError(f"file exceeds the {maximum}-byte ceiling: {path.name}")
    return payload


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _package_identity(root: Path) -> dict[str, object]:
    paths = sorted(
        path for path in root.rglob("*") if path.suffix in {".py", ".json"} and path.is_file()
    )
    if not paths or len(paths) > 256:
        raise OracleError("runtime package has an unexpected file count")
    entries = {path.relative_to(root).as_posix(): _sha(_bytes(path)) for path in paths}
    digest = _sha(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii"))
    return {"sha256": digest, "files": entries}


def _spice_identity(wheel: Path) -> dict[str, object]:
    payload = _bytes(wheel, maximum=MAX_WHEEL_BYTES)
    if _sha(payload) != SPICE_WHEEL_SHA256:
        raise OracleError("SpiceTrellis wheel is not the pinned public v0.6.0 artifact")
    try:
        distribution = importlib.metadata.distribution("spicetrellis")
    except importlib.metadata.PackageNotFoundError as exc:
        raise OracleError(
            "install the pinned SpiceTrellis wheel in the harness environment"
        ) from exc
    if distribution.version != SPICE_VERSION:
        raise OracleError("installed SpiceTrellis must be version 0.6.0")
    import spicetrellis

    package_root = Path(spicetrellis.__file__).resolve().parent
    installed_root = Path(distribution.locate_file("spicetrellis")).resolve()
    if package_root != installed_root:
        raise OracleError("imported SpiceTrellis shadows the installed distribution")
    with zipfile.ZipFile(wheel) as archive:
        members = [item for item in archive.infolist() if item.filename.startswith("spicetrellis/")]
        if (
            not members
            or len(members) > 256
            or sum(item.file_size for item in members) > MAX_FILE_BYTES
        ):
            raise OracleError("public wheel package payload exceeds its bounds")
        expected: dict[str, str] = {}
        for item in members:
            if item.is_dir() or ".." in Path(item.filename).parts:
                raise OracleError("wheel contains an unsupported package entry")
            relative = item.filename.removeprefix("spicetrellis/")
            if relative in expected:
                raise OracleError("wheel contains duplicate package entries")
            digest = _sha(archive.read(item))
            if _sha(_bytes(package_root / relative)) != digest:
                raise OracleError(
                    f"installed SpiceTrellis differs from the public wheel: {relative}"
                )
            expected[relative] = digest
    actual = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if actual != set(expected):
        raise OracleError("installed SpiceTrellis has missing or additional package payload files")
    return {"version": SPICE_VERSION, "wheel_sha256": SPICE_WHEEL_SHA256, "payload_files": expected}


def deck(name: str) -> str:
    try:
        resistance, step, points = CASES[name]
    except KeyError as exc:
        raise OracleError("only fixed original RC cases are executable") from exc
    return (
        f"* Regressistor original waveform oracle: {name}\n"
        "Vin drive 0 DC 0 AC 1 PULSE(0 1 1m 10u 10u 10m 20m)\n"
        f"Rfilter drive out {resistance}\n"
        "Cfilter out 0 1u\n"
        ".options reltol=1e-7 abstol=1e-12 vntol=1e-9\n"
        ".control\nset filetype=ascii\nset numdgt=17\n"
        f"tran {step} 6m 0 {step}\n"
        "write transient.raw v(out)\n"
        f"ac lin {points} 0 4000\n"
        "write ac.raw v(out)\nquit\n.endc\n.end\n"
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _run(
    command: list[str], directory: Path, expected_outputs: tuple[str, ...]
) -> dict[str, object]:
    """Bound wall time and polled/final file sizes; always reap the direct child."""
    watched = tuple(directory / name for name in ("stdout.txt", "stderr.txt", *expected_outputs))
    started = time.monotonic()

    def check_size() -> None:
        sizes = [path.stat().st_size if path.exists() else 0 for path in watched]
        if max(sizes, default=0) > MAX_FILE_BYTES or sum(sizes) > MAX_PROCESS_BYTES:
            raise OracleError("simulator output exceeded its file/aggregate byte ceiling")

    with watched[0].open("xb") as stdout, watched[1].open("xb") as stderr:
        process = subprocess.Popen(
            command, cwd=directory, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr
        )
        try:
            while process.poll() is None:
                check_size()
                if time.monotonic() - started > TIMEOUT_SECONDS:
                    raise OracleError("simulator exceeded its wall-time ceiling")
                time.sleep(0.02)
            check_size()
            if process.returncode != 0:
                raise OracleError(f"simulator exited with status {process.returncode}")
        finally:
            _stop(process)
    return {
        "arguments": command[1:],
        "returncode": process.returncode,
        "wall_seconds": time.monotonic() - started,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha(_bytes(path))}
            for path in watched
        },
    }


def rc_transient(time_value: float, tau: float) -> float:
    """Finite-rise RC response, never approximating the input ramp as an ideal step."""
    if not math.isfinite(time_value) or not math.isfinite(tau) or tau <= 0:
        raise OracleError("analytic RC inputs must be finite with positive tau")
    elapsed, rise = time_value - 0.001, 0.00001
    if elapsed <= 0:
        return 0.0
    if elapsed < rise:
        x = elapsed / tau
        # x + expm1(-x) loses relative accuracy near zero. Evaluate its
        # alternating Taylor remainder directly in that narrow interval.
        remainder = (
            math.fsum((-1.0) ** power * x**power / math.factorial(power) for power in range(2, 12))
            if x < 0.001
            else x + math.expm1(-x)
        )
        return tau * remainder / rise
    decay = math.exp(-(elapsed - rise) / tau)
    ramp_remaining = -tau / rise * math.expm1(-rise / tau)
    return -math.expm1(-(elapsed - rise) / tau) + (1 - ramp_remaining) * decay


def _trace(plot: Any, analysis: str, component: str) -> Waveform:
    if analysis not in {"transient", "ac"} or component not in {"real", "imaginary"}:
        raise OracleError("the bridge requires an explicit supported analysis and component")
    expected_axis = "time" if analysis == "transient" else "frequency"
    expected_encoding = "real" if analysis == "transient" else "complex"
    if plot.encoding != expected_encoding or plot.analysis != (
        "Transient Analysis" if analysis == "transient" else "AC Analysis"
    ):
        raise OracleError("raw plot analysis/encoding differs from the fixed experiment")
    names = tuple(variable.name for variable in plot.variables)
    if names != (expected_axis, "v(out)") or tuple(v.quantity for v in plot.variables) != (
        expected_axis,
        "voltage",
    ):
        raise OracleError("raw axis/vector names or physical quantities are unexpected")
    if plot.dimensions != (plot.points,) or not 2 <= plot.points <= 100_000:
        raise OracleError("raw plot must be a bounded one-dimensional trajectory")
    axis, values = plot.columns
    if any(item.imag != 0 for item in axis):
        raise OracleError("complex frequency/time axes are not scalar real coordinates")
    if analysis == "transient" and component != "real":
        raise OracleError("transient encoding does not provide an imaginary component")
    samples = tuple(item.real if component == "real" else item.imag for item in values)
    return Waveform(
        tuple(item.real for item in axis),
        samples,
        "s" if analysis == "transient" else "Hz",
        "V",
        f"{analysis}-{component}",
    )


def _bridge(path: Path, analysis: str) -> tuple[dict[str, Waveform], dict[str, object]]:
    from spicetrellis import ResultLimits, dump_result, load_raw, load_result_text

    raw_bytes = _bytes(path)
    limits = ResultLimits(
        max_bytes=MAX_FILE_BYTES,
        max_plots=1,
        max_variables=2,
        max_points=100_000,
        max_cells=200_000,
    )
    parsed = load_raw(path, limits=limits)
    wire = dump_result(parsed, limits=limits)
    restored = load_result_text(wire, limits=limits)
    if parsed.source_sha256 != _sha(raw_bytes) or restored != parsed or len(restored.plots) != 1:
        raise OracleError("SpiceTrellis raw/source/wire identity changed at the result boundary")
    wire_path = path.with_suffix(".spicetrellis.json")
    with wire_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(wire)
    traces = {}
    for component in ("real",) if analysis == "transient" else ("real", "imaginary"):
        trace = _trace(restored.plots[0], analysis, component)
        waveform_wire = dumps_waveform(trace)
        if loads_waveform(waveform_wire) != trace:
            raise OracleError("Regressistor canonical waveform wire changed the projected samples")
        with path.with_suffix(f".{component}.waveform.json").open(
            "x", encoding="ascii", newline="\n"
        ) as stream:
            stream.write(waveform_wire)
        traces[component] = trace
    return traces, {
        "raw_sha256": _sha(raw_bytes),
        "spicetrellis_wire_sha256": _sha(wire.encode("utf-8")),
        "points": restored.plots[0].points,
        "waveform_identities": {name: value.identity for name, value in traces.items()},
    }


def _comparison(
    baseline: Waveform, candidate: Waveform, tolerance: float, *, linear: bool
) -> dict[str, object]:
    result = compare_waveforms(
        baseline,
        candidate,
        WaveformPolicy(absolute=tolerance, grid="linear" if linear else "exact"),
    )
    return waveform_comparison_data(result)


def _analytic(traces: dict[str, Waveform], analysis: str, tau: float) -> dict[str, object]:
    checks = {}
    for component, trace in traces.items():
        expected = tuple(
            rc_transient(x, tau)
            if analysis == "transient"
            else (1 / (1 + 2j * math.pi * x * tau)).real
            if component == "real"
            else (1 / (1 + 2j * math.pi * x * tau)).imag
            for x in trace.axis
        )
        oracle = Waveform(
            trace.axis, expected, trace.axis_unit, "V", f"analytic-{analysis}-{component}"
        )
        tolerance = 0.0005 if analysis == "transient" else 2e-12
        check = _comparison(oracle, trace, tolerance, linear=False)
        if check["passed"] is not True:
            raise OracleError(
                f"{analysis}/{component} exceeds its independent analytical tolerance"
            )
        checks[component] = check
    return checks


def run(
    executable: Path, expected_sha256: str, spice_wheel: Path, output: Path
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise OracleError("the caller must provide an exact lowercase simulator SHA-256")
    executable = executable.resolve(strict=True)
    binary_sha = _sha(_bytes(executable, maximum=64 * 1024 * 1024))
    if binary_sha != expected_sha256:
        raise OracleError("ngspice executable SHA-256 differs from the requested pin")
    spice_identity = _spice_identity(spice_wheel)
    source_root = Path(regressistor.__file__).resolve().parent
    source_identity = _package_identity(source_root)
    output.mkdir(parents=True, exist_ok=False)
    version_directory = output / "version"
    version_directory.mkdir()
    version_run = _run([str(executable), "--version"], version_directory, ())
    version_text = _bytes(version_directory / "stdout.txt").decode("utf-8", errors="strict")
    if re.search(r"\bngspice-42\b", version_text, re.IGNORECASE) is None:
        raise OracleError("this recorded numerical profile requires real ngspice 42")
    records, all_traces = {}, {}
    for name, (resistance, _step, ac_points) in CASES.items():
        directory = output / name
        directory.mkdir()
        deck_bytes = deck(name).encode("ascii")
        with (directory / "experiment.cir").open("xb") as stream:
            stream.write(deck_bytes)
        process = _run(
            [str(executable), "-n", "-b", "-o", "ngspice.log", "experiment.cir"],
            directory,
            ("ngspice.log", "transient.raw", "ac.raw"),
        )
        analyses = {}
        for analysis in ("transient", "ac"):
            traces, boundary = _bridge(directory / f"{analysis}.raw", analysis)
            if (analysis == "ac" and len(traces["real"].axis) != ac_points) or (
                analysis == "transient" and len(traces["real"].axis) < 20
            ):
                raise OracleError("raw point count differs from the fixed experiment profile")
            if traces["real"].axis[0] != 0 or traces["real"].axis[-1] != (
                0.006 if analysis == "transient" else 4000
            ):
                raise OracleError("actual experiment domain differs from its requested endpoints")
            analyses[analysis] = {
                "boundary": boundary,
                "analytic_checks": _analytic(traces, analysis, resistance * 1e-6),
            }
            all_traces[name, analysis] = traces
        records[name] = {"deck_sha256": _sha(deck_bytes), "process": process, "analyses": analyses}
    pairs = {}
    for candidate_name, expected_pass in (("nominal-fine", True), ("changed-resistance", False)):
        for analysis in ("transient", "ac"):
            for component, baseline in all_traces["nominal-coarse", analysis].items():
                candidate = all_traces[candidate_name, analysis][component]
                comparison = _comparison(
                    baseline, candidate, 0.002 if analysis == "transient" else 0.03, linear=True
                )
                if comparison["passed"] is not expected_pass:
                    raise OracleError(
                        f"unexpected physical comparison verdict: {candidate_name}/{analysis}/{component}"
                    )
                pairs[f"{candidate_name}/{analysis}/{component}"] = comparison
    if (
        _package_identity(source_root) != source_identity
        or _spice_identity(spice_wheel) != spice_identity
    ):
        raise OracleError("runtime package changed while the experiment was running")
    if _sha(_bytes(executable, maximum=64 * 1024 * 1024)) != binary_sha:
        raise OracleError("simulator executable changed while experiments ran")
    report: dict[str, object] = {
        "schema": "org.regressistor.waveform-simulator-oracle",
        "version": 1,
        "scope": "three fixed original RC experiments; successful process/log plus sampled analytic checks, not general convergence certification",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "regressistor_version": regressistor.__version__,
        "regressistor_source": source_identity,
        "spicetrellis": spice_identity,
        "ngspice_sha256": binary_sha,
        "ngspice_version": version_text.strip(),
        "version_process": version_run,
        "harness_sha256": _sha(_bytes(Path(__file__))),
        "limits": {
            "wall_seconds_per_process": TIMEOUT_SECONDS,
            "file_bytes": MAX_FILE_BYTES,
            "aggregate_process_bytes": MAX_PROCESS_BYTES,
        },
        "cases": records,
        "comparisons": pairs,
        "all_expected_verdicts": True,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode("ascii") + b"\n"
    with (output / "report.json").open("xb") as stream:
        stream.write(encoded)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--spicetrellis-wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run(args.ngspice, args.expected_sha256, args.spicetrellis_wheel, args.output)
    except (OracleError, OSError, ValueError) as exc:
        parser.exit(1, f"Waveform simulator oracle failed: {exc}\n")
    print(
        f"Validated {len(report['cases'])} actual RC runs and {len(report['comparisons'])} waveform comparisons."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
