# Security policy

## Supported versions

Security fixes are applied to the latest released minor version.

## Reporting

Do not publish a suspected vulnerability with exploit details. Use GitHub's
private vulnerability reporting for the repository owner. Include the affected
version, a minimal synthetic reproducer, impact, and any proposed mitigation.

Regressistor treats policy and result files as untrusted data. It never executes
expressions from them. Reports may contain supplied labels, so downstream HTML
renderers must escape report content.
