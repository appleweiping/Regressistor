"""Original source-bound waveform workloads with independently known counts.

Run one size/profile per fresh process. Peak memory is the process lifetime OS
high-water mark, including imports and retained input traces; it is not incremental
comparator allocation. Timing is informational and has no CI pass threshold.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
import time
from fractions import Fraction
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import regressistor
from regressistor import Waveform, WaveformPolicy, compare_waveforms, waveform_comparison_data


def _peak_process_bytes() -> tuple[int, str]:
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                *[
                    (name, ctypes.c_size_t)
                    for name in (
                        "PeakWorkingSetSize",
                        "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage",
                        "PagefileUsage",
                        "PeakPagefileUsage",
                    )
                ],
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return counters.PeakWorkingSetSize, "Windows PeakWorkingSetSize"
    if sys.platform in {"linux", "darwin"}:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak) * (1024 if sys.platform == "linux" else 1), "getrusage ru_maxrss"
    raise RuntimeError("OS peak-memory units are not specified for this platform")


def _package_hash() -> str:
    if regressistor.__file__ is None:
        raise RuntimeError("cannot locate the imported package")
    root = Path(regressistor.__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        relative, payload = path.relative_to(root).as_posix().encode(), path.read_bytes()
        for part in (relative, payload):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()


def run(points: int, profile: str) -> dict[str, object]:
    if type(points) is not int or not 2 <= points <= 100_000:
        raise ValueError("points must be an integer in [2, 100000]")
    if type(profile) is not str or profile not in {"exact", "floor-crossings"}:
        raise ValueError("profile must be exact or floor-crossings")
    started = time.perf_counter()
    axis = tuple(float(index) for index in range(points))
    values = tuple(float(2 * (index % 2) - 1) for index in range(points))
    baseline = Waveform(axis, values, "s", "V", "original-alternating-linear-baseline")
    candidate = Waveform(
        axis, tuple(value + 0.125 for value in values), "s", "V", "original-offset-candidate"
    )
    policy = (
        WaveformPolicy(absolute=0.125)
        if profile == "exact"
        else WaveformPolicy(relative=0.25, relative_floor=0.25, grid="linear")
    )
    constructed = time.perf_counter()
    result = compare_waveforms(baseline, candidate, policy)
    compared = time.perf_counter()
    # Every knot passes. Each segment crosses -floor and +floor, where the
    # excess is exactly 1/16 V. Counts follow directly from this construction.
    expected = {
        "evaluated_breakpoints": points if profile == "exact" else 3 * points - 2,
        "failing_breakpoints": 0 if profile == "exact" else 2 * (points - 1),
        "status": "pass" if profile == "exact" else "fail",
        "maximum_excess": Fraction(0) if profile == "exact" else Fraction(1, 16),
        "maximum_excess_axis": Fraction(0) if profile == "exact" else Fraction(3, 8),
    }
    for field in ("evaluated_breakpoints", "failing_breakpoints", "status"):
        if getattr(result, field) != expected[field]:
            raise AssertionError(f"independent waveform {field} oracle failed")
    if (
        result.maximum_excess.excess != expected["maximum_excess"]
        or result.maximum_excess.axis != expected["maximum_excess_axis"]
        or result.maximum_deviation.deviation != Fraction(1, 8)
        or not result.full_domain_covered
    ):
        raise AssertionError("independent waveform maximum/domain oracle failed")
    report = waveform_comparison_data(result)
    report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    serialized = time.perf_counter()
    peak, memory_method = _peak_process_bytes()
    return {
        "schema_version": 1,
        "benchmark": "regressistor-original-waveform-scale-v1",
        "profile": profile,
        "points_per_trace": points,
        "distribution_version": version("regressistor"),
        "package_tree_sha256": _package_hash(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "seconds": {
            "construct": constructed - started,
            "compare": compared - constructed,
            "report": serialized - compared,
            "total": serialized - started,
        },
        "memory": {"peak_process_bytes": peak, "method": memory_method},
        "baseline_id": result.baseline_id,
        "candidate_id": result.candidate_id,
        "policy": report["policy"],
        "evaluated_breakpoints": result.evaluated_breakpoints,
        "failing_breakpoints": result.failing_breakpoints,
        "retained_diagnostics": len(result.diagnostics),
        "report_bytes": len(report_bytes),
        "report_sha256": sha256(report_bytes).hexdigest(),
        "observations_sha256": result.observations_sha256,
        "status": result.status,
        "all_analytic_oracles_passed": True,
        "scope": "API construction/comparison/report; no wire loading or simulator execution",
        "timing_policy": "Informational; fresh process per run; not a CI timing threshold.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=100_000)
    parser.add_argument("--profile", choices=("exact", "floor-crossings"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; use a fresh result path")
    report = run(args.points, args.profile)
    # x mode is also the final no-clobber check after a potentially long run.
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
