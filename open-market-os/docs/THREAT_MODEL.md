# Threat model — 0.1.0

## Assets

1. Secrets: AI provider API keys, FRED key (future: broker credentials).
2. Control of trading: placing/cancelling orders, starting bots, changing risk limits.
3. Private data: positions, orders, journal, strategies, watchlists.
4. Integrity of data and results: prices labelled correctly, backtests without look-ahead.
5. The user's computer (code execution, files).

## Trust boundaries

```
 Browser / paired device ──HTTP(S) + bearer token──▶ Host API ──▶ Services ──▶ SQLite / vault / keychain
 MCP assistant ─stdio─▶ omos mcp bridge ──HTTP + MCP token + tool header──▶ Host API (matrix-enforced)
 Host ──HTTPS──▶ data providers, RSS, SEC, FRED (untrusted input)
 Host ──HTTP(S)──▶ AI providers (untrusted output) · external MCP servers (untrusted output)
 Host ──spawn──▶ process plugins (untrusted code, separate process)
```

## Threats and mitigations

| # | Threat | Mitigations (tested where noted) | Residual risk |
|---|---|---|---|
| T1 | Another program or website on the same computer calls the API | Bearer token required on every request (I); tokens hashed at rest; one-time sign-in codes in the URL fragment | A process running as your user can read the browser's token storage |
| T2 | DNS rebinding from a malicious website | Host-header allow-list: only loopback names and, with remote access, this machine's own addresses/hostname (I) | — |
| T3 | Other devices on the LAN | Loopback-only by default (I); remote access opt-in; pairing codes (5 min, single use, lockout after failures) with permission presets; per-device revocation; rate limits | Plain HTTP on the LAN unless TLS is configured |
| T4 | Cross-site scripting stealing tokens | CSP `script-src 'self'`, no inline scripts, `connect-src` limited to this host (I); Preact escapes text; news/feed text stored as plain text | A future UI bug could still introduce injection |
| T5 | Malicious RSS/XML (XXE, entity expansion) | defusedxml; such feeds refused individually (U) | — |
| T6 | Prompt injection via headlines/data into AI | Evidence delimited as untrusted; AI tools limited to read/analyze/propose; proposals need a person; risk engine on every order (U) | AI analysis text can still be misleading |
| T7 | AI or MCP client acting beyond permission | Host-enforced MCP matrix per request; private/state tools off by default; no live-trading tool; proposals cannot be decided by MCP (I) | — |
| T8 | Malicious external MCP server | Tools disabled until enabled; timeouts; size caps; output marked untrusted; no shell for stdio commands; HTTPS for remote (I) | A stdio server is a local program with your permissions |
| T9 | Malicious plugin | Declarative kinds cannot run code; process plugins: separate process, scrubbed environment, CPU/memory/file/time limits, SHA-256 pinning, symlink refusal, explicit permission grant (I) | Process plugins can use the network and run as your user |
| T10 | Secret leakage | OS keychain or AES-256-GCM vault, never plaintext (U); never sent to AI/plugins/exports; log redaction (U); secret scanning of the repo | Passphrase in `OMOS_VAULT_PASSPHRASE` is readable by your user |
| T11 | Runaway trading (bug, bad strategy, compromised client) | Single gateway + non-bypassable risk engine; circuit breakers; order-rate limits; emergency stop; only a person can resume (U, I, E) | Losses within the limits you set are still possible |
| T12 | Duplicate or lost orders after crashes | Idempotent client order ids; reconciliation on restart; WAL database; corruption recovery from backups (U) | — |
| T13 | Mislabelled or fabricated data | Provider-qualified ids; no cross-venue substitution; status labels everywhere; stale cache labelled; simulator only with `--demo` and labelled (U, E) | Free sources can themselves be wrong |
| T14 | Supply-chain compromise | Few dependencies, lockfile, pip-audit + npm audit, license inventory, no install scripts executed from plugins | A compromised upstream release before advisories exist |
| T15 | Denial of service | Per-client rate limits for remote clients, request size limits, script time/size limits, capped optimiser grids | A local process can still exhaust resources |

Evidence codes: U unit tests, I integration tests, E browser end-to-end tests.

## Out of scope for this release

Exposure to the public internet (not supported), multi-user accounts on one host, protection against
a compromised operating system or administrator, and live broker credentials (no live adapter exists).
