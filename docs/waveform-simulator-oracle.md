# Real simulator waveform oracle

`benchmarks/waveform_simulator_oracle.py` is an opt-in integration experiment, not
a simulator backend or a required Regressistor dependency. It runs three original
passive RC circuits with a caller-supplied, SHA-256-pinned ngspice 42 executable,
passes actual raw data through the **public SpiceTrellis 0.6.0 wheel**, and compares
the resulting scalar waveforms with independent analytical predictions.

The data path is:

1. A fixed original deck is executed by `ngspice -n -b`. User/system initialization
   commands are excluded. No caller-provided netlist or arbitrary control command
   is accepted by the experiment generator.
2. `SpiceTrellis.load_raw` reads each actual raw artifact under lowered byte, plot,
   variable, point and cell ceilings. `dump_result` and `load_result_text` perform
   a strict public result-JSON round trip; the result must remain equal and its
   source digest must match the actual raw bytes.
3. The bridge requires one one-dimensional plot with exactly the expected axis
   and output vector. Transient data must be real. AC frequency must have an
   exactly zero imaginary component; AC output is projected **separately** onto
   its real and imaginary components. Quantity names are checked and the bridge
   explicitly supplies `s`/`Hz` and `V`; it never guesses a unit or converts a
   complex voltage to magnitude by accident.
4. Each `Waveform` traverses Regressistor's canonical hexadecimal binary64 JSON
   round trip. The comparator checks these exact sampled piecewise-linear traces.
   Parsed raw/JSON metadata is data and cannot execute simulator commands.

## Fixed physical experiments and acceptance gates

All three circuits use `C = 1 µF` and a 0-to-1 V input with a **10 µs linear rise**
starting at 1 ms. Transient analysis ends at 6 ms; AC spans 0 to 4000 Hz. The
baseline and nominal candidate use `R = 1 kΩ`; the deliberately changed candidate
uses `R = 2 kΩ`. Requested maximum transient steps are 50 µs and 5 µs respectively.
The simulator may choose smaller adaptive steps, so actual transient grids differ.
The linear AC grids contain exactly 81 baseline or 161 candidate points.

For `τ = RC`, elapsed time `e` since the beginning of the rise, and rise duration
`r`, the independent transient oracle is zero before the rise; during it,

```text
y(e) = [e + τ expm1(-e / τ)] / r,          0 < e < r
y(e) = 1 - [-(τ/r) expm1(-r / τ)]
             exp(-(e-r) / τ),              e >= r
```

The implementation evaluates the small-argument remainder with a bounded Taylor
sum to avoid cancellation, and the post-rise expression in an equivalent stable
form. Offline tests compare it with an 80-digit Decimal convolution calculation,
including the very start of the ramp. This is not an ideal-step approximation.
AC uses `H(jω) = 1 / (1 + jωRC)` and checks both components independently.

Every simulator sample must agree with its **own** circuit's analytical waveform
within 0.0005 V for transient or `2e-12` V for either AC component. Thus the
deliberately changed circuit must still be simulated correctly; its intended
failure comes from comparison with the original circuit, not from a solver error.

Continuous piecewise-linear comparison requires equal complete-domain endpoints,
never extrapolates, and uses the exact merged proof grid. Nominal coarse/fine
traces must pass 0.002 V transient and 0.03 V AC-component absolute budgets. The
doubled-resistance traces must fail those same budgets. These declared tolerances
are fixed in the harness and are not fitted to a run's observed error.

## Reproduce without changing the project's environment

Use a separate virtual environment. Download the public SpiceTrellis release
wheel yourself or use an already audited copy; the harness never downloads
executables or dependencies. Its required wheel is
`spicetrellis-0.6.0-py3-none-any.whl`, SHA-256
`59f2c73554a0318b817ff02f8fdd7d82c34911ef7d9752f111a64d9244cc1860`.
The distribution version, imported package location, and **every installed
package payload file** must match that exact wheel. Additional importable files
or source-tree shadowing fail closed.

For example, from the Regressistor repository, with paths chosen for your system:

```powershell
uv venv --python 3.11 D:/Temp/regressistor-waveform-oracle-env
uv pip install --python D:/Temp/regressistor-waveform-oracle-env/Scripts/python.exe --no-deps D:/Downloads/spicetrellis-0.6.0-py3-none-any.whl
uv pip install --python D:/Temp/regressistor-waveform-oracle-env/Scripts/python.exe --no-deps --editable .
D:/Temp/regressistor-waveform-oracle-env/Scripts/python.exe benchmarks/waveform_simulator_oracle.py --ngspice D:/Tools/Spice64/bin/ngspice_con.exe --expected-sha256 YOUR_EXACT_BINARY_SHA256 --spicetrellis-wheel D:/Downloads/spicetrellis-0.6.0-py3-none-any.whl --output D:/Temp/regressistor-waveform-oracle-run
```

The output directory must not already exist. Each process is limited to 30 seconds,
each watched output file to 8 MiB, and their aggregate to 24 MiB. File sizes are
polled every 20 ms and checked again after exit; these are detection/termination
bounds, not kernel-enforced storage quotas, so brief write overshoot is possible.
The direct child is terminated and reaped on failure. The harness does not claim
an adversarial-process sandbox or descendant-process containment. It expects a
trusted, caller-selected binary and fixed original decks.

The complete run directory retains decks, stdout, stderr, solver logs, raw files,
SpiceTrellis JSON, canonical waveform JSON and `report.json`. A failed run retains
its diagnostic files but does **not** produce a successful final report. The
report records executable, wheel, runtime-package, harness, deck, raw, log, wire,
waveform and comparison identities. Runtime package and executable identities
are checked again at the end. Hashes establish content binding, not signatures;
the report does not claim a whole simulator's convergence correctness merely
because an exit code is zero.

## Recorded Windows run, 2026-09-09

The retained [source-bound report](../benchmarks/evidence/waveform-ngspice42-v050-20260909.json)
records CPython 3.11.2, Regressistor 0.5.0 source, the pinned public SpiceTrellis
0.6.0 wheel, and ngspice 42 executable SHA-256
`f86062f3bb1016dcf1552a57f38954891b4e71aee6defbebde6de589ee55682f`.
The Regressistor package identity uses the manifest algorithm recorded by this
harness; it is not interchangeable with another benchmark's source-hash format.
The final source-cleanup replay's package manifest SHA-256 is
`9bc1ac4183f2e3a069c9c348c03bd6190998abe51d0b3b02c379aa85467fec5c`;
its retained report SHA-256 is
`39aa7da56ccb4c0e8d395e82eff90d06441abe5a7e53a96ae272234a41bf188f`.

| Actual run | Transient points | Maximum transient analytic error | AC points | Maximum AC-component analytic error |
| --- | ---: | ---: | ---: | ---: |
| Nominal coarse | 189 | `4.01205e-5 V` | 81 | `1.11023e-16 V` |
| Nominal fine | 1249 | `7.38098e-7 V` | 161 | `1.11023e-16 V` |
| Doubled resistance | 1246 | `1.82896e-7 V` | 161 | `1.11023e-16 V` |

All nine analytical checks passed. Nominal coarse/fine continuous comparisons
passed with maximum deviations of `6.20552e-5 V` transient, `0.0208352 V` AC real,
and `0.0174006 V` AC imaginary. Doubled-resistance comparisons failed with maxima
of `0.250036 V`, `0.334871 V`, and `0.166494 V`, respectively. Their retained
reports contain exact rational observations and complete evaluated-breakpoint
counts, not just these rounded display values.

This is three real executions and six cross-grid comparisons of one original RC
family. It does not establish arbitrary circuit accuracy, phase-unwrapping or
discontinuous-event semantics, performance parity with a simulator regression
suite, or behavior of other ngspice builds. Raw headers may contain run dates and
logs contain timings, so complete run/report hashes can legitimately change on
replay even when numerical results agree. Offline harness tests do not substitute
for rerunning this external integration experiment.
