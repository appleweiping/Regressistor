# Changelog

All notable changes are recorded here. Versions follow semantic versioning.

## Unreleased

### Added

- `noise_budget` on a regression policy: a second condition, expressed in standard errors of
  the difference rather than in the metric's own units. A change is a regression only when it
  exceeds both the magnitude budget and the noise budget, so a change too small to care about
  and a change too small to tell from scatter are both allowed through. The scatter comes from
  repeated `sample` points on each side and is combined with Welch's standard error, which does
  not assume the frozen baseline and the candidate scatter equally.
- `noise_min_samples` states how many repeats a side the estimate needs. Below it the noise
  budget is skipped and the decision says so; the magnitude test then stands alone, which can
  only make the gate stricter than the policy asked for.
- Every decision now carries the dispersion of both sides -- count, mean, deviation, span and
  standard error -- whether or not a noise budget is set, because the numbers needed to choose
  a budget are the ones available before it exists.
- `regressistor.dispersion` as a Python API: `describe`, `assess`, `difference_standard_error`,
  `welch_degrees_of_freedom`, and `standardize`.

### Changed

- A report result carries a `noise` object, validated as strictly as the rest of the schema: a
  stored standard error that disagrees with its own deviation and count is rejected rather than
  trusted, as is scatter claimed from a single measurement.
- A `noise_budget` requires `reduce = "mean"`. The budget counts standard errors of the mean,
  and an extreme such as `max` or `p95` moves far more between identical runs than the mean
  does, so applying the mean's error to it would understate the noise and fire on scatter. The
  combination is rejected in the policy parser rather than quietly approximated.

## 0.2.0 - 2026-08-31

- Added a versioned SimCairn measurement-bundle interoperability benchmark,
  including separately labelled analytic-mock and real ngspice-42 evidence.
- Added a Draft 2020-12 bundle schema and fixture conformance tests.
- Added a multi-size load/index/compare benchmark with hashed workload and invariant counts.
- Added failure-injection coverage for truncated, oversized, and over-complex bundles.
- Added a provenance-bound 27-point Ciel SKY130A/ngspice-42 measurement fixture with strict gate
  policy and negative regression tests; no PDK model files are vendored.
- Added the corresponding real GF180MCU/ngspice-42 27-point interoperability fixture and gate.
- Added bounded strict bundle loading and distribution-backed runtime and CLI version metadata.
- Added producer-bound SimCairn bundle v2 with exact package-tree, validator, adapter, and activity
  identity while preserving generic bundle v1 compatibility.
- Added strict duplicate-key, Unicode-scalar, finite-number, text, depth, node, report-size, and
  expanded-decision boundaries with write/read round-trip guarantees.

## 0.1.0 - 2026-08-31

### Added

- TOML policies for contract limits and directional regression budgets.
- Deterministic JSON bundle validation and unit conversion.
- Per-corner comparison with explicit missing-data policy.
- JSON, Markdown, JUnit XML, and human-readable explanation outputs.
- Validation, checking, baseline freezing, and report explanation commands.
