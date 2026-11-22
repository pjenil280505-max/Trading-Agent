# Security policy

## Reporting a vulnerability

Please report security problems privately through the repository's **Security → Report a
vulnerability** page (GitHub private vulnerability reporting) rather than in a public issue. Include
the version, your platform, steps to reproduce and the impact you expect. You should receive an
acknowledgement within 7 days. Please allow time for a fix before public disclosure.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Scope

In scope: the host (API, authentication, risk engine, secret storage, MCP bridge and client, plugin
isolation), the web UI and the release artifacts. Out of scope: exposing the host to the public
internet (not a supported configuration), a compromised operating system, and the third-party data
or AI providers themselves.

## How this project checks itself

`scripts/security_scan.sh` (bandit, pip-audit, npm audit, detect-secrets, license inventory), the
test suite's security tests (authentication, scopes, MCP permission enforcement, prompt injection,
plugin isolation, Host-header and CSP rules), and the threat model in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). Results of the latest audit and the remaining risks are
in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md). No security property is guaranteed.

User-facing advice: [docs/guides/SECURITY_GUIDE.md](docs/guides/SECURITY_GUIDE.md).
