from __future__ import annotations

import math
from pathlib import Path

import pytest

from regressistor.errors import InputError
from regressistor.model import ContractKind, Direction, MissingAction, Reducer, Severity
from regressistor.policy import load_policy, parse_policy


def valid_policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_keys": ["process", "vdd"],
        "metrics": [
            {
                "name": "gain",
                "unit": "dB",
                "reduce": "min",
                "severity": "warning",
                "contract": {"kind": "min", "limit": 60.0},
                "regression": {
                    "direction": "higher",
                    "absolute_budget": 1.0,
                    "relative_budget": 0.02,
                    "relative_floor": 1.0,
                },
            }
        ],
    }


def test_parses_complete_policy() -> None:
    policy = parse_policy(valid_policy(), source_hash="a" * 64)
    assert policy.case_keys == ("process", "vdd")
    assert policy.source_hash == "a" * 64
    metric = policy.metrics[0]
    assert metric.reducer is Reducer.MIN
    assert metric.severity is Severity.WARNING
    assert metric.contract and metric.contract.kind is ContractKind.MIN
    assert metric.regression and metric.regression.direction is Direction.HIGHER


def test_policy_source_digest_is_strict() -> None:
    with pytest.raises(InputError, match="source_hash"):
        parse_policy(valid_policy(), source_hash="not-a-digest")


def test_load_policy_reads_toml_and_hashes_source(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        "schema_version=1\ncase_keys=['corner']\n"
        "[[metrics]]\nname='power'\nunit='mW'\n"
        "contract={kind='max',limit=2.0}\n",
        encoding="utf-8",
    )
    policy = load_policy(path)
    assert len(policy.source_hash) == 64
    assert policy.metrics[0].contract and policy.metrics[0].contract.upper == 2.0


@pytest.mark.parametrize(
    ("contract", "kind"),
    [
        ({"kind": "min", "limit": 1.0}, ContractKind.MIN),
        ({"kind": "max", "limit": 1.0}, ContractKind.MAX),
        ({"kind": "range", "lower": -1.0, "upper": 1.0}, ContractKind.RANGE),
        ({"kind": "target", "target": 4.0, "tolerance": 0.2}, ContractKind.TARGET),
    ],
)
def test_contract_variants(contract: dict[str, object], kind: ContractKind) -> None:
    data = valid_policy()
    data["metrics"][0]["contract"] = contract  # type: ignore[index]
    assert parse_policy(data).metrics[0].contract.kind is kind  # type: ignore[union-attr]


def test_target_regression_inherits_contract_target() -> None:
    data = valid_policy()
    metric = data["metrics"][0]  # type: ignore[index]
    metric["contract"] = {"kind": "target", "target": 5.0, "tolerance": 0.5}
    metric["regression"] = {"direction": "target", "absolute_budget": 0.1}
    regression = parse_policy(data).metrics[0].regression
    assert regression and regression.target == 5.0


def test_missing_policy_defaults_and_overrides() -> None:
    data = valid_policy()
    data["missing"] = {"candidate_metric": "warning", "baseline_case": "ignore"}
    missing = parse_policy(data).missing
    assert missing.candidate_metric is MissingAction.WARNING
    assert missing.baseline_case is MissingAction.IGNORE
    assert missing.candidate_case is MissingAction.ERROR


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(schema_version=True), "schema_version"),
        (lambda data: data.update(case_keys=[]), "case_keys"),
        (lambda data: data.update(case_keys=["x", "x"]), "duplicates"),
        (lambda data: data.update(extra=True), "unknown fields"),
        (lambda data: data.update(metrics=[]), "at least one"),
        (lambda data: data["metrics"][0].update(name=""), "non-empty"),
        (lambda data: data["metrics"][0].update(unit="bananas"), "unsupported"),
        (lambda data: data["metrics"][0].update(reduce="mode"), "one of"),
        (lambda data: data["metrics"][0].update(severity="fatal"), "one of"),
        (lambda data: data["metrics"][0].update(extra=True), "unknown fields"),
    ],
)
def test_rejects_invalid_policy(mutation: object, message: str) -> None:
    data = valid_policy()
    mutation(data)  # type: ignore[operator]
    with pytest.raises(InputError, match=message):
        parse_policy(data)


