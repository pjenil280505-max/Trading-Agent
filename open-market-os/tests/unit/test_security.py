import pytest

from omos.errors import AuthRequired, PermissionDenied, RateLimited, SecretStoreError, ValidationError
from omos.security.auth import AuthService
from omos.security.secrets import SecretStore, VaultBackend


def test_vault_encrypts_and_needs_passphrase(tmp_path, monkeypatch):
    monkeypatch.delenv("OMOS_VAULT_PASSPHRASE", raising=False)
    v = VaultBackend(tmp_path / "secrets.vault")
    with pytest.raises(SecretStoreError):
        v.put("ai.openai", "sk-test-123")
    v.unlock("correct horse battery")
    v.put("ai.openai", "sk-test-1234567890")
    raw = (tmp_path / "secrets.vault").read_text()
    assert "sk-test" not in raw and "ai.openai" not in raw  # names AND values are encrypted
    v2 = VaultBackend(tmp_path / "secrets.vault")
    with pytest.raises(SecretStoreError):
        v2.unlock("wrong passphrase!!")
    v2.unlock("correct horse battery")
    assert v2.get("ai.openai") == "sk-test-1234567890"
    v2.delete("ai.openai")
    assert v2.get("ai.openai") is None
    with pytest.raises(ValidationError):
        v2.unlock("short")


def test_vault_file_permissions(tmp_path):
    import os
    import stat

    v = VaultBackend(tmp_path / "s.vault")
    v.unlock("a long enough passphrase")
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "s.vault").stat().st_mode) == 0o600


def test_secret_store_falls_back_to_vault_never_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "env passphrase 123")
    s = SecretStore(tmp_path)
    assert s.kind == "encrypted-vault" and s.status()["plaintext"] is False
    s.put("broker.demo", "secret-value")
    assert s.names() == ["broker.demo"]
    assert "secret-value" not in str(s.status())
    with pytest.raises(ValidationError):
        s.put("../etc", "x")


def test_tokens_hash_only_and_scopes(db):
    auth = AuthService(db)
    tok, info = auth.create_token("laptop", "device", ["read"])
    assert tok.startswith("omos_") and len(tok) > 40
    assert db.scalar("SELECT COUNT(*) FROM auth_tokens WHERE token_hash = ?", (tok,)) == 0  # plaintext never stored
    ti = auth.verify(tok)
    ti.require("read")
    with pytest.raises(PermissionDenied):
        ti.require("admin")
    auth.revoke(info["id"], actor="ui")
    with pytest.raises(AuthRequired):
        auth.verify(tok)


def test_expired_token_rejected(db):
    auth = AuthService(db)
    tok, info = auth.create_token("old", "device", ["read"], ttl_days=1)
    db.execute("UPDATE auth_tokens SET expires_at = 1 WHERE id = ?", (info["id"],))
    with pytest.raises(AuthRequired):
        auth.verify(tok)


def test_bootstrap_code_single_use(db):
    auth = AuthService(db)
    code = auth.create_bootstrap_code()
    tok, info = auth.exchange_code(code, device_name="browser", client="127.0.0.1")
    assert "admin" in info["scopes"] and info["kind"] == "browser"
    with pytest.raises(AuthRequired):
        auth.exchange_code(code, device_name="again", client="127.0.0.1")


def test_pairing_presets_and_expiry(db):
    auth = AuthService(db)
    p = auth.create_pairing_code("monitor", "phone", actor="ui")
    assert len(p["code"]) == 8 and p["scopes"] == ["read"]
    db.execute("UPDATE auth_codes SET expires_at = 1")
    with pytest.raises(AuthRequired):
        auth.exchange_code(p["code"], device_name="phone", client="10.0.0.5")
    with pytest.raises(ValidationError):
        auth.create_pairing_code("root", "x", actor="ui")


def test_bruteforce_lockout(db):
    auth = AuthService(db)
    for _ in range(10):
        with pytest.raises(AuthRequired):
            auth.exchange_code("WRONGCODE", device_name="x", client="10.0.0.9")
    with pytest.raises(RateLimited):
        auth.exchange_code("WRONGCODE", device_name="x", client="10.0.0.9")
    # other clients are unaffected
    with pytest.raises(AuthRequired):
        auth.exchange_code("WRONGCODE", device_name="x", client="10.0.0.10")


def test_invalid_scopes(db):
    with pytest.raises(ValidationError):
        AuthService(db).create_token("x", "device", ["root"])


def test_bad_vault_passphrase_env_keeps_host_running_with_locked_vault(tmp_path, monkeypatch, caplog):
    """Regression: a too-short OMOS_VAULT_PASSPHRASE crashed host startup with a traceback."""
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "x")
    v = VaultBackend(tmp_path / "secrets.vault")
    assert not v.unlocked
    assert "did not unlock the vault" in caplog.text
    assert not (tmp_path / "secrets.vault").exists()   # nothing written, never plaintext
