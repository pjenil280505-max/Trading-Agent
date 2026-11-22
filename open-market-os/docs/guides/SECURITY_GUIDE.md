# Security guide

No software is perfectly secure; this explains what Open Market OS protects, how, and what you
should do. The technical analysis is in [THREAT_MODEL.md](../THREAT_MODEL.md); open issues are in
[KNOWN_ISSUES.md](../KNOWN_ISSUES.md#security-findings-and-residual-risks).

## Credentials

- **API keys** (AI providers, FRED) are stored in your OS keychain, or — without one — in an
  AES-256-GCM encrypted file whose key is derived (scrypt) from your passphrase. They are never
  written in plain text, never shown again after saving, never included in exports, and never sent
  to AI models or plugins. Log files redact common key and token formats (tested patterns); do not
  paste secrets into fields that are not meant for them.
- **Your sign-in tokens** are random, stored on the host only as SHA-256 hashes, and each device has
  its own. Revoke a lost device in **Settings → Devices & access**; it is signed out at once.
- **Sign-in links** from `omos start` / `omos open` work once, expire after 10 minutes and carry the
  code after `#`, so it is never sent to the server in the URL or leaked in referrers. Pairing codes
  expire after 5 minutes. Repeated wrong codes lock sign-in for 10 minutes.
- **No broker passwords** exist in this release; there is no live broker.
- `OMOS_VAULT_PASSPHRASE` is a convenience for headless machines: anyone who can read your
  environment or service file can read it. Prefer the keychain or unlock in the UI.

## Permissions

| Who | Can |
|---|---|
| Browser on the host computer | read, paper trading, AI, admin (settings, secrets, devices) |
| Paired device | only the preset you chose: `monitor` (read), `paper-control` (+ paper trading), `full` (+ AI, admin) |
| MCP assistant | only the tools ticked in **Settings → MCP**; enforced by the host per request |
| AI agents | read / analyze / propose levels (Settings → AI); proposals wait for a person |
| Plugins | only the permissions you grant when enabling them |
| Live trading | nobody — no live adapter exists; it would additionally need the `trade:live` permission and the typed switch |

Only a person (browser or CLI) can clear a risk halt or the emergency stop, approve order proposals
or approve a bot for live trading. AI agents and MCP clients cannot.

## AI risks

- **Wrong answers.** Models make things up. Every numeric claim is checked against the evidence pack
  and unverified ones are flagged; treat all AI output as an opinion, not a fact.
- **Prompt injection.** Headlines and data can contain instructions aimed at the model. They are
  passed as delimited, untrusted data; the model cannot place or cancel orders or change settings —
  at most it proposes an order that you must approve, and that still passes the risk engine.
- **Cost.** Hosted providers bill per token. They stay disabled until you type a cost
  acknowledgement, and a daily budget (default $1) stops calls when today's estimated spend reaches
  it. Enter the provider's real prices so the estimate is right.
- **Privacy.** Hosted providers receive the evidence pack (prices, indicators, headlines, reported
  fundamentals) — never your keys, accounts or positions. Local models (Ollama) keep everything on
  your computer.

## MCP risks

- **Our server:** an MCP assistant connects through `omos mcp` with its own token (file
  readable only by you). Tools that read your portfolio or change anything are **off by default**.
  There is no live-trading tool, and no tool reads secrets, changes settings, installs plugins or
  touches files. Every call is logged in the audit log.
- **External servers you add:** a stdio server is a program running on your computer with your
  permissions — add only ones you trust. Their tools start disabled; calls have time and size
  limits; results are treated as untrusted text. Remote servers must use HTTPS.

## Plugins

Declarative plugins (OMS Script indicators/strategies, JSON REST data feeds limited to one declared
host) are interpreted by Open Market OS and cannot run code. **Process plugins** are programs: they
run in a separate process with a scrubbed environment (no secrets), CPU/memory/time limits on
Linux/macOS and a file fingerprint check, but **their network access cannot be blocked**. Install
only plugins whose source you trust.

## Trading safety

Paper only. Every order — yours, a bot's, a workflow's or an approved AI proposal — passes the risk
engine. Circuit breakers halt an account after daily/weekly losses or drawdown, and only you can
resume. Learn where **Risk → EMERGENCY STOP** is before you need it.

## Remote access

- Default: the host listens only on `127.0.0.1`; other devices cannot connect at all.
- `--allow-remote` opens it to your network. Use it only on a home network you trust, never on
  public Wi-Fi, and **never forward the port to the internet** — there is no protection designed for
  public exposure.
- Traffic on the LAN is plain HTTP unless you configure TLS (`tls_certfile`, `tls_keyfile`).
- Requests whose `Host` header is not this computer's own name or address are refused (protection
  against DNS rebinding); remote clients are rate-limited.

## Backups

Backups and exports contain your watchlists, strategies, orders, journal and settings (not your
secrets). They are not encrypted: keep them in a private place. The data folder is created readable
only by your user on Linux/macOS.

## Checklist

- [ ] Keep the host on `127.0.0.1` unless you need other devices.
- [ ] Pair phones with `monitor` or `paper-control`, not `full`.
- [ ] Revoke devices you no longer use.
- [ ] Leave MCP's private and state-changing tools off unless needed.
- [ ] Prefer local AI; set a low daily budget for hosted AI.
- [ ] Install plugins only from sources you trust.
- [ ] Keep backups somewhere private; test a restore once.
- [ ] Update when new releases fix security issues ([SECURITY.md](../../SECURITY.md)).