def test_rejects_duplicate_metrics() -> None:
    data = valid_policy()
    data["metrics"].append(dict(data["metrics"][0]))  # type: ignore[union-attr,index]
    with pytest.raises(InputError, match="duplicate metric"):
        parse_policy(data)


@pytest.mark.parametrize(
    ("contract", "message"),
    [
        ({"kind": "range", "lower": 2.0, "upper": 1.0}, "must not exceed"),
        ({"kind": "target", "target": 1.0, "tolerance": -1.0}, "at least"),
        ({"kind": "min", "limit": True}, "numeric"),
        ({"kind": "max", "limit": math.inf}, "finite"),
        ({"kind": "unknown", "limit": 1.0}, "one of"),
        ({"kind": "min", "limit": 1.0, "target": 2.0}, "unknown fields"),
    ],
)
def test_rejects_invalid_contracts(contract: dict[str, object], message: str) -> None:
    data = valid_policy()
    data["metrics"][0]["contract"] = contract  # type: ignore[index]
    with pytest.raises(InputError, match=message):
        parse_policy(data)


def test_rejects_target_regression_without_target() -> None:
    data = valid_policy()
    data["metrics"][0]["contract"] = {"kind": "min", "limit": 1.0}  # type: ignore[index]
    data["metrics"][0]["regression"] = {"direction": "target"}  # type: ignore[index]
    with pytest.raises(InputError, match="target"):
        parse_policy(data)


def test_rejects_target_field_for_non_target_regression() -> None:
    data = valid_policy()
    data["metrics"][0]["regression"] = {  # type: ignore[index]
        "direction": "higher",
        "absolute_budget": 1.0,
        "target": 2.0,
    }
    with pytest.raises(InputError, match="unknown fields"):
        parse_policy(data)


def test_rejects_metric_without_any_gate() -> None:
    data = valid_policy()
    metric = data["metrics"][0]  # type: ignore[index]
    metric.pop("contract")
    metric.pop("regression")
    with pytest.raises(InputError, match="must define"):
        parse_policy(data)


def test_rejects_invalid_toml_and_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="cannot read"):
        load_policy(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("this = [", encoding="utf-8")
    with pytest.raises(InputError, match="invalid TOML"):
        load_policy(broken)


def test_policy_rejects_oversized_and_deep_documents(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.toml"
    oversized.write_bytes(b" " * 1_048_577)
    with pytest.raises(InputError, match="byte input limit"):
        load_policy(oversized)

    data = valid_policy()
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    data["unknown"] = nested
    with pytest.raises(InputError, match="complexity limits"):
        parse_policy(data)


def test_policy_rejects_parser_recursion_and_non_string_keys(tmp_path: Path) -> None:
    deeply_nested = tmp_path / "parser-recursion.toml"
    deeply_nested.write_text("value = " + "[" * 2_000 + "0" + "]" * 2_000, encoding="utf-8")
    with pytest.raises(InputError, match="invalid TOML"):
        load_policy(deeply_nested)

    data = valid_policy()
    data[1] = "not-toml"  # type: ignore[index]
    with pytest.raises(InputError, match="keys must be strings"):
        parse_policy(data)


@pytest.mark.parametrize("field", ["case_key", "metric"])
def test_policy_rejects_control_characters_and_casefold_collisions(field: str) -> None:
    data = valid_policy()
    if field == "case_key":
        data["case_keys"] = ["VDD", "vdd"]
    else:
        data["metrics"].append(dict(data["metrics"][0], name="GAIN"))  # type: ignore[union-attr,index]
    with pytest.raises(InputError, match="duplicate"):
        parse_policy(data)

    data = valid_policy()
    data["metrics"][0]["name"] = "\x1b[31mGAIN"  # type: ignore[index]
    with pytest.raises(InputError, match="portable"):
        parse_policy(data)
