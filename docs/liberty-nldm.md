# Liberty NLDM import

`regressistor import-liberty` converts a deliberately bounded subset of Liberty timing data into
the ordinary Regressistor measurement-bundle v1 schema. It does not execute a simulator, evaluate
Liberty expressions, infer missing units, or claim that a timing library is silicon signoff
evidence. The exact input bytes remain the evidence source.

```console
regressistor import-liberty --input characterized.lib --out characterized.json
regressistor validate --policy timing-policy.toml --bundle characterized.json
```

The writer refuses an existing output by default. `--force` is an explicit replacement request,
but the destination may never alias the Liberty input. Output is flushed to a same-directory
temporary file and atomically installed, so failure cannot leave a truncated bundle.

## Supported records

The importer accepts one `library` with one selected `operating_conditions` group. Selection uses
`default_operating_conditions`; when that attribute is absent, exactly one condition must exist.
`process`, `voltage`, and `temperature` are all required. A library with no condition groups may
instead provide the standard `nom_process`, `nom_voltage`, and `nom_temperature` attributes, which
are labelled `nominal`. Voltage is normalized to volts in the `vdd` case coordinate. The explicit
operating-condition name and numeric process value are preserved separately; a corner name is
never guessed from either one.

The following library units are recognized and dimension checked:

- `time_unit`, `voltage_unit`, `current_unit`, and `leakage_power_unit`;
- `capacitive_load_unit(multiplier, unit)`.

Only a unit needed by emitted data is mandatory. A declaration such as `100ps` is represented as
unit `ps` with multiplier 100, and values are multiplied before entering the bundle. Common
lower-case Liberty base spellings such as `pf` are canonicalized to Regressistor's `pF`. Table axes
carry that explicit unit spelling in their case coordinates.

Internal `rise_power`, `fall_power`, and `power` values are **energy per transition**,
not watts. The importer emits these metrics in `J`, with scale
`capacitance_unit_in_farads * voltage_unit_in_volts**2`. Both declarations are required.
It records this derived scale as `run.units.internal_energy`; `leakage_power_unit` remains
separate library metadata and does not scale an internal-energy table. Neither nominal PVT
voltage nor the time unit is multiplied into this energy scale. Obtaining average power would
require switching activity, which this importer does not invent. Regression policies for these
tables must therefore use energy units such as `J`, `pJ`, or `fJ`, not `W`.

The energy-per-transition meaning is specified in the
[Liberty Reference Manual R-2020.09, pages 305–308](https://zao111222333.github.io/liberty-db/2020.09/reference_manual.pdf#page=305).
The unit-scale convention can also be checked in the independent
[OpenSTA Liberty reader](https://github.com/The-OpenROAD-Project/OpenSTA/blob/a9a3f30ca97dc13f9ef911cae1a82c42c67379e1/liberty/LibertyReader.cc),
at `energyScale` and `readInternalPowerGroups`.

Within `cell` and `pin` or `bus` groups, the converter supports records on a bus itself and on
member `pin` groups nested immediately beneath a bus. Member points preserve both `bus` and
`port` case coordinates; unquoted forms such as `din0[1:0]` are accepted without changing their
identity. The converter supports:

- timing tables `cell_rise`, `cell_fall`, `rise_transition`, and `fall_transition`;
- `rise_constraint` and `fall_constraint`, including setup, hold, minimum-pulse-width, and
  minimum-period arcs identified by the preserved `timing_type`;
- standard `min_pulse_width` groups with `constraint_high`/`constraint_low`, and
  `minimum_period` groups with `constraint`;
- scalar `min_pulse_width_high`, `min_pulse_width_low`, `minimum_period`, and `min_period`;
- `internal_power` tables `rise_power`, `fall_power`, and `power`;
- scalar, one-axis, and two-axis lookup data from `index_1`, `index_2`, and `values`;
- `lu_table_template` and `power_lut_template`, with local index/variable overrides.

Common relation attributes (`related_pin`, `related_output_pin`, `related_bus_pins`, and
`related_pg_pin`) and timing qualifiers (`timing_type`, `timing_sense`, `when`, and `sdf_cond`) are
copied into each case. Internal-power switching qualifiers and two-argument `mode` selectors are
also retained. This makes separate arcs explicit instead of relying on file order.

Supported axis variables are transition time, total output capacitance, voltage, and normalized
voltage variables listed by the importer. An unknown variable fails because assigning it a unit
would be a guess. Three-dimensional tables are outside this contract.

## Bundle mapping and provenance

Each lookup value becomes one measurement point. Its case contains library, cell, port, port kind,
PVT, arc qualifiers, and up to two triplets:

```json
{
  "axis_1": "input_net_transition",
  "axis_1_value": 0.02,
  "axis_1_unit": "ns"
}
```

The point's `sample` is `L<line>:C<column>` for the metric declaration. This is a stable source
locator, not a simulated Monte Carlo sample. `run.source.sha256` binds the exact original bytes,
and `run.source.name` is descriptive only. `run.units`, `run.operating_conditions`, and the
`regressistor.liberty-nldm/1` contract label make the conversion assumptions reviewable.

Every point contains one metric. Two declarations that would produce the same case and metric are
rejected as ambiguous rather than treated as repeated samples. Metric names remain Liberty names;
for example, a setup constraint is `rise_constraint` or `fall_constraint` with
`timing_type = "setup_rising"` in its case.

## Strictness and limits

The parser is dependency-free and treats the file only as data. It accepts UTF-8, block and line
comments, quoted strings, and Liberty line continuations. It rejects malformed syntax, unsupported
escapes, invalid Unicode identities, duplicate libraries/templates/cells/ports/semantic fields,
non-finite decimals, missing or extra table values, incomplete or noncontiguous axes, ambiguous PVT selection, and
unknown physical units.

Default limits are 4 MiB input, 200,000 tokens, 4,096 bytes per token, 64 group levels, 50,000
entries, 2,048 supported tables, 100,000 numeric values, and 320 emitted points. Python callers can
lower these limits with `LibertyLimits`; all limits must be positive integers. The 320-point cap is
independent of Regressistor's existing 1 MiB and metadata-complexity bundle gates, which can reject
an unusually text-heavy result earlier. Convert large multi-cell libraries one deliberately
selected source library at a time.

## Python API

```python
from regressistor import load_liberty_nldm, write_liberty_bundle

conversion = load_liberty_nldm("characterized.lib")
print(conversion.source_sha256, len(conversion.bundle.points))
for source in conversion.metric_sources:
    print(source.metric, source.location.line, source.location.column)
write_liberty_bundle(conversion, "characterized.json")
```

For in-memory data, call `convert_liberty_nldm(payload, source_name="characterized.lib")` with
`bytes`, not decoded text, so the SHA-256 identity remains exact.

## Frozen OpenRAM interoperability gate

CI checks the importer against
[`VLSIDA/OpenRAM@b2b069ce119d1488cbe6883b2240bceb5c7ce29a`](https://github.com/VLSIDA/OpenRAM/tree/b2b069ce119d1488cbe6883b2240bceb5c7ce29a).
The referenced `sram_2_16_1_freepdk45_TT_1p0V_25C.lib` input is fetched by an exact commit and is
not vendored. The gate first requires SHA-256
`6b47ce23059f842957aa470c71d1a684680d5df9e98f48736e7c09588f9bf4e5`, then checks the independently
reviewable invariants: 192 points, 32 metric declarations, all eight supported delay, transition,
constraint, and power families, and three bus/member-port identities. A changed upstream fixture
cannot silently redefine the benchmark.
