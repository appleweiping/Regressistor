"""Verify the frozen OpenRAM Liberty interoperability contract used by CI."""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path

from regressistor.liberty import load_liberty_nldm

EXPECTED_SHA256 = "6b47ce23059f842957aa470c71d1a684680d5df9e98f48736e7c09588f9bf4e5"
EXPECTED_METRICS = {
    "cell_fall",
    "cell_rise",
    "fall_constraint",
    "fall_power",
    "fall_transition",
    "rise_constraint",
    "rise_power",
    "rise_transition",
}


def verify(path: Path) -> None:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"OpenRAM Liberty SHA-256 mismatch: {digest}")

    conversion = load_liberty_nldm(path)
    metrics = {source.metric for source in conversion.metric_sources}
    bus_ports = {
        (str(case["bus"]), str(case["port"]))
        for point in conversion.bundle.points
        if "bus" in (case := dict(point.case))
    }
    observed = {
        "points": len(conversion.bundle.points),
        "metric_sources": len(conversion.metric_sources),
        "metrics": metrics,
        "bus_ports": bus_ports,
    }
    expected = {
        "points": 192,
        "metric_sources": 32,
        "metrics": EXPECTED_METRICS,
        "bus_ports": {
            ("addr0", "addr0[3:0]"),
            ("dout0", "dout0[1:0]"),
            ("din0", "din0[1:0]"),
        },
    }
    if observed != expected:
        raise RuntimeError(f"OpenRAM Liberty invariant mismatch: {observed!r}")
    # The locked file declares 1 pF and 1 V; clk0 values on lines 285–289
    # therefore represent energy in pJ, regardless of its 1 mW leakage unit.
    for metric, raw_value in (("rise_power", 0.3069977), ("fall_power", 0.3686680)):
        matches = [
            point.metrics[metric]
            for point in conversion.bundle.points
            if metric in point.metrics
            and dict(point.case).get("port") == "clk0"
            and dict(point.case).get("when") == "!csb0 & !web0"
        ]
        if (
            len(matches) != 1
            or matches[0].unit != "J"
            or not math.isclose(matches[0].value, raw_value * 1e-12, rel_tol=1e-12, abs_tol=0)
        ):
            raise RuntimeError(f"OpenRAM Liberty internal-energy mismatch: {metric}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("liberty", type=Path)
    args = parser.parse_args()
    verify(args.liberty)
    print("OpenRAM Liberty interoperability contract verified: 192 points, 32 sources")


if __name__ == "__main__":
    main()
