# License compliance — 0.1.0

Not legal advice. This records what was checked and how obligations are met.

## Our license

Open Market OS is licensed under **Apache-2.0** (`LICENSE`). Contributors' code is accepted under the
same license ([CONTRIBUTING.md](../CONTRIBUTING.md)).

## Third-party code

| Distribution | Contains | Obligations | How they are met |
|---|---|---|---|
| Source repository | Our code only (dependencies are fetched by pip/npm) | — | — |
| Built web UI (`src/omos/web/dist`, inside the wheel and binary) | KLineChart (Apache-2.0, includes code derived from TradingView Lightweight Charts, Apache-2.0), Preact (MIT) | Apache-2.0 §4: include the license and the NOTICE contents; MIT: include the copyright and license | License and NOTICE texts copied into `dist/third-party/` at build time, shown in the app (System status → About and third-party notices) and reproduced in our `NOTICE` |
| Wheel (`pip install`) | Our code + built UI | As above; Python dependencies are installed separately from PyPI under their own licenses | `LICENSE` and `NOTICE` inside the wheel's metadata |
| Single-file binary | Our code, built UI, Python interpreter and the 44 runtime packages | Include each bundled package's license and notices | `third_party_licenses/` in the release tarball, collected from each package's metadata by `scripts/build_release.sh`, plus `THIRD_PARTY_NOTICES.md` |

License mix of runtime dependencies: MIT, BSD-3-Clause, Apache-2.0, PSF-2.0, MIT-0, 0BSD, Zlib, CC0
and MPL-2.0 (certifi, used unmodified — MPL obligations attach only to modified MPL files). No GPL,
AGPL or other strong-copyleft code is distributed. PyInstaller (GPL-2.0-or-later) is a build tool
whose license exception explicitly allows distributing the built program under any license.

## Repositories reviewed but not reused

See [research/REPOSITORY_AUDIT.md](research/REPOSITORY_AUDIT.md): projects were evaluated for license
compatibility; GPL/AGPL and source-available projects were not copied. No code from them is included.

## Data

Market data, news and filings are fetched at run time from each provider and are **not** distributed
with the software. Each source's terms apply to the user ([DATA_SOURCES.md](DATA_SOURCES.md)); in
particular Yahoo Finance data is for personal, non-commercial use, and some FRED series carry
third-party copyright. The software does not circumvent access controls, paywalls or authentication;
it uses public endpoints at rates below the published limits.

## Trademarks

Names such as TradingView, Yahoo, Binance, OKX, Coinbase, Kraken, Claude and others are trademarks of
their owners, used only to describe compatibility. Open Market OS is not affiliated with them.

## Verification

`scripts/security_scan.sh` writes `python-licenses.csv` and `npm-licenses.csv` (full inventories)
alongside the vulnerability scans; the 2026-11-22 run found no license that conflicts with Apache-2.0
distribution.
