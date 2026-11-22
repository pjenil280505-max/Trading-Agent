# Dependency inventory — 0.1.0

Generated from installed package metadata on 2026-11-22. Version ranges are declared in
`pyproject.toml` and `web/package.json`; exact npm versions are pinned in `web/package-lock.json`.
Known-vulnerability status: `pip-audit` (91 installed packages) and `npm audit` (shipped packages)
reported none on 2026-11-22 (`scripts/security_scan.sh`).

## Python runtime (44 packages, all free and open source)

| Package | Version | License | Kind | Why |
|---|---|---|---|---|
| annotated-doc | 0.0.7 | MIT | transitive |  |
| annotated-types | 0.8.0 | MIT | transitive |  |
| anyio | 4.16.0 | MIT | transitive |  |
| attrs | 26.1.0 | MIT | transitive |  |
| certifi | 2026.11.20 | Mozilla Public License 2.0 (MPL 2.0) | transitive |  |
| cffi | 2.1.1 | MIT-0 | transitive |  |
| click | 8.5.1 | BSD-3-Clause | transitive |  |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | direct | AES-256-GCM vault, scrypt |
| defusedxml | 0.7.1 | Python Software Foundation License | direct | safe RSS/XML parsing |
| fastapi | 0.165.0 | MIT | direct | HTTP API |
| h11 | 0.16.0 | MIT License | transitive |  |
| httpcore | 1.0.9 | BSD-3-Clause | transitive |  |
| httpcore2 | 2.16.2 | BSD-3-Clause | transitive |  |
| httpx | 0.28.1 | BSD License | direct | HTTP client for data providers, AI, MCP |
| httpx2 | 2.16.2 | BSD-3-Clause | transitive |  |
| idna | 3.20 | BSD-3-Clause | transitive |  |
| jaraco.classes | 3.4.0 | MIT License | transitive |  |
| jaraco.context | 6.1.2 | MIT | transitive |  |
| jaraco.functools | 4.6.0 | MIT | transitive |  |
| jeepney | 0.9.0 | MIT | transitive |  |
| jsonschema | 4.26.0 | MIT | transitive |  |
| jsonschema-specifications | 2025.9.1 | MIT | transitive |  |
| keyring | 25.7.0 | MIT | direct | OS keychain access |
| mcp | 2.3.0 | MIT License | direct | MCP server and client SDK |
| mcp-types | 2.3.0 | MIT License | transitive |  |
| more-itertools | 11.2.1 | MIT | transitive |  |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | direct | all numeric work (indicators, backtests) |
| opentelemetry-api | 1.45.2 | Apache-2.0 | transitive |  |
| psutil | 7.2.2 | BSD-3-Clause | direct | device profile, process checks |
| pycparser | 3.12 | BSD-3-Clause | transitive |  |
| pydantic | 2.14.4 | MIT | direct | request/response models (FastAPI, MCP) |
| pydantic_core | 2.50.5 | MIT | transitive |  |
| PyJWT | 2.15.1 | MIT | transitive |  |
| python-multipart | 0.0.34 | Apache-2.0 | transitive |  |
| referencing | 0.37.0 | MIT | transitive |  |
| rpds-py | 2026.9.1 | MIT | transitive |  |
| SecretStorage | 3.5.0 | BSD-3-Clause | transitive |  |
| sse-starlette | 3.7.0 | BSD-3-Clause | transitive |  |
| starlette | 1.10.0 | BSD-3-Clause | transitive |  |
| truststore | 0.10.5 | MIT | transitive |  |
| typing_extensions | 4.16.0 | PSF-2.0 | transitive |  |
| typing-inspection | 0.4.5 | MIT | transitive |  |
| uvicorn | 0.54.0 | BSD-3-Clause | direct | ASGI server |
| websockets | 17.4 | BSD-3-Clause | direct | WebSocket protocol for live updates |

## JavaScript shipped in the web UI

| Package | Version | License | Why |
|---|---|---|---|
| klinecharts | 10.1.1 | Apache-2.0 | Chart rendering (behind `web/src/chart/adapter.ts`) |
| preact | 10.29.8 | MIT | UI components |

## Build-time only (not distributed)

| Tool | Version | License | Why |
|---|---|---|---|
| vite | 7.3.8 | MIT | Bundler |
| typescript | 5.9.3 | Apache-2.0 | Type checking |
| @preact/preset-vite | 2.10.2 | MIT | JSX for Preact |
| + 133 transitive npm packages | — | Mixed; across all 138 npm packages: MIT 119, ISC 6, BSD 9, Apache-2.0 3, CC-BY-4.0 1 (browser-support data) | Build tooling |
| setuptools, wheel | — | MIT | Python packaging |
| PyInstaller | ≥6 (6.22.3 used) | GPL-2.0-or-later with an exception allowing built programs to be distributed under any license | Single-file builds |

## Development and testing only

pytest, pytest-timeout, coverage (tests); `ta` and pandas (independent indicator oracle in tests);
bandit, pip-audit, pip-licenses, detect-secrets (scans); Node.js + Playwright + Chromium (browser
E2E tests and UI benchmarks). None are needed to run the app.

## External programs (optional)

| Program | Needed for | Cost |
|---|---|---|
| Ollama / LM Studio / llama.cpp | Local AI models | Free |
| A web browser | The UI | Free |
| `notify-send` (Linux) | Host desktop notifications | Free, usually preinstalled |
