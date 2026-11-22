# Open Market OS 0.2.0 — final report

Date: 2026-11-22 · Source: `open-market-os/` on branch `ccr-80d6e707-jb6y48` · Build pipeline:
`.github/workflows/omos-build.yml`

**The short version:**
- Open Market OS now installs and runs like an ordinary app on Windows, macOS and Linux, with an
  Android companion app. Download, install, open, use: no terminal.
- Every package was built, installed and smoke-tested automatically on real Windows, macOS
  (Apple Silicon and Intel), Linux (x86_64 and ARM) and Android machines. That includes updating
  while running and uninstalling on Windows, and pairing over pinned HTTPS on Android.

What it is **not**:
- It cannot trade real money: there is no live broker or MetaTrader 5 connection.
- No person has yet used the installers on a home computer or a physical phone.
- 25 of 183 tracked features are not started.

## DOWNLOAD

Draft release **"Open Market OS 0.2.0"** on the repository's Releases page. Draft releases are
visible only to people with write access to the repository; publish it to make it public and to let
the in-app updater offer future versions.

| File | For |
|---|---|
| `OpenMarketOS-0.2.0-windows-x64-setup.exe` | Windows 10/11, 64-bit |
| `OpenMarketOS-0.2.0-macos-arm64.dmg` / `…-macos-x86_64.dmg` | Mac (Apple Silicon / Intel) |
| `open-market-os_0.2.0_amd64.deb` / `…_arm64.deb` | Ubuntu, Debian, Mint |
| `OpenMarketOS-0.2.0-x86_64.AppImage` / `…-aarch64.AppImage` | Any 64-bit Linux |
| `open-market-os-0.2.0-linux-*.tar.gz` | Manual Linux install |
| `OpenMarketOS-0.2.0-android.apk` | Android 7+ companion |
| `SHA256SUMS.txt` | SHA-256 of every file |

The checksums of the files you receive are in `SHA256SUMS.txt` (the pipeline computes them from the
exact files it uploads; PyInstaller builds are not byte-for-byte reproducible, so a rebuild differs).

## INSTALL

- **Windows:** run the setup file; SmartScreen: More info → Run anyway (not code-signed). No admin.
- **macOS:** drag to Applications; first open: System Settings → Privacy & Security → Open Anyway.
- **Linux:** double-click the `.deb` → Install; or make the AppImage executable.
- **Android:** open the APK on the phone, allow installing from this source.

Details: [INSTALLATION_GUIDE](guides/INSTALLATION_GUIDE.md).

## OPEN

Start menu / Applications / app launcher → **Open Market OS**. The app starts in the background and
opens in the browser already signed in; the first time, a setup assistant detects the computer,
checks the data sources, offers free local AI and sets start-at-sign-in and keep-awake.

## USE

[START_HERE.md](../START_HERE.md): dashboard → charts → strategy lab → paper order → a bot
(Validate → Simulate → Start) → emergency stop. Pair a phone in Settings → Devices.

## STATUS

### What was built in this mission

| Area | Delivered | Verified how |
|---|---|---|
| No-terminal desktop app | Launcher, background host, quit/restart, start at sign-in, keep-awake, diagnostics, first-run assistant | Integration tests; CI on every desktop OS |
| Installers | Windows (Inno Setup, per-user, update-safe, uninstall keeps data), macOS .app/.dmg (arm64, x86_64), Linux .deb/AppImage/tar.gz (x86_64, arm64) | CI installs each one and smoke-tests the installed app |
| Android app | QR pairing, certificate pinning, full interface, optional alerts, encrypted pairing storage | Unit tests, lint; emulator end-to-end against a real host |
| Network security | HTTPS listener for devices with a computer-made certificate; local browser stays on loopback | Integration test (fingerprint, exchange, persistence) |
| Free local AI | One-click Ollama runtime (SHA-256-verified), hardware-sized model | Fake-server tests incl. tampering; a real install on Linux |
| Live data | WebSocket streams (Coinbase, Kraken, Binance, OKX), LIVE / LIVE·poll / STALE / UNAVAILABLE labels | Unit tests; real ticks from Kraken and Coinbase |
| Layered entries | Adaptive ladders from risk budget and volatility, all-or-nothing basket risk, trailing stops | Unit tests; 11-scenario simulator |
| Bot safety | Stops work during circuit-breaker halts; no stuck orders on broker failure; auto-resume after data outages | Simulator scenarios, unit tests |
| Backups and updates | Restore from the app (undoable); verified in-app updates with backup first | Integration tests (fake release server) |
| Build pipeline | Tests + E2E, 5 desktop builds with install tests, Android with emulator test, draft release with checksums | Green run on 2026-11-22 |

