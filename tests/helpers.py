from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_TEXT = """\
schema_version = 1
case_keys = ["process", "vdd"]

[missing]
baseline_case = "error"
candidate_case = "error"
baseline_metric = "error"
candidate_metric = "error"

[[metrics]]
name = "gain"
unit = "dB"
reduce = "mean"
severity = "error"
contract = { kind = "min", limit = 60.0 }
regression = { direction = "higher", absolute_budget = 1.0, relative_budget = 0.01 }
"""


def bundle_dict(
    gain: float = 65.0,
    *,
    unit: str = "dB",
    process: str = "tt",
    vdd: float = 1.0,
    run_id: str = "test",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run": {"id": run_id},
        "points": [
            {
                "case": {"process": process, "vdd": vdd},
                "sample": 0,
                "metrics": {"gain": {"value": gain, "unit": unit}},
            }
        ],
    }


def write_inputs(tmp_path: Path, candidate_gain: float = 65.0) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    policy = tmp_path / "policy.toml"
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    policy.write_text(POLICY_TEXT, encoding="utf-8")
    baseline.write_text(json.dumps(bundle_dict(65.0, run_id="base")), encoding="utf-8")
    candidate.write_text(
        json.dumps(bundle_dict(candidate_gain, run_id="candidate")), encoding="utf-8"
    )
    return policy, baseline, candidate
