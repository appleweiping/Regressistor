# GF180MCU reference boundary

`fixtures/simcairn-gf180-ngspice42.json` is the exact 27-point measurement bundle emitted by
SimCairn from a real ngspice-42 KLU run over `tt`, `ss`, and `ff`, three supply voltages, and three
temperatures. The generating flow pinned the Ciel GF180MCU revision, both official archive hashes,
the installed `nodeinfo.json`, the design wrapper, the device model, the simulator executable, and
the three hashes of the upstream sizing decision. No PDK files or models are redistributed here.

The committed bundle is a reproducibility and interoperability fixture, not measured silicon data
or sign-off evidence. `gf180-policy.toml` checks broad physical sanity and exact units. A bundle
self-comparison proves strict ingestion, complete case identity, and deterministic gate behavior;
it does not establish model accuracy or a performance claim.

```console
regressistor validate --policy benchmarks/gf180-policy.toml --bundle benchmarks/fixtures/simcairn-gf180-ngspice42.json
regressistor check --policy benchmarks/gf180-policy.toml --baseline benchmarks/fixtures/simcairn-gf180-ngspice42.json --candidate benchmarks/fixtures/simcairn-gf180-ngspice42.json --out gf180-reference-report
```
