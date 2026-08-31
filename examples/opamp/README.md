# Synthetic op-amp gate

This fixture demonstrates the complete CLI without a simulator or proprietary
technology data. The candidate improves current and bandwidth. Its slow,
low-voltage, high-temperature phase margin is below the 60 degree contract; the
slew-rate warning is non-blocking.

From the repository root:

```bash
regressistor check \
  --policy examples/opamp/policy.toml \
  --baseline examples/opamp/baseline.json \
  --candidate examples/opamp/candidate.json \
  --out artifacts/opamp
```

The expected exit code is 1. Inspect `report.json`, `summary.md`, and
`junit.xml` under the output directory.
