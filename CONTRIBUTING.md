# Contributing

Regressistor accepts focused, reviewable changes that preserve deterministic
results and simulator independence.

1. Open an issue for user-visible behavior or schema changes.
2. Create a small branch and add tests that fail without the change.
3. Run `ruff check .`, `ruff format --check .`, and
   `pytest --cov=regressistor --cov-report=term-missing`.
4. Run `python -m build` and the example command from the README.
5. Explain compatibility effects and disclose substantial automated assistance.

Every commit in a pull request must contain an author-matching
`Signed-off-by: Name <email>` trailer (DCO 1.1). Release tags must be annotated
and signed by a key listed in `.github/allowed_signers`; the release workflow
cryptographically verifies the tag before building artifacts.

Tests must use synthetic data that contributors are allowed to publish. Do not
add PDK files, confidential measurements, generated simulator output, or code
copied from another project. Schema changes require an architecture note and a
changelog entry.
