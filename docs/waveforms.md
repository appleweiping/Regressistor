# Scalar waveform comparison

The waveform API compares two finite, continuous scalar trajectories. It does
not run a simulator. A trace has 2–100,000 immutable samples, explicit axis and
value units, and a strictly increasing axis. Repeated timestamps, discontinuities,
NaN/infinity, phase unwrapping and extrapolation are rejected or outside this
profile. Do not turn event data into a continuous trace by dropping repeated times.

## A failure that endpoint-only checks miss

```python
from regressistor import Waveform, WaveformPolicy, compare_waveforms

baseline = Waveform((0, 1), (-1, 1), "s", "V")
candidate = Waveform((0, 1), (-1.125, 0.875), "s", "V")
policy = WaveformPolicy(relative=0.25, grid="linear")
result = compare_waveforms(baseline, candidate, policy)
assert result.status == "fail"
assert result.maximum_excess.axis == 0.5
assert result.failing_breakpoints == 1
```

Both endpoints pass, but at the baseline's zero crossing the allowed relative
error is zero and the candidate is −0.125 V. Linear mode checks that crossing
as well as the merged input knots. A nonzero relative floor adds its positive
and negative crossings. Between consecutive such breakpoints, the allowed error
is affine and absolute error minus allowed error is convex; its maximum occurs
at an endpoint. This is a guarantee for the declared piecewise-linear traces,
not for an underlying exponential or other unsampled physical signal.

## Policies and exact arithmetic

The baseline-referenced pointwise allowance is
`absolute + relative * max(abs(baseline), relative_floor)`. Absolute and floor
values use the baseline's ordinate unit; relative is dimensionless. Equality
with the allowance passes. No hidden epsilon is added.

- `grid="exact"` (default) requires identical converted grids and compares
  samples. It makes no claim about the signal between samples.
- `grid="linear"` explicitly requests the continuous piecewise-linear profile.
- `domain="full"` (default) rejects different endpoints, with no report written.
- `domain="intersection"` reports both traces' excluded left/right lengths.
  Any exclusion yields `partial_pass` or `partial_fail`, and `passed` remains
  false. An intersection whose endpoints already cover both traces can pass.

Input Python values must be built-in finite binary64 floats or exactly
representable integers with magnitude at most 2**53. Signed zero is normalized.
Arithmetic compares those exact binary values using rational interpolation and
exact decimal SI-prefix conversion. For example, a rounded binary64 `0.001 s`
need not equal the rational conversion of `1 ms`; exact-grid mode deliberately
does not snap these into equality. Choose a shared axis representation upstream.
The unit grammar supports linear SI products/ratios and percent. It does not
convert radians to degrees or unwrap phase. Complex results must be projected
explicitly, for example into separate real and imaginary voltage traces.

The preflight bounds comparison work by `n_baseline` for exact mode or
`3*n_baseline + n_candidate` for linear mode. This conservative upper bound is
checked before scanning the grid; a requested smaller budget can reject an input
whose actual merged grid would be smaller. The hard ceiling is 400,000
breakpoints. Diagnostics retain at most 64 witnesses, default 16, while all
required breakpoints still contribute to counts, extrema and the observations
digest. Failure counts describe evaluated breakpoints, not intervals, time
duration or statistical yield. Tied maxima select the earliest axis value.

## Wire format and CLI

Versioned waveform and policy JSON encode numbers as canonical Python
`float.hex()` **strings**, not decimal JSON numbers. Use the public writers:

```python
from regressistor import write_waveform, write_waveform_policy

write_waveform(baseline, "artifacts/baseline.waveform.json")
write_waveform(candidate, "artifacts/candidate.waveform.json")
write_waveform_policy(policy, "artifacts/policy.waveform.json")
```

```bash
regressistor waveform-check --policy artifacts/policy.waveform.json \
  --baseline artifacts/baseline.waveform.json \
  --candidate artifacts/candidate.waveform.json --out artifacts/comparison.json
```

Exit codes are 0 for full pass, 1 for fail or partial comparison, 2 for invalid
input/domain/budget, and 3 for output errors. Outputs are atomic and no-clobber
by default. `--force` permits replacing an output, never a protected input.
The executable original example is `examples/waveform/generate.py`; run it with
`--out` set to a fresh directory, then run the command above using those paths.

The three JSON Schemas ship in `regressistor/schemas/`. Structural validation
does not replace the runtime's identities, units, ordering, canonical-number and
cross-field checks. The reader rejects unknown/duplicate fields, excessive
nesting, numeric JSON samples and malformed Unicode. Trace input is bounded to
16 MiB. Reports preserve exact rational observations as reduced hexadecimal
integer numerator/denominator pairs, avoiding lossy JSON float rendering.

Trace and policy identities bind their canonical content. A report also binds
the trace identities, policy, all evaluated observations and its own serialized
content. These hashes are consistency checks, not signatures, simulator-success
evidence or proof that a third party ran the calculation. The report writer
checks intrinsic witness/count/domain consistency but does not replay a report
against unavailable traces.

Independent tests include rational interval enumeration, collinear refinement,
extreme finite binary64 samples, relative-floor crossings and separately
derived RC transient/AC examples. Analytic tests are not simulator runs. Event
alignment, ULP budgets, vector norms and statistical comparison remain separate
capabilities, not implied by this continuous scalar profile.

The [separate real-simulator experiment](waveform-simulator-oracle.md) records
three actual ngspice RC runs through the public SpiceTrellis result boundary.
It includes both passing nominal-grid comparisons and deliberately failing
changed-circuit comparisons. That evidence is distinct from the offline tests.
