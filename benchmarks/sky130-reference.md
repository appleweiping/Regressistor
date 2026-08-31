# SKY130 ngspice measurement reference

`fixtures/simcairn-sky130-ngspice42.json` is a pure measurement bundle produced by SimCairn from a
27-point Cartesian sweep of `CORNER = {tt, ss, ff}`, `VDD = {1.62, 1.80, 1.98}`, and
`TEMP_C = {-40, 27, 125}`. The run used ngspice-42 and the Ciel SKY130A distribution pinned to
revision `1689ac3f2dc763876eaf967227c7dfe831b031ae`.

The generation provenance was content-bound as follows:

- SimCairn aggregate activity: `0b8d0eee0c37e29f7520468da71943d6578998a1b6d545d3663f0918e290a7af`.
- Sizing decision SHA-256: `b2c8682f543233727c395d9ce5b260a3201c9ebbaf71b97085add08eaf6393d8`.
- Selected PDK model-set SHA-256: `3e9aafbfbc52f83591793b1f1c31c1d0097b90565df9333376b13ebe1f1e6d29`.
- Published measurement bundle SHA-256: `d00dd01c6d3b662248d7848f3f0de6642c0e056682dc14a07d227ab7a7467a87`.

To reproduce the data, obtain that pinned Ciel revision independently, configure the SimCairn
SKY130 common-source workflow using the recorded sizing decision and PDK hashes, run its 27-point
manifest with ngspice-42, and collect the aggregate `regression-bundle.json`. Regressistor then
validates it with:

```console
regressistor validate --policy benchmarks/sky130-policy.toml --bundle benchmarks/fixtures/simcairn-sky130-ngspice42.json
regressistor check --policy benchmarks/sky130-policy.toml --baseline benchmarks/fixtures/simcairn-sky130-ngspice42.json --candidate benchmarks/fixtures/simcairn-sky130-ngspice42.json --out sky130-reference-report
```

No PDK model, archive, simulator binary, or generated deck is vendored here. This is simulator
output, not silicon measurement, model qualification, foundry signoff, or a claim that the circuit
meets a product specification. Self-comparison proves ingestion, complete identities, and stable
replay only. The negative tests deliberately perturb a numeric value and remove a case to prove
that the policy gate rejects meaningful changes.