### Tests (2026-11-22)

| Check | Result |
|---|---|
| `pytest` (offline) | 329 passed, 15 skipped (network tests) |
| Network tests incl. live streams | Kraken and Coinbase streams, data providers: passed |
| Browser end-to-end (`scripts/e2e.sh`) | 13/13 |
| CI: Windows x64 | build, smoke test, install, installed smoke test, update while running, uninstall (data kept): passed |
| CI: macOS arm64 and x86_64 | build, smoke test, disk image, installed smoke test: passed |
| CI: Linux x86_64 and arm64 | build, smoke test, .deb install/remove, AppImage: passed |
| CI: Android | unit tests, lint, signed build, emulator end-to-end (pairing, pinned HTTPS, wrong certificate refused): passed |

### Feature matrix

183 items ([FEATURE_MATRIX.md](FEATURE_MATRIX.md)):

| VERIFIED | TESTED | IMPLEMENTED | PARTIAL | NOT STARTED | NOT SUPPORTED | BLOCKED |
|---|---|---|---|---|---|---|
| 91 | 21 | 21 | 18 | 25 | 7 | 0 |

### Not done, and why

| Item | State | Why / what it needs |
|---|---|---|
| MetaTrader 5 and exchange live-trading adapters | NOT STARTED | Needs your MT5 terminal and demo account, or exchange testnet API keys, to build against and verify; claiming an untested broker connection would be unsafe |
| Charts: compare symbols, bar replay, Renko / point & figure, keyboard shortcuts, templates UI | NOT STARTED / PARTIAL | Not reached in this mission |
| Visual node editors (strategy, workflows), multi-symbol backtests and bots | NOT STARTED | Not reached |
| AI: agent memory, provider failover, more agent roles, task queue | NOT STARTED | Not reached; the job system now exists to build the queue on |
| Fundamental screener filters, non-US fundamentals | NOT SUPPORTED | No free licensed bulk source |
| Plugin network isolation (OS sandbox) | NOT STARTED | Process plugins can still use the network (see SECURITY_GUIDE) |

Full lists: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

### Security (unchanged claims: no security is guaranteed)

- **Added:**
  - HTTPS with certificate pinning for phones.
  - SHA-256 verification of every downloaded runtime and update.
  - Safe archive extraction (path traversal and links outside the folder are rejected; tested).
  - Pairing tokens encrypted with the Android Keystore and excluded from backups.
  - Updates take a data backup first.
- **Remains:**
  - Unsigned installers.
  - Checksums come from the same release as the files.
  - Process plugins have network access.
  - Browsers warn about the self-made certificate.
  - Stops cannot protect against gaps or a switched-off computer.
- **Android signing key:** the build workflow created it on its first run and keeps it in a
  private draft release named "Android signing key - PRIVATE, DO NOT PUBLISH". **Never publish that
  draft.** Moving it into GitHub Actions secrets is described in `android/README.md`.

## DELIVER

- **Files:** the draft release "Open Market OS 0.2.0" (above).
- **Source:** branch `ccr-80d6e707-jb6y48`.
- **Pipeline:** `.github/workflows/omos-build.yml`. Every push to the branch rebuilds, re-tests and
  replaces the draft.
- **To make the release public** (and enable in-app updates for later versions): open the draft on
  GitHub and press **Publish release**. Publishing is your decision; nothing is published
  automatically.
