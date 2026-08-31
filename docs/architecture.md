# Architecture

## Boundary

Regressistor starts after simulation or measurement extraction. Producers write
strict JSON bundles; Regressistor validates, aligns, compares, and renders. It
never launches a simulator and has no plug-in execution surface.

```text
policy.toml -----> policy parser -----------+
                                             |
baseline.json --> bundle parser --> index ---+--> gate --> immutable report
                                             |               |  |  |
candidate.json -> bundle parser --> index ---+              JSON MD JUnit
```

## Validation layers

Parsing has three separate responsibilities:

1. Policy validation rejects unsupported fields, incomplete contracts,
   negative budgets, duplicate metric names, and unknown units.
2. Bundle validation rejects duplicate case/sample identities, non-finite
   values, unknown fields, and malformed measurement objects.
3. Comparison validation projects every case onto the policy keys and verifies
   unit compatibility when a configured metric is consumed.

Keeping those layers separate allows `validate` to identify structural errors
without running a gate.

## Case and sample model

A case is an ordered tuple in policy key order. Bundle metadata outside those
keys does not alter matching. Points with the same projected case are repeated
samples and are grouped by metric. The selected reducer runs after every sample
is converted to the policy unit.

## Decision model

The contract margin is positive inside the allowed region:

- minimum: `candidate - lower`
- maximum: `upper - candidate`
- range: `min(candidate - lower, upper - candidate)`
- target: `tolerance - abs(candidate - target)`

Regression uses a signed adverse change. Higher-is-better subtracts candidate
from baseline; lower-is-better subtracts baseline from candidate; target mode
compares absolute target errors. A negative adverse change is an improvement.

Contract failure takes precedence over regression because satisfying the public
specification is independent of baseline quality. Missing or invalid evidence
is never converted into a numeric penalty.

## Determinism

- Policies preserve declared metric order.
- Cases sort by canonical JSON.
- Measurement reduction uses sorted interpolation for quantiles and `fsum` for
  means.
- Reports contain content hashes, not generation timestamps.
- JSON keys and bundle points have stable order.

This permits byte-for-byte report comparison when inputs are unchanged.

## Security

Input files are data, never programs. TOML has no expression field. JSON report
labels are escaped by the provided HTML helper, while XML construction uses the
standard library element API. Output overwrite behavior is explicit for frozen
baselines. File paths remain under caller control and are not expanded through
a shell.
