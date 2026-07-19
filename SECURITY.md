# Security Policy

## Supported versions

Security fixes are handled on the default branch until the project adopts a
versioned release policy.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Report it by
email to the repository owner, or use GitHub's private vulnerability reporting
feature if it is enabled for this repository.

Include enough detail to reproduce the issue, including the affected commit or
version, configuration, and impact. Do not include real API keys, tokens,
personal transcripts, or database files in the report.

## Scope

This app is designed for local, single-user use. If you deploy it on a public
network, add your own authentication, access control, secret management, rate
limits, HTTPS termination, monitoring, and backup policy.

## Secret handling

- Keep API keys and provider credentials in `.env` or your deployment secret
  store.
- Do not commit `.env`, runtime logs, SQLite databases, database backups, or
  screenshots that may contain private transcript content.
- Rotate any credential that may have been committed, logged, or shared.