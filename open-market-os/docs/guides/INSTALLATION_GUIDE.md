# Installation guide

| Platform | Status in 0.1.0 |
|---|---|
| **Linux x86_64** | **Tested.** Single-file app and source install both verified |
| Windows 10/11 | Not tested. The source install below is expected to work; please report results |
| macOS | Not tested. The source install below is expected to work; please report results |
| Linux ARM (Raspberry Pi etc.) | Not tested (source install only) |
| Android / iOS | Not a host. Use the phone as a screen for a host on your network (section 5) |

Measured on Linux (2026-11-22): the single-file app is 52 MB; a runtime-only Python environment is
115 MB (plus ~70 MB of Node packages if you build the UI). The host used about 100 MB of RAM idle
and 112 MB after loading four 5,000-bar charts. The database grows as market data is cached.

## 1. Linux — single-file app (tested)

```bash
sha256sum -c SHA256SUMS --ignore-missing          # check the download
tar -xzf omos-0.1.0-linux-x86_64.tar.gz
cd omos-0.1.0-linux-x86_64
./omos start                                      # add --demo for offline SIMULATED symbols
```

Open the printed link (`http://127.0.0.1:8765/#code=…`, valid 10 minutes, single use). Move `omos`
anywhere on your `PATH` (e.g. `~/.local/bin`) if you like. The folder also holds `LICENSE`, `NOTICE`
and `third_party_licenses/`.

Python process plugins need a separate Python 3 installation (the single-file app has no
interpreter for plugins); everything else is self-contained.

## 2. From source (Linux tested; Windows and macOS untested)

Prerequisites: **Python 3.11+**, **Node.js 20+** (only to build the web UI), Git.

```bash
git clone <repository url>
cd open-market-os
python3 -m venv .venv
. .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
scripts/build_web.sh              # Windows: cd web; npm ci; npm run build; cd ..
omos start
```

`pip install -e '.[dev]'` adds the test, scan and packaging tools.

To build the release artifacts yourself (on the OS you want them for — PyInstaller cannot
cross-compile): `scripts/build_release.sh`, then `scripts/verify_release.sh`.

## 3. Where your data lives

| OS | Folder |
|---|---|
| Linux | `~/.local/share/open-market-os` (or `$XDG_DATA_HOME/open-market-os`) |
| macOS | `~/Library/Application Support/OpenMarketOS` |
| Windows | `%LOCALAPPDATA%\OpenMarketOS` |

Set `OMOS_HOME` to use another folder. Inside: `omos.db` (all data), `backups/`, `exports/`,
`logs/`, `plugins/`, `secrets.vault` (only without an OS keychain), `host.toml` (settings).

Settings in `host.toml` (edit with `omos config --set key=value`): `port` (8765), `host`,
`allow_remote` (false), `extra_allowed_hosts`, `log_level`, `tls_certfile`, `tls_keyfile`.

## 4. Secrets without a desktop keychain

On desktops, API keys go into the OS keychain (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). Without one (servers, minimal Linux, the single-file app on a headless
box), they go into an AES-256-GCM encrypted file. Unlock it with a passphrase of at least 8
characters, either in **Settings → Secrets** after each start, or by starting the host with
`OMOS_VAULT_PASSPHRASE` set (convenient, but anyone who can read your environment or service file
can read the passphrase — see the security guide). You only need this if you store API keys.

## 5. Phones, tablets and other computers on your network

1. Restart the host with `omos start --allow-remote` (or `omos config --set allow_remote=true`).
   The host then listens on all interfaces. **Use only on a home network you trust and never
   forward the port to the internet.**
2. On the PC, open **Settings → Devices & access**, choose permissions (e.g. `paper-control`) and
   create a one-time code (valid 5 minutes).
3. On the phone, open `http://<PC's LAN address>:8765` (e.g. `http://192.168.1.20:8765`) and type
   the code.

Plain HTTP on your LAN can be read by others on the same network. For HTTPS, create a certificate
(e.g. with `mkcert`) and set `tls_certfile` / `tls_keyfile`. Browsers only allow installing the
app (PWA) and showing notifications on HTTPS or `localhost` origins.

## 6. Start automatically (for 24/7 bots and alerts)

`omos service install` prepares autostart for the current user:

- **Linux:** writes `~/.config/systemd/user/open-market-os.service`, then run
  `systemctl --user daemon-reload && systemctl --user enable --now open-market-os`. For it to run
  without being logged in: `sudo loginctl enable-linger $USER`.
- **macOS (untested):** writes `~/Library/LaunchAgents/org.openmarketos.host.plist`; load it with
  `launchctl load <file>`.
- **Windows (untested):** prints a `schtasks` command that starts the host at log-on.

`omos service print` shows the file without installing; `omos service uninstall` removes it.
Power and sleep settings matter just as much — see the [bot guide](BOT_GUIDE.md#running-247-on-a-home-computer).

## 7. Update

1. `omos backup`
2. Stop the host (Ctrl+C, or `systemctl --user stop open-market-os`).
3. Replace the single-file app, or `git pull && pip install -e . && scripts/build_web.sh`.
4. Start again. Database migrations run automatically on start; your data folder is kept.

## 8. Back up and restore

- Automatic: one backup a day (keeps 14), in `backups/`. Change in **Settings → Backups**.
- Manual: **Settings → Backups → Back up now**, or `omos backup`.
- Move to another machine: **Export everything (zip)** or `omos export` (database + `host.toml`;
  secrets are not included — re-enter API keys).
- Restore: stop the host, then `omos restore <backup file>`. The current database is saved first.
- If the database is found corrupt at start-up, the host restores the newest valid backup and logs it.

## 9. Uninstall

1. `omos service uninstall` (if you set up autostart).
2. Optional — delete all data: `omos reset --confirm "DELETE ALL LOCAL DATA"` (host stopped). This
   removes the database, backups, plugins and vault. Secrets in the OS keychain are stored under the
   service name `open-market-os` and can be removed with your keychain tool.
3. Delete the single-file app folder, or `pip uninstall open-market-os` and the source folder.
