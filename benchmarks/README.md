# Benchmarks

This directory demonstrates the machine contract emitted by SimCairn. The
offline fixture is project-generated analytic data and is not a simulator or
silicon result. The ngspice fixture was produced by ngspice-42 under WSL Ubuntu
from SimCairn's `examples/rc_pvt/ngspice.toml` workflow.

Validate and gate either fixture:

```console
regressistor validate --policy benchmarks/rc-pvt-policy.toml --bundle benchmarks/fixtures/simcairn-ngspice-42.json
regressistor check --policy benchmarks/rc-pvt-policy.toml --baseline benchmarks/fixtures/simcairn-ngspice-42.json --candidate benchmarks/fixtures/simcairn-ngspice-42.json --out benchmark-report
```

Self-comparison proves contract ingestion and deterministic replay, not circuit
accuracy. Meaningful regression use freezes an independently reviewed prior run
and compares a new run from the same simulator/PDK environment.

The additional [SKY130 reference](sky130-reference.md) contains 27 pure measurement cases from a
pinned Ciel SKY130A/ngspice-42 run. No PDK models are included. Its self-comparison is a format and
identity check, not silicon evidence or performance signoff.

The additional [GF180MCU reference](gf180-reference.md) provides the same strict 27-case boundary
for a pinned Ciel GF180MCU/ngspice-42 run, with no redistributed PDK models.
Exact copies of the three generating SimCairn evidence manifests are retained under `evidence/`;
their hashes are bound by `manifest.json` alongside each emitted measurement bundle.

## Scaling benchmark

Run `python benchmarks/benchmark.py --points 10,100,1000 --repetitions 5` to time strict loading,
index construction, and self-comparison separately. JSON includes environment and workload hashes
plus decision-count invariants. It also records the installed distribution version and independent
content hashes for the imported Python package tree and executing harness. Timing is descriptive
only: compare runs on the same host and interpreter, and never treat a duration as a CI threshold
or speed guarantee.
