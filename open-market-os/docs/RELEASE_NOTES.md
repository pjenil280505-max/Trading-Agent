# Open Market OS 0.2.0

**Download, install, open, use: no terminal.** Paper trading only: real-money trading stays switched
off, and no live broker connection ships in this release.

## Downloads

| Your device | File | How to install |
|---|---|---|
| Windows 10/11 (64-bit) | `OpenMarketOS-0.2.0-windows-x64-setup.exe` | Run it. If SmartScreen says "Windows protected your PC", choose More info → Run anyway (the installer is not code-signed). No administrator rights needed. |
| Mac with Apple Silicon (M1–M4) | `OpenMarketOS-0.2.0-macos-arm64.dmg` | Open it, drag Open Market OS to Applications. First open: System Settings → Privacy & Security → Open Anyway (not notarized). |
| Intel Mac | `OpenMarketOS-0.2.0-macos-x86_64.dmg` | As above. |
| Ubuntu, Debian, Mint (64-bit PC) | `open-market-os_0.2.0_amd64.deb` | Double-click to open it in the Software app, then Install. |
| Other Linux (64-bit PC) | `OpenMarketOS-0.2.0-x86_64.AppImage` | Right-click → Properties → allow executing, then double-click. |
| Linux on 64-bit ARM (built and tested on Ubuntu 22.04 ARM) | `open-market-os_0.2.0_arm64.deb` or `OpenMarketOS-0.2.0-aarch64.AppImage` | As above. |
| Android phone or tablet | `OpenMarketOS-0.2.0-android.apk` | Open the file on the phone and allow installing from this source. It connects to Open Market OS on your computer. |

`SHA256SUMS.txt` lists the SHA-256 checksum of every file. Linux `.tar.gz` archives are included for
manual installs.

## New in 0.2.0

- **No terminal anywhere.** Installers for Windows, macOS and Linux with menu and desktop entries.
  The icon starts the app in the background and opens it in your browser, already signed in. Quit,
  restart, start at sign-in, keep the computer awake while bots run, diagnostics: all in the app.
- **First-run setup assistant:** detects the computer, checks the free data sources, offers free
  local AI, and sets the background options.
- **One-click free local AI:** downloads the official Ollama runtime, checks it against its
  published SHA-256 checksum, picks a model that suits the computer's memory and graphics card,
  and connects it. If you already have Ollama, it is used as-is.
- **Android app:** scan the QR code on the computer's Settings → Devices page; the phone shows the
  whole app over your home network, encrypted with the computer's own certificate, which the app
  pins. Optional alerts on the phone.
- **Live streaming prices** from Coinbase, Kraken, Binance and OKX. Every price carries a label:
  LIVE, LIVE·poll (polled, with its interval), DELAYED, HISTORICAL, SIMULATED, STALE or UNAVAILABLE.
  Bots never act on stale data and resume by themselves when fresh data returns.
- **Adaptive layered entries:** a bot type whose ladder of entries follows a risk budget and the
  current volatility, with no fixed number of layers. All layers are checked together before
  anything is placed; if the whole ladder does not fit your limits, none of it is placed.
- **Simulate before you start:** stress-test a bot on 11 market scenarios (crash, gaps, spread
  blow-outs, thin markets with partial fills, data outages, restarts, broker rejections).
- **Safer automation:** after a circuit breaker stops trading, a bot's stop-loss still closes its
  position; a broker failure can no longer leave an order stuck; tiny leftover DCA orders are gone.
- **Restore a backup from the app** (and undo it). **Update from the app:** the installer for your
  computer is downloaded, checked against the release's checksums, your data is backed up first,
  then the update installs.
- **Network access** for phones and tablets is a switch in Settings → Devices, and uses HTTPS.
- **Charts:** compare another symbol (percentage change, own pane), keyboard shortcuts (press ? on a
  chart), and saved chart templates.
- **AI failover:** if a provider does not answer, your other enabled providers are tried, local ones
  first; a free local request never turns into a paid one unless you allow it.

## Tested how

Automated, on 2026-11-22:
- 330 automated tests pass, plus live-network tests.
- 13/13 browser end-to-end tests pass.
- The build pipeline installs each desktop package on a fresh GitHub-hosted machine and smoke-tests
  the installed app: open → background service → interface → API → restart → quit. This ran on
  Windows x64, macOS 15 (Apple Silicon and Intel) and Ubuntu (x86_64 and ARM).
- On Windows it also tests updating while the app runs and uninstalling with your data kept.
- The Android app is tested on an Android 14 emulator against a real host.

What was **not** tested:
- No person used the installers on their own Windows PC or Mac.
- The Android app was not tried on a physical phone.
- Starting at sign-in, keeping the computer awake and phone alerts were not observed working.
- The local AI runtime install was exercised on Linux only.

## Known limits

- Unsigned installers: Windows SmartScreen and macOS Gatekeeper ask once.
- No live broker or MetaTrader 5 connection yet: real-money trading is not possible.
- Stops are executed by the app: they cannot protect against price gaps, and need the app running.

See KNOWN_ISSUES.md and FEATURE_MATRIX.md in the repository for everything else.
