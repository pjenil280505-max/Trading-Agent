# Open Market OS for Android

A companion app for phones and tablets: it pairs with Open Market OS running on your own computer
and shows its full interface over your home network. Nothing runs in a cloud.

- **Pairing:** scan the QR code from the computer's *Settings → Devices* page (or type the address and
  one-time code). The QR code carries the computer's addresses, the code and the SHA-256 fingerprint
  of the computer's certificate.
- **Encryption:** HTTPS to the computer's own certificate, pinned by fingerprint: the app accepts
  that certificate and no other (`Pinning.kt`). When you type the address instead of scanning, the
  app shows the certificate check to compare with the computer's screen before sending the code.
- **Storage:** only the pairing (addresses, fingerprint, sign-in token), encrypted with an Android
  Keystore key; excluded from backups and device transfers.
- **Alerts on this phone** (optional, from the app menu): a foreground service keeps one encrypted
  connection to the computer and shows its notifications. It reconnects by itself.

## Build

Android SDK (platform 35, build tools 35) and JDK 17+:

    cd android && ./gradlew testDebugUnitTest lintDebug assembleRelease

`scripts/android_e2e.sh` installs the APK on a running emulator and pairs it with a real host.

## Signing key

Android installs a new version over an old one only if both are signed with the same key. The CI
workflow creates that key on its first run and keeps it as two assets of a **private draft release**
named "Android signing key - PRIVATE, DO NOT PUBLISH" (draft releases are visible only to people with
write access to the repository). Never publish that draft. To use GitHub Actions secrets instead,
store the two files' contents as secrets and change the "Signing key" step of
`.github/workflows/omos-build.yml` to read them.
