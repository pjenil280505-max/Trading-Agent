# Plugin guide

Plugins add indicators, strategies, data feeds and computations. Prefer **declarative** plugins —
they cannot run code. Examples live in `examples/plugins/`.

## Kinds

| Kind | Entry file | Runs as | Use for |
|---|---|---|---|
| `indicator` | `.oms` script | OMS Script sandbox | Custom indicators on charts (`plugin:<id>`) |
| `strategy` | `.oms` script | OMS Script sandbox | Extra strategy templates |
| `screener` | `.oms` / JSON | Sandbox | Reusable screen definitions |
| `workflow` | JSON | Workflow engine | Packaged automation |
| `data_provider_rest` | JSON | Built-in REST client, one allow-listed host | Any JSON candle API |
| `process` | program (e.g. `main.py`) | Separate OS process | Computations that need real code |

## Manifest (`plugin.json`)

```json
{
  "id": "org.example.ema-ribbon",
  "name": "EMA Ribbon",
  "version": "1.0.0",
  "author": "You",
  "license": "Apache-2.0",
  "kind": "indicator",
  "entry": "ribbon.oms",
  "description": "Four EMAs as an overlay.",
  "omos_version": ">=0.1.0",
  "permissions": ["read:market_data"]
}
```

Required: `id`, `name`, `version`, `author`, `license`, `kind`, `entry`, `description`.
Permissions may only be `read:market_data`, `compute`, or `network:<exact host>`; a REST provider
must declare its host.

## Install, enable, remove

**Settings → Plugins**: enter the plugin folder's path → **Install**. The files are copied into the
data folder and fingerprinted (SHA-256); symbolic links are refused. Plugins install **disabled**.
**Enable…** shows the requested permissions; you must grant all of them. If the files change later,
the plugin is refused until reinstalled. **Disable** / **Uninstall** at any time.

## REST data providers

`provider.json` describes the API declaratively:

```json
{
  "name": "OKXPLUG",
  "display_name": "OKX (generic REST plugin example)",
  "status": "DELAYED",
  "url": "https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit={limit}",
  "intervals": {"1m": "1m", "1h": "1H"},
  "rows": "data",
  "fields": {"t": 0, "o": 1, "h": 2, "l": 3, "c": 4, "v": 5},
  "time_unit": "ms",
  "asset_class": "crypto",
  "rate_per_s": 2
}
```

Requests go only to the declared host, over HTTPS, rate-limited. The data carries the status you
declare; `REALTIME` is accepted only with an explicit `"realtime_claim_ok": true`, otherwise it is
shown as `DELAYED`. Label honestly.

## Process plugins

The host runs the entry program for each call and talks JSON-RPC over stdin/stdout:
request `{"jsonrpc":"2.0","id":1,"method":"compute","params":{…}}`, response
`{"jsonrpc":"2.0","id":1,"result":{…}}`. See `examples/plugins/zscore-process/main.py`.

Isolation: separate process; environment scrubbed to `PATH`, `LANG` and the plugin id (no secrets);
on Linux/macOS limits of 10 s CPU time, 1 GB memory and 10 MB of file writes; a 10 s per-call
timeout; Python plugins run with `python -I`.
**Network access cannot be blocked** and the program runs as your user — install process plugins
only from sources you trust. The single-file app needs a system Python 3 for Python plugins.
