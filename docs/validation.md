# Validation evidence

Regressistor accepts generic measurement bundle version 1 for compatibility and
independently validates SimCairn's producer-bound
`regressistor.measurement-bundle/2` artifact without importing SimCairn. Both
tools enforce schema version, finite scalar metrics, explicit units, complete
case keys, and unique case/sample identities. Version 2 also binds the exact
SimCairn distribution version, all imported package Python sources, the
validation implementation, the simulator adapter, and the aggregate activity
graph. Relabelling version-1 metadata as version 2 is rejected fail closed.

Every point must carry an explicit `sample`, including sample `0`, and every
measurement must carry an explicit `unit`, including dimensionless unit `1`.
The loader does not infer either field. A frozen SimCairn v2 bundle preserves
the four contract provenance fields, including its nested producer identity,
and adds only `frozen_from_sha256`, a lowercase SHA-256 digest of the exact
source bundle bytes; other contract run fields are rejected.

Bundle JSON rejects duplicate keys and non-standard numeric constants before
schema validation. Case and metric names are printable and unique under case
folding, sample identifiers are bounded and printable, and SimCairn contract
metadata requires the producer plus a 64-character content-addressed aggregate
activity ID.

The benchmark contains two separately labelled RC fixtures: an offline analytic
mock for deterministic CI and a real ngspice-42 run. Neither is PDK or silicon
evidence. The SKY130A and GF180MCU fixtures are real ngspice-42/KLU runs against
pinned Ciel PDK assets. They are open-PDK simulation evidence, not silicon data
or signoff evidence. Hashes, commands, producer identity, point count, activity
counts, and expected gate decision are recorded in `benchmarks/manifest.json`
and the copied evidence manifests.

The 27-point SKY130 fixture is separately documented in
[`../benchmarks/sky130-reference.md`](../benchmarks/sky130-reference.md). It is real simulator output
from pinned open PDK models, but it is neither silicon data nor signoff evidence; the PDK itself is
not redistributed by this repository.

The public Draft 2020-12 schemas are
[`schemas/measurement-bundle-1.schema.json`](schemas/measurement-bundle-1.schema.json) and
[`schemas/measurement-bundle-2.schema.json`](schemas/measurement-bundle-2.schema.json). They provide
portable shape validation for producers and editors, including the optional frozen-source digest
accepted by the strict SimCairn runtime contracts. Runtime loading is deliberately stricter:
JSON Schema cannot observe duplicate keys after decoding, enforce case-folded name uniqueness,
apply byte limits, or enforce parser depth, node, text, and expanded-decision budgets. Runtime
validation also parses supported engineering units and rejects values that overflow to non-finite
floats. Bundle and policy inputs are capped at 1 MiB; reports use a symmetric 16 MiB/250,000-node
budget, and successful baseline/report writes are prevalidated against their corresponding loader.
