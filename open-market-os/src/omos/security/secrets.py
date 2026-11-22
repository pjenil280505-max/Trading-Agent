"""Secret storage. Never plaintext.

Backends, in order of preference:

1. **OS keyring** via ``keyring``: Windows Credential Manager, macOS Keychain, Secret Service
   (GNOME Keyring / KWallet) on Linux. Used when a real backend is available.
2. **Encrypted vault file** (``secrets.vault``): AES-256-GCM, key derived from a user passphrase
   with scrypt (n=2^15, r=8, p=1). The passphrase comes from ``unlock()`` (UI/CLI prompt) or the
   ``OMOS_VAULT_PASSPHRASE`` environment variable on headless hosts. Without a passphrase the
   vault stays locked and secrets are unavailable — the app never falls back to plaintext.

Secret values are never logged, never returned by list(), and never sent to AI models.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets as pysecrets
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from omos.errors import SecretStoreError, ValidationError

log = logging.getLogger(__name__)

SERVICE = "open-market-os"
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def _check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValidationError("secret names use lowercase letters, digits, '.', '_' or '-' (max 80)")
    return name


class KeyringBackend:
    kind = "os-keyring"

    def __init__(self, index_path: Path):
        import keyring

        self._kr = keyring
        self.index_path = index_path  # names only (never values), so the UI can list what exists

    @staticmethod
    def available() -> bool:
        try:
            import keyring
            from keyring.backends import fail

            kr = keyring.get_keyring()
            name = type(kr).__module__ + "." + type(kr).__name__
            if isinstance(kr, fail.Keyring) or "null" in name.lower() or "chainer" in name.lower() and not getattr(kr, "backends", None):
                return False
            # some backends exist but cannot store (e.g. no D-Bus session); probe with a write
            probe = f"__probe_{pysecrets.token_hex(4)}"
            kr.set_password(SERVICE, probe, "x")
            ok = kr.get_password(SERVICE, probe) == "x"
            kr.delete_password(SERVICE, probe)
            return ok
        except Exception:
            return False

    def _names(self) -> set[str]:
        try:
            return set(json.loads(self.index_path.read_text()))
        except (OSError, ValueError):
            return set()

    def _save_names(self, names: set[str]) -> None:
        self.index_path.write_text(json.dumps(sorted(names)))

    def put(self, name: str, value: str) -> None:
        self._kr.set_password(SERVICE, name, value)
        self._save_names(self._names() | {name})

    def get(self, name: str) -> str | None:
        return self._kr.get_password(SERVICE, name)

    def delete(self, name: str) -> None:
        try:
            self._kr.delete_password(SERVICE, name)
        except Exception:  # already gone
            pass
        self._save_names(self._names() - {name})

    def names(self) -> list[str]:
        return sorted(self._names())

    @property
    def unlocked(self) -> bool:
        return True


class VaultBackend:
    kind = "encrypted-vault"
    VERSION = 1

    def __init__(self, path: Path):
        self.path = path
        self._key: bytes | None = None
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()
        env = os.environ.get("OMOS_VAULT_PASSPHRASE")
        if env:
            try:
                self.unlock(env)
            except (SecretStoreError, ValidationError) as exc:
                # a bad environment variable must not stop the host: the vault simply stays locked
                log.error("OMOS_VAULT_PASSPHRASE did not unlock the vault (%s); secrets stay locked until unlocked "
                          "in Settings > Secrets", getattr(exc, "message", exc))

    @staticmethod
    def _derive(passphrase: str, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def unlock(self, passphrase: str) -> None:
        if len(passphrase) < 8:
            raise ValidationError("the vault passphrase must be at least 8 characters")
        with self._lock:
            if not self.path.exists():
                salt = os.urandom(16)
                self._key = self._derive(passphrase, salt)
                self._salt = salt
                self._data = {}
                self._flush()
                return
            blob = json.loads(self.path.read_text())
            salt = base64.b64decode(blob["salt"])
            key = self._derive(passphrase, salt)
            try:
                plain = AESGCM(key).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), b"omos-vault-v1")
            except InvalidTag as exc:
                raise SecretStoreError("wrong vault passphrase") from exc
            self._key, self._salt, self._data = key, salt, json.loads(plain)

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._data = {}

    def _flush(self) -> None:
        if self._key is None:  # explicit check: asserts vanish under `python -O`
            raise SecretStoreError("the secret vault is locked; nothing was written", code="vault_locked")
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, json.dumps(self._data).encode(), b"omos-vault-v1")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": self.VERSION, "kdf": "scrypt-n32768-r8-p1", "salt": base64.b64encode(self._salt).decode(),
                                   "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}))
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def _require(self) -> None:
        if self._key is None:
            raise SecretStoreError("the secret vault is locked; unlock it in Settings > Security (or set OMOS_VAULT_PASSPHRASE)",
                                   code="vault_locked")

    def put(self, name: str, value: str) -> None:
        with self._lock:
            self._require()
            self._data[name] = value
            self._flush()

    def get(self, name: str) -> str | None:
        with self._lock:
            self._require()
            return self._data.get(name)

    def delete(self, name: str) -> None:
        with self._lock:
            self._require()
            self._data.pop(name, None)
            self._flush()

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._data) if self._key else []


class SecretStore:
    def __init__(self, data_dir: Path, *, prefer_keyring: bool = True):
        self.data_dir = data_dir
        if prefer_keyring and os.environ.get("OMOS_DISABLE_KEYRING") != "1" and KeyringBackend.available():
            self.backend: KeyringBackend | VaultBackend = KeyringBackend(data_dir / "secrets.index.json")
        else:
            self.backend = VaultBackend(data_dir / "secrets.vault")

    @property
    def kind(self) -> str:
        return self.backend.kind

    def status(self) -> dict:
        return {"backend": self.kind, "unlocked": self.backend.unlocked, "count": len(self.backend.names()),
                "plaintext": False}

    def unlock(self, passphrase: str) -> None:
        if isinstance(self.backend, VaultBackend):
            self.backend.unlock(passphrase)

    def put(self, name: str, value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 10_000:
            raise ValidationError("secret value must be 1–10000 characters")
        self.backend.put(_check_name(name), value)

    def get(self, name: str) -> str | None:
        return self.backend.get(_check_name(name))

    def delete(self, name: str) -> None:
        self.backend.delete(_check_name(name))

    def names(self) -> list[str]:
        return self.backend.names()
