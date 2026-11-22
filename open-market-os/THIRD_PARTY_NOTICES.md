# Third-party notices

Open Market OS is licensed under Apache-2.0 (see LICENSE and NOTICE). It depends on the open-source
components below. Licenses were read from each package's installed metadata on 2026-11-22 and are
summaries — each package's own license file is authoritative. Build-time and test-only tools
(Vite, TypeScript, pytest, bandit, PyInstaller, …) are not distributed and are not listed.

## Bundled in the web interface (JavaScript)

| Component | Version | License | Notes |
|---|---|---|---|
| KLineChart | 10.1.1 | Apache-2.0 | Includes code from TradingView Lightweight Charts (Apache-2.0, Copyright 2019 TradingView, Inc.). NOTICE reproduced in NOTICE and served at /third-party/klinecharts-NOTICE.txt |
| Preact | 10.29.8 | MIT | |

## Python runtime dependencies (installed by pip; bundled only in the single-file binary)

| Package | Version | License | Source |
|---|---|---|---|
| annotated-doc | 0.0.7 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.16.0 | MIT | https://github.com/agronholm/anyio |
| attrs | 26.1.0 | MIT | https://github.com/python-attrs/attrs |
| certifi | 2026.11.20 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| click | 8.5.1 | BSD-3-Clause | https://github.com/pallets/click/ |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| defusedxml | 0.7.1 | Python Software Foundation License | https://github.com/tiran/defusedxml |
| fastapi | 0.165.0 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| httpcore2 | 2.16.2 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| httpx2 | 2.16.2 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| jaraco.classes | 3.4.0 | MIT License | https://github.com/jaraco/jaraco.classes |
| jaraco.context | 6.1.2 | MIT | https://github.com/jaraco/jaraco.context |
| jaraco.functools | 4.6.0 | MIT | https://github.com/jaraco/jaraco.functools |
| jeepney | 0.9.0 | MIT | https://gitlab.com/takluyver/jeepney |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| mcp | 2.3.0 | MIT License | https://modelcontextprotocol.io |
| mcp-types | 2.3.0 | MIT License | https://modelcontextprotocol.io |
| more-itertools | 11.2.1 | MIT | https://github.com/more-itertools/more-itertools |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://numpy.org |
| opentelemetry-api | 1.45.2 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| psutil | 7.2.2 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| pycparser | 3.12 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.14.4 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.5 | MIT | https://github.com/pydantic |
| PyJWT | 2.15.1 | MIT | https://github.com/jpadilla/pyjwt |
| python-multipart | 0.0.34 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| referencing | 0.37.0 | MIT | https://github.com/python-jsonschema/referencing |
| rpds-py | 2026.9.1 | MIT | https://github.com/crate-py/rpds |
| SecretStorage | 3.5.0 | BSD-3-Clause | https://github.com/mitya57/secretstorage |
| sse-starlette | 3.7.0 | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| starlette | 1.10.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| truststore | 0.10.5 | MIT | https://github.com/sethmlarson/truststore |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.5 | MIT | https://github.com/pydantic/typing-inspection |
| uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/ |
| websockets | 17.4 | BSD-3-Clause | https://github.com/python-websockets/websockets |

MPL-2.0 (certifi) is a file-level copyleft license; certifi is used unmodified.

## Data and services

Market data, news and filings are fetched at run time from their providers and are not
redistributed with this software. Each source has its own terms; see docs/DATA_SOURCES.md.
