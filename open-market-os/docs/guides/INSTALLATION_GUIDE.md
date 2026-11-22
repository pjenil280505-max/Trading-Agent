# Installation guide

Download the file for your device from the project's release page, install it like any other app,
open it from your Start menu, Applications folder or app launcher. No terminal is needed for
anything in this guide except section 2 (building from source).

| Platform | File | Status in 0.2.0 |
|---|---|---|
| Windows 10/11, 64-bit | `OpenMarketOS-0.2.0-windows-x64-setup.exe` | Installed and smoke-tested automatically on Windows Server; not code-signed |
| macOS 11+ (Apple Silicon) | `OpenMarketOS-0.2.0-macos-arm64.dmg` | Installed and smoke-tested automatically on macOS 15; not notarized |
| macOS 11+ (Intel) | `OpenMarketOS-0.2.0-macos-x86_64.dmg` | Installed and smoke-tested automatically on macOS 15 Intel; not notarized |
| Ubuntu/Debian/Mint, 64-bit | `open-market-os_0.2.0_amd64.deb` | Installed, smoke-tested and removed automatically on Ubuntu 22.04 |
| Other Linux, 64-bit | `OpenMarketOS-0.2.0-x86_64.AppImage` | Smoke-tested automatically on Ubuntu 22.04 |
| Linux on 64-bit ARM | `open-market-os_0.2.0_arm64.deb`, `OpenMarketOS-0.2.0-aarch64.AppImage` | Smoke-tested automatically on Ubuntu 22.04 ARM |
| Android 7+ | `OpenMarketOS-0.2.0-android.apk` | Tested on an Android 14 emulator; a companion for a computer running Open Market OS |
| iOS | — | No app; Safari works as a screen for your computer (section 5) |

Check a download (optional): every file's SHA-256 is in `SHA256SUMS.txt` on the release page.

## 1. Install

**Windows.** Run the setup file. If SmartScreen says "Windows protected your PC", choose **More info
→ Run anyway** (the installer is not code-signed). No administrator rights are needed: it installs
for your user into `%LOCALAPPDATA%\Programs\Open Market OS`, with a Start-menu entry and (optionally)
a desktop shortcut and start-at-sign-in.

**macOS.** Open the disk image and drag **Open Market OS** onto **Applications**. The first time you
open it, macOS says it cannot check the app: click **Done**, open **System Settings → Privacy &
Security**, scroll down and click **Open Anyway**. This is needed once. The app has no Dock icon:
it runs in the background and its window is the browser tab it opens.

**Ubuntu, Debian, Mint.** Double-click the `.deb` file and choose **Install** in the Software app.
Open Market OS then appears in the applications menu.

**Other Linux (AppImage).** Right-click the file → Properties → Permissions → allow executing it as a
program, then double-click it.

**Android.** See section 5.

**When it opens** the first time, a setup assistant detects your computer, checks the free data
sources, offers free local AI, and asks whether the app should start when you sign in. Everything
starts in paper trading with simulated money.

## 2. From source (developers)

Prerequisites: **Python 3.11+**, **Node.js 20+** (only to build the web UI), Git.

```bash
git clone <repository url>
cd open-market-os
python3 -m venv .venv
. .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
scripts/build_web.sh
omos start
```

To build the installers yourself: `python packaging/build_app.py`, then the scripts in
`packaging/windows`, `packaging/macos` or `packaging/linux` on that operating system (see
`.github/workflows/omos-build.yml`, which does exactly this).

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

1. On the computer: **Settings → Devices → Allow devices on my network**. The app restarts and shows
   its network address(es) and a **certificate check** code. Devices on your network now reach it
   over HTTPS, encrypted with a certificate made on your computer. Use this only on a network you
   trust, and never forward the port to the internet: there is no cloud relay.
2. Choose permissions for the device (e.g. `paper-control`) and press **Create one-time code**
   (valid 5 minutes, one use). A QR code appears.
3. **Android:** install `OpenMarketOS-0.2.0-android.apk` on the phone (open the file; allow
   installing from this source when asked), open it and tap **Scan pairing code**. The phone checks
   the computer's certificate from the QR code and accepts no other. Without the camera: type the
   address and code; the app then shows the certificate check to compare with the computer's screen.
4. **iPhone, iPad or any browser:** open the address shown (e.g. `https://192.168.1.20:8766`). The
   browser warns that the certificate is not trusted, because it was made on your computer. Check that
   its SHA-256 fingerprint starts with the certificate check shown on the computer, accept it, and type
   the code.

**Alerts on the phone** (Android): in the app's menu (☰ More → Phone app settings), turn on
**Alerts on this phone**. It keeps one connection to your computer open and uses some battery.

## 6. Start automatically (for 24/7 bots and alerts)

In **Settings → General**: **Start Open Market OS in the background when I sign in** (Windows: your
sign-in; macOS: login item; Linux desktops: autostart entry). The same option is offered by the
setup assistant and, on Windows, by the installer. **Keep this computer awake while bots are
running** stops the computer from sleeping (the screen may still turn off).

Bots run only while the computer is on and online. On a laptop, keep it plugged in. Power settings per
operating system: [bot guide](BOT_GUIDE.md#running-247-on-a-home-computer).

Headless Linux servers (no desktop session): `omos service install` writes a systemd user unit.

## 7. Update

**Settings → General → Updates → Check for updates.** When a newer published release exists, **Download,
verify and install**: the installer for your computer is downloaded, checked against the release's
SHA-256 checksums, all your data is backed up first ("before an update"), then:
- Windows: the installer runs, stops the app, updates it and opens it again.
- AppImage: the file replaces itself and the app restarts.
- macOS and `.deb`: the verified file opens; drag the app to Applications (replace) or confirm in the
  package installer.

Installing a newer download over the old version by hand also works and keeps your data.
To go back: restore the "before an update" backup (section 8) and install the older version.

## 8. Back up and restore

- Automatic: one backup a day (keeps 14). Change it in **Settings → Backups**.
- Manual: **Settings → Backups → Back up now**.
- **Restore:** **Settings → Backups → Restore…** next to a backup. The app saves your current data
  first ("before a restore"), restarts and comes back with the backup's data. Restoring the
  "before a restore" backup undoes it.
- Move to another computer: **Export everything (zip)** (secrets are not included: re-enter API keys).
- If the database is found damaged at start-up, the newest valid backup is restored automatically.

## 9. Uninstall

| System | Data folder (kept when you uninstall) |
|---|---|
| Windows | `%LOCALAPPDATA%\OpenMarketOS` |
| macOS | `~/Library/Application Support/OpenMarketOS` |
| Linux | `~/.local/share/open-market-os` |

- **Windows:** Settings → Apps → Open Market OS → Uninstall. At the end you are asked whether to
  delete your data too (default: keep it).
- **macOS:** quit the app (Settings → General → Quit), then drag it from Applications to the Bin.
- **Ubuntu/Debian:** remove "Open Market OS" in the Software app.
- **AppImage:** quit the app and delete the file.
- To delete your data as well, delete the data folder above after uninstalling.
