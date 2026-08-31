import json
from hashlib import sha256
from pathlib import Path

from regressistor.bundle import load_bundle
from regressistor.gate import compare
from regressistor.policy import load_policy

ROOT = Path(__file__).parents[1]
PRODUCER_IDENTITY = {
    "distribution": "simcairn",
    "version": "0.2.0",
    "package_tree_algorithm": "simcairn-python-source-tree/1",
    "package_tree_sha256": "8abfa550a6b576a176d3d81bb251d6d1cdefc440a32a6cf75911e4ab3ad702d0",
    "validation_implementation_sha256": "90e27f73e3261bc58646bb49d8310d524afa29899a7a5523d789f383329ef4c4",
    "adapter_implementation_sha256": "aaa76bb2654444ba0b5e8aee7c1e5908f9146c705c84aa6f766168d45eda6c4e",
}


def test_simcairn_offline_and_ngspice_fixtures_obey_machine_contract() -> None:
    policy = load_policy(ROOT / "benchmarks" / "rc-pvt-policy.toml")
    for name in ("simcairn-offline-golden.json", "simcairn-ngspice-42.json"):
        bundle = load_bundle(ROOT / "benchmarks" / "fixtures" / name)
        assert len(bundle.points) == 32
        report = compare(policy, bundle, bundle)
        assert report.passed is True


def test_simcairn_fixture_manifest_hashes_are_current() -> None:
    manifest = json.loads((ROOT / "benchmarks" / "manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        path = ROOT / "benchmarks" / source["path"]
        assert sha256(path.read_bytes()).hexdigest() == source["sha256"]
        bundle = load_bundle(path)
        assert bundle.run["contract"] == "regressistor.measurement-bundle/2"
        assert bundle.run["producer_identity"] == PRODUCER_IDENTITY
        provenance = source.get("provenance", {})
        assert bundle.run["aggregate_activity_id"] == provenance["aggregate_activity_id"]
        if "evidence_manifest_path" in provenance:
            evidence = ROOT / "benchmarks" / provenance["evidence_manifest_path"]
            assert (
                sha256(evidence.read_bytes()).hexdigest() == provenance["evidence_manifest_sha256"]
            )
            evidence_data = json.loads(evidence.read_text(encoding="utf-8"))
            assert evidence_data["producer_identity"] == PRODUCER_IDENTITY
    for key in ("schema", "compatibility_schema"):
        schema = manifest[key]
        assert sha256((ROOT / schema["path"]).read_bytes()).hexdigest() == schema["sha256"]
