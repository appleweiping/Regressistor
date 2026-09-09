"""Generate an original zero-crossing regression example in a fresh directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from regressistor import (
    Waveform,
    WaveformPolicy,
    compare_waveforms,
    write_waveform,
    write_waveform_comparison,
    write_waveform_policy,
)


def generate(destination: Path) -> None:
    # Refuse even an existing empty directory: a failed/partial example cannot
    # be mistaken for a fully regenerated set of content-bound artifacts.
    destination.mkdir(parents=True, exist_ok=False)
    baseline = Waveform((0, 1), (-1, 1), "s", "V", "zero-crossing-baseline")
    candidate = Waveform((0, 1), (-1.125, 0.875), "s", "V", "zero-crossing-candidate")
    policy = WaveformPolicy(relative=0.25, grid="linear")
    result = compare_waveforms(baseline, candidate, policy)
    if result.status != "fail" or result.failing_breakpoints != 1:
        raise AssertionError("zero-crossing regression oracle changed")
    write_waveform(baseline, destination / "baseline.waveform.json")
    write_waveform(candidate, destination / "candidate.waveform.json")
    write_waveform_policy(policy, destination / "policy.waveform.json")
    write_waveform_comparison(result, destination / "comparison.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    generate(parser.parse_args().out)
