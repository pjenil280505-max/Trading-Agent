# Contributing

Contributions are accepted under the Apache License 2.0.

## Set up

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
scripts/build_web.sh                 # web UI (Node.js 20+)
cd web && npm run dev                # optional: UI dev server on :5173, proxies /api to a running host
```

## Before you open a pull request

```bash
pytest                               # unit + integration (OMOS_RUN_NETWORK=1 for live sources)
scripts/e2e.sh                       # browser E2E (needs Node Playwright + Chromium)
scripts/security_scan.sh             # must pass
cd web && npm run build              # type check + build
```

Bug fixes need a regression test that fails without the fix. Do not mark a feature VERIFIED in
`docs/FEATURE_MATRIX.md` unless tests exist, ran, and their results and limitations are recorded.

## Ground rules

- **Never fabricate data.** No placeholder prices outside the `SIM` provider; every price carries an
  honest status label; never substitute one instrument for another.
- **Every order goes through `ExecutionGateway`** and therefore the risk engine. No shortcuts.
- **AI and MCP stay permission-bound**; nothing may let them place orders directly or read secrets.
- **No paid dependencies**, and new dependencies need a license compatible with Apache-2.0 (no
  GPL/AGPL in distributed code). Add them to `docs/DEPENDENCY_INVENTORY.md`.
- Indicators go in `src/omos/indicators/` with reference-value tests and must pass the no-look-ahead
  test; register them in `registry.py`.
- New data providers implement `marketdata/providers/base.Provider`, declare honest `ProviderInfo`
  (status, limits, terms) and get live tests behind `OMOS_RUN_NETWORK=1`.
- All chart-library calls stay inside `web/src/chart/adapter.ts`.
- Update the relevant docs in the same change.

## Layout

```
src/omos/         host: api/, marketdata/, indicators/, script/, backtest/, execution/, risk/,
                  portfolio/, bots/, alerts/, screener/, research/, ai/, mcp/, plugins/,
                  automation/, security/, storage/, device/, cli.py, host.py
web/src/          Preact UI: views/, chart/ (adapter + drawing overlays), components/
tests/            unit/, integration/, e2e/ (Playwright)
scripts/          build_web, e2e, security_scan, benchmark, build_release, verify_release
docs/             guides/, reference/, architecture/, research/ and project status documents
examples/         example plugins
```
