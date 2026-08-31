# Changelog

All notable changes are recorded here. Versions follow semantic versioning.

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
