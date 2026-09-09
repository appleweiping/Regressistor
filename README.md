# Regressistor

[![CI](https://github.com/appleweiping/Regressistor/actions/workflows/ci.yml/badge.svg)](https://github.com/appleweiping/Regressistor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/appleweiping/Regressistor/actions/workflows/codeql.yml/badge.svg)](https://github.com/appleweiping/Regressistor/actions/workflows/codeql.yml)
[![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

Regressistor is a dependency-free Python 3.11+ command-line tool for analog
specification regression gates. It compares a candidate measurement bundle
against both contractual limits and a frozen, hash-addressed baseline across explicit process,
voltage, temperature, load, or user-defined case keys.

It does not run a simulator, execute policy expressions, infer missing units,
or silently accept a new baseline. This narrow boundary makes results suitable
for local review and CI.

## Install

```bash
python -m pip install .
regressistor --version
```

For development:

```bash
python -m pip install -e ".[dev]"
```

The runtime has no third-party dependencies.

## Liberty NLDM import

Strictly convert characterized Liberty timing and internal-power data into the ordinary
measurement-bundle v1 format:

```bash
regressistor import-liberty \
  --input characterized.lib \
  --out characterized.json
```

The dependency-free importer binds the exact input SHA-256 and source coordinates, preserves PVT,
units, related ports and arc qualifiers, expands scalar/one-axis/two-axis NLDM tables, and rejects
ambiguous or dimensionally inconsistent input under explicit resource limits. It covers delay and
transition tables, internal power, setup/hold constraints, minimum pulse width, and minimum period.
The output is atomic and no-clobber by default; even `--force` cannot overwrite the Liberty input.
See [the Liberty NLDM contract](docs/liberty-nldm.md) for the precise mapping and limits.

## SimCairn interoperability benchmark

Regressistor directly validates SimCairn's producer-bound
`regressistor.measurement-bundle/2` aggregate artifact while retaining the
generic version-1 bundle API for compatibility; no runtime dependency between
the projects is required. The 32-point RC PVT reference, policy,
offline analytic fixture, real ngspice-42 fixture, and pinned SKY130A and GF180MCU
27-point PVT fixtures, commands, and hashes are in
[`benchmarks`](benchmarks). See [`docs/validation.md`](docs/validation.md) for
the evidence boundary.

## Run the example

```bash
regressistor validate \
  --policy examples/opamp/policy.toml \
  --bundle examples/opamp/baseline.json \
  --bundle examples/opamp/candidate.json

regressistor check \
  --policy examples/opamp/policy.toml \
  --baseline examples/opamp/baseline.json \
  --candidate examples/opamp/candidate.json \
  --out artifacts/opamp
```

The synthetic candidate deliberately fails phase margin at the slow,
low-voltage, high-temperature case. The command therefore exits with 1 while
still writing all three report formats.

```bash
regressistor explain \
  --report artifacts/opamp/report.json \
  --metric phase_margin \
  --case process=ss \
  --case vdd=0.9 \
  --case temp_c=125
```

![A real Regressistor CLI gate and focused failure explanation](docs/assets/demo.svg)

## Policy format

Policies are TOML with a fixed schema version:

```toml
schema_version = 1
case_keys = ["process", "vdd", "temp_c"]

[[metrics]]
name = "phase_margin"
unit = "deg"
reduce = "min"
severity = "error"
contract = { kind = "min", limit = 60.0 }
regression = { direction = "higher", absolute_budget = 1.0 }
```

Contract kinds are `min`, `max`, `range`, and `target`. Regression directions
are `higher`, `lower`, and `target`. An adverse regression is allowed when it
is no greater than:

```text
absolute_budget + relative_budget * max(abs(baseline), relative_floor)
```

Repeated points with the same case must have distinct `sample` values. Their
measurements are reduced with `min`, `max`, `mean`, `median`, `p05`, or `p95`
before comparison.

### Regressions inside the measurement noise

A budget stated only as a magnitude has no relationship to how much the metric
moves between identical runs. The same one percent is far too tight for a
metric that scatters three percent and far too loose for one that repeats to a
part in ten thousand: the first fails constantly until somebody switches the
gate off, and the second lets a twenty-sigma shift through in silence.

`noise_budget` adds a second condition in units the measurement supplies
itself:

```toml
[[metrics]]
name = "gain"
unit = "dB"
reduce = "mean"
severity = "error"
regression = { direction = "higher", relative_budget = 0.01, noise_budget = 3.0, noise_min_samples = 6 }
```

A change is a regression only when it exceeds **both** budgets. The magnitude
budget says what is too small to care about; the noise budget says what is too
small to distinguish from scatter. A change that is statistically clear but
immaterial should not block a merge, and neither should one that is material
but indistinguishable from the noise.

The scatter is estimated from repeated `sample` points on both sides and
combined with Welch's standard error, which does not assume the two sides
scatter equally. A baseline frozen from an older simulator, machine or seed set
has no reason to share a variance with the candidate, and pooling them would
report a precision neither has.

#### What it changes

A metric scattering 2% run to run, six repeats a side, a 1% materiality budget
and a 3-standard-error noise budget, over 120 frozen baselines and 80 clean
candidate runs each, with **no real change present**:

| gate | mean false alarms | median | worst decile | worst baseline |
|---|---:|---:|---:|---:|
| magnitude only | 21.0% | 13.8% | 61.3% | 81.2% |
| with `noise_budget` | 0.8% | 0.0% | 2.5% | 15.0% |

Twelve of those 120 baselines make the magnitude-only gate fire on more than
half of all clean runs; none do with the noise budget. That spread matters more
than the average, because the baseline is frozen: its own sampling error is not
re-drawn per comparison, so an unlucky baseline poisons every future run until
somebody re-baselines.

The cost is real and in the other direction. Detection of a genuine shift falls
where the shift is comparable to the noise, and repeats buy it back:

| repeats a side | false alarm | detects a 1.5% shift | detects a 3% shift |
|---:|---:|---:|---:|
| 3 | 2.2% | 9.2% | 25.4% |
| 6 | 1.6% | 6.2% | 38.0% |
| 12 | 0.6% | 17.6% | 78.2% |
| 25 | 0.6% | 36.6% | 99.0% |
| 50 | 0.2% | 79.4% | 100.0% |

Adding repeats improves both columns at once, which is the useful reading: a
noise-gated policy that misses real regressions is asking for more samples, not
for a looser budget.

#### Limits worth knowing

- The budget counts standard errors of the **mean**, so it requires
  `reduce = "mean"`. An extreme such as `max` or `p95` moves far more between
  identical runs than the mean does, and scaling its change by the mean's
  standard error would understate the noise and fire on scatter. A policy that
  asks for both is rejected rather than quietly approximated.
- Below `noise_min_samples` repeats on the thinner side, a standard deviation
  is not an estimate of anything. The noise budget is then skipped and the
  decision message says so. The magnitude test standing alone can only make the
  gate stricter than the policy asked for, never looser.
- A metric that repeats exactly has no measurable scatter, which is the
  ordinary case for a deterministic simulator run several times. Any change in
  it is beyond its scatter, so the noise budget adds nothing and the magnitude
  budget decides.
- The dispersion of both sides is reported on every decision whether or not a
  noise budget is set, since the numbers needed to choose one are exactly the
  ones available before it exists.
- None of this is a hypothesis test. The standardized change is a descriptive
  ratio compared against a threshold the policy states outright, not a p-value.


Audit case and metric coverage before a gate with:

```bash
regressistor inspect \
  --policy policy.toml \
  --bundle candidate.json \
  --format text
```

The JSON format also lists observed units, missing case counts, unique case-key
values, and measurements that have no configured policy.

Missing cases and metrics are configured independently for baseline and
candidate using `error`, `warning`, or `ignore`.

## Bundle format

Bundles are strict JSON:

```json
{
  "schema_version": 1,
  "run": {"id": "run-42"},
  "points": [
    {
      "case": {"process": "tt", "vdd": 1.0, "temp_c": 27},
      "sample": 0,
      "metrics": {
        "phase_margin": {"value": 64.2, "unit": "deg"}
      }
    }
  ]
}
```

Values must be finite. Supported scalar units include dimensionless values,
percent, V, A, s, Hz, F, H, W, Ohm, degrees, radians, dB, SI prefixes, and one
level of multiplication or division such as `V/us` and `A/V`.

Version 2 is reserved for SimCairn evidence and additionally requires the exact
producer version, package-source-tree digest, validation implementation digest,
adapter digest, and aggregate activity ID. A version-1 document cannot acquire
that trust level by changing its version or contract label.

## Baseline workflow

After review, make a canonical baseline explicitly:

```bash
regressistor freeze \
  --policy policy.toml \
  --candidate accepted.json \
  --out baseline.json
```

The command refuses to overwrite an existing file unless `--force` is passed and records the
input SHA-256 in baseline metadata. A destination that aliases the candidate or policy is always
rejected.

## Outputs and exit codes

`check` writes:

- `report.json`: complete machine-readable evidence and input hashes.
- `summary.md`: deterministic reviewer summary.
- `junit.xml`: one test case per metric/corner decision.

All writers use same-directory temporary files and atomic installation. Existing outputs are
refused; pass `--force` to `check`, `freeze`, or `import-liberty` only for an intentional
replacement. Inputs are protected from exact-path, case-folded, and physical-file aliases.

Exit codes are 0 for a passing gate, 1 for a blocking decision, 2 for invalid
input, and 3 for output or I/O failure. Warning-severity contract failures are
reported but do not change a passing exit code.

## Python API

```python
from regressistor import compare, load_bundle, load_policy

report = compare(
    load_policy("policy.toml"),
    load_bundle("baseline.json"),
    load_bundle("candidate.json"),
)
print(report.passed, report.counts)
report.write_json("report.json")
```

## Design guarantees

- No evaluation of input text as code.
- Exact case matching after projection onto declared case keys.
- Explicit dimensional conversion; incompatible units fail visibly.
- Improvement cannot be classified as an adverse regression.
- A regression may be required to exceed the measurement's own run-to-run
  scatter as well as a stated magnitude, so a frozen baseline's sampling error
  does not become a permanent source of false alarms.
- Stable ordering and serialization for identical inputs.
- Input objects are never mutated.

See [the architecture document](docs/architecture.md) for the decision model.

## Development

```bash
ruff check .
ruff format --check .
pytest --cov=regressistor --cov-report=term-missing
python -m build
```

The importer has an informational, non-gating benchmark:

```bash
python benchmarks/liberty_nldm_benchmark.py --repetitions 10
```

The project is available under the MIT License.
