"""Authentication: per-device bearer tokens, one-time bootstrap/pairing codes, lockout.

* Tokens look like ``omos_<43 url-safe chars>`` (256 bits). Only their SHA-256 is stored.
* Scopes:
    read        view market data, charts, portfolio, bots
    trade:paper submit paper orders, control paper bots, create alerts
    trade:live  live orders (still gated by settings + risk engine)
    ai          run AI analyses (may incur provider cost)
    admin       settings, secrets, plugins, MCP config, risk limits, tokens, backups
    mcp         the MCP bridge; individual MCP tools have their own permission matrix
* The browser on the host machine gets a token by exchanging a bootstrap code that
  ``omos open`` / ``omos start`` prints (valid 10 minutes, single use).
* Other devices pair with a short code shown in Settings > Devices (valid 5 minutes, single use).
* Failed code/token attempts are rate-limited per client address (lockout after 10 in 10 minutes).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from omos.errors import AuthRequired, PermissionDenied, RateLimited, ValidationError
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

ALL_SCOPES = ("read", "trade:paper", "trade:live", "ai", "admin", "mcp")
BROWSER_SCOPES = ["read", "trade:paper", "ai", "admin"]
PAIRING_PRESETS = {
    "monitor": ["read"],
    "paper-control": ["read", "trade:paper"],
    "full": ["read", "trade:paper", "ai", "admin"],
}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass
class TokenInfo:
    id: int
    name: str
    kind: str
    scopes: list[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise PermissionDenied(f"this device/token lacks the '{scope}' permission")

    @property
    def actor(self) -> str:
        if self.kind == "mcp":
            return f"mcp:{self.id}"
        if self.kind in ("browser", "device", "cli"):
            return f"ui:{self.id}"
        return f"api:{self.id}"


class AttemptLimiter:
    def __init__(self, max_failures: int = 10, window_s: float = 600):
        self.max = max_failures
        self.window = window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            q = self._fails[key]
            cutoff = time.monotonic() - self.window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max:
                raise RateLimited("too many failed attempts; wait 10 minutes and try again")

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


class AuthService:
    def __init__(self, db: Database):
        self.db = db
        self.limiter = AttemptLimiter()
        self._cache: dict[str, tuple[TokenInfo, float]] = {}

    # -- tokens ----------------------------------------------------------------------
    def create_token(self, name: str, kind: str, scopes: list[str], *, ttl_days: float | None = 90,
                     actor: str = "system") -> tuple[str, dict[str, Any]]:
        bad = [s for s in scopes if s not in ALL_SCOPES]
        if bad or not scopes:
            raise ValidationError(f"invalid scopes: {', '.join(bad) or '(none)'}")
        if kind not in ("browser", "device", "mcp", "cli", "api"):
            raise ValidationError("invalid token kind")
        token = "omos_" + secrets.token_urlsafe(32)
        expires = now_ms() + int(ttl_days * 86_400_000) if ttl_days else None
        cur = self.db.execute(
            "INSERT INTO auth_tokens(token_hash, name, kind, scopes, created_at, expires_at) VALUES (?,?,?,?,?,?)",
            (_hash(token), name[:80], kind, dumps(sorted(set(scopes))), now_ms(), expires))
        self.db.audit(actor, "token.create", str(cur.lastrowid), {"name": name, "kind": kind, "scopes": scopes})
        return token, self.token_row(int(cur.lastrowid))

    def token_row(self, token_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT id, name, kind, scopes, created_at, expires_at, last_used_at, revoked_at "
                                "FROM auth_tokens WHERE id = ?", (token_id,))
        if row:
            row["scopes"] = loads(row["scopes"], [])
        return row or {}

    def list_tokens(self) -> list[dict[str, Any]]:
        return [self.token_row(r["id"]) for r in self.db.query("SELECT id FROM auth_tokens ORDER BY id DESC")]

    def revoke(self, token_id: int, actor: str) -> None:
        self.db.execute("UPDATE auth_tokens SET revoked_at = ? WHERE id = ?", (now_ms(), token_id))
        self._cache.clear()
        self.db.audit(actor, "token.revoke", str(token_id))

    def verify(self, token: str | None, *, client: str = "local") -> TokenInfo:
        if not token:
            raise AuthRequired("sign in required: open the link printed by 'omos open' or pair this device")
        self.limiter.check(client)
        h = _hash(token)
        hit = self._cache.get(h)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        row = self.db.query_one("SELECT * FROM auth_tokens WHERE token_hash = ?", (h,))
        if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] < now_ms()):
            self.limiter.fail(client)
            raise AuthRequired("this sign-in has expired or was revoked; pair the device again")
        # constant-time comparison of the stored hash (defence in depth; lookup was by hash)
        if not hmac.compare_digest(row["token_hash"], h):
            raise AuthRequired("invalid token")
        info = TokenInfo(row["id"], row["name"], row["kind"], loads(row["scopes"], []))
        if not row["last_used_at"] or now_ms() - row["last_used_at"] > 60_000:
            self.db.execute("UPDATE auth_tokens SET last_used_at = ? WHERE id = ?", (now_ms(), row["id"]))
        self._cache[h] = (info, time.monotonic() + 30)
        return info

    # -- one-time codes ----------------------------------------------------------------------
    def _new_code(self, purpose: str, scopes: list[str], ttl_s: int, label: str, length: int) -> str:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        self.db.execute("INSERT INTO auth_codes(code_hash, purpose, scopes, label, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                        (_hash(code), purpose, dumps(scopes), label[:80], now_ms(), now_ms() + ttl_s * 1000))
        return code

    def create_bootstrap_code(self) -> str:
        return self._new_code("bootstrap", BROWSER_SCOPES, 600, "local browser", 24)

    def create_pairing_code(self, preset: str, label: str, actor: str) -> dict[str, Any]:
        if preset not in PAIRING_PRESETS:
            raise ValidationError(f"preset must be one of {', '.join(PAIRING_PRESETS)}")
        code = self._new_code("pairing", PAIRING_PRESETS[preset], 300, label or "paired device", 8)
        self.db.audit(actor, "pairing.code", label, {"preset": preset})
        return {"code": code, "expires_in_s": 300, "scopes": PAIRING_PRESETS[preset]}

    def exchange_code(self, code: str, *, device_name: str, client: str) -> tuple[str, dict[str, Any]]:
        self.limiter.check(client)
        code = (code or "").strip().upper().replace("-", "").replace(" ", "")
        row = self.db.query_one("SELECT * FROM auth_codes WHERE code_hash = ?", (_hash(code),)) if code else None
        if not row or row["used_at"] or row["expires_at"] < now_ms():
            self.limiter.fail(client)
            self.db.audit(f"client:{client}", "auth.code_failed", None, {"reason": "invalid/expired/used"}, ip=client)
            raise AuthRequired("that code is invalid, expired or already used")
        self.db.execute("UPDATE auth_codes SET used_at = ? WHERE code_hash = ?", (now_ms(), row["code_hash"]))
        self.limiter.reset(client)
        kind = "browser" if row["purpose"] == "bootstrap" else "device"
        ttl = 30 if kind == "browser" else 90
        token, info = self.create_token(device_name or row["label"] or kind, kind, loads(row["scopes"], ["read"]),
                                        ttl_days=ttl, actor=f"client:{client}")
        self.db.audit(f"client:{client}", "auth.code_exchanged", str(info["id"]), {"purpose": row["purpose"]}, ip=client)
        return token, info

    def cleanup(self) -> None:
        self.db.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now_ms() - 86_400_000,))
