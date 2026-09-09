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

The current scalar-regression manifest was refreshed by an actual v0.5.0 run;
its [source-bound report](results/waveform-v050-scalar-regression-final-20260909.json)
retains the original 10/100/1000-point workload and unchanged verdict invariants.

## Waveform work and memory

Run each profile in a fresh process and choose a new output filename:

```bash
python benchmarks/waveform_scale.py --points 100000 --profile exact --output exact.json
python benchmarks/waveform_scale.py --points 100000 --profile floor-crossings --output linear.json
```

Both traces contain 100,000 samples in the recorded runs. The original baseline
alternates between -1 and +1 V; the candidate has an exactly representable 0.125 V
offset. Exact mode permits that offset. Linear mode instead uses a 25% relative
budget and 0.25 V floor: every original knot passes, but both floor crossings in
every segment fail. The independently known counts are therefore 299,998 evaluated
breakpoints and 199,998 failing breakpoints, with maximum excess exactly 1/16 V.
Only 16 diagnostic witnesses are retained; the complete evaluation is hash-bound.

| Profile | Evaluated / failing | Comparison time | OS process peak | Evidence |
| --- | ---: | ---: | ---: | --- |
| Exact grid | 100,000 / 0 | 13.5375 s | 40,624,128 bytes | [JSON](results/waveform-scale-v050-exact-100k-windows-final-20260909.json) |
| Linear with floor crossings | 299,998 / 199,998 | 52.4962 s | 40,656,896 bytes | [JSON](results/waveform-scale-v050-floor-100k-windows-final-20260909.json) |

These Windows CPython 3.11.2 results bind Regressistor 0.5.0 imported Python-tree
SHA-256 `7c174ebdfc4f64fdfa24554613c2647dd58575671fc817ec607742319f6cfb2f`
and harness SHA-256 `2fc7c596d9f2b8a899288a714c3546e6c04c7f02f472a2f8a0ed38164c43b034`.
Peak working set is an OS process-lifetime high-water measurement, including
interpreter imports and both retained traces, sampled after construction,
comparison and report serialization. It is not incremental comparator allocation
or a kernel-enforced memory quota. The benchmark uses the API, not wire loading or
a simulator. Timings include concurrent host-load effects and are informational;
they do not establish general performance superiority. Earlier result filenames
without `final` retain their original source hashes and are not relabeled.

The [separate real RC simulator oracle](../docs/waveform-simulator-oracle.md)
checks actual transient and complex-AC projections across unequal grids.

## Liberty NLDM import benchmark

Run `python benchmarks/liberty_nldm_benchmark.py --repetitions 10` to measure strict conversion of
the project-authored synthetic Liberty fixture. The report binds the fixture and harness hashes,
records environment identity, checks point/source-count invariants on every repetition, and labels
all timing as informational. It is parser evidence, not a real-PDK accuracy or performance claim.
