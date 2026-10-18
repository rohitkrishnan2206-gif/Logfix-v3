import time
from app.security import (SecretBox, LoginLimiter, hash_password, verify_password, create_token, decode_token,
                          check_password_policy, role_allows)
from cryptography.fernet import Fernet
from app.redact import redact


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("Secret12345", 1000), hash_password("Secret12345", 1000)
    assert h1 != h2 and verify_password("Secret12345", h1) and not verify_password("wrong", h1)
    assert not verify_password("x", "garbage")


def test_password_policy():
    assert check_password_policy("short1A")
    assert check_password_policy("alllowercase123")
    assert check_password_policy("GoodPassw0rd") is None


def test_jwt_tamper_and_expiry():
    t = create_token("s" * 32, 1, "admin", 5)
    assert decode_token("s" * 32, t)["sub"] == "1"
    assert decode_token("other" * 8, t) is None
    assert decode_token("s" * 32, create_token("s" * 32, 1, "admin", -1)) is None


def test_secretbox_encrypts_and_rejects_wrong_key():
    box = SecretBox(Fernet.generate_key().decode())
    tok = box.encrypt({"k": "s3cr3t-value"})
    assert "s3cr3t" not in tok and box.decrypt(tok) == {"k": "s3cr3t-value"}
    assert SecretBox(Fernet.generate_key().decode()).decrypt(tok) == {}


def test_limiter_locks_after_five():
    l = LoginLimiter(5, 300)
    for _ in range(5):
        l.fail("a")
    assert l.locked("a") and not l.locked("b")
    l.reset("a")
    assert not l.locked("a")


def test_roles():
    assert role_allows("admin", "viewer") and role_allows("analyst", "analyst") and not role_allows("viewer", "analyst")


def test_redaction():
    out = redact("password=hunter2 key AKIAABCDEFGHIJKLMNOP mail a@b.com card 4111 1111 1111 1111 order 1234567890123")
    assert "hunter2" not in out and "AKIA" not in out and "a@b.com" not in out
    assert "4111" not in out and "1234567890123" in out  # non-Luhn number kept


def test_secretbox_accepts_long_passphrase_rejects_short():
    import pytest
    box = SecretBox("a-long-random-passphrase-with-at-least-32-chars-123")
    assert box.decrypt(box.encrypt({"a": 1})) == {"a": 1}
    with pytest.raises(ValueError, match="at least 32"):
        SecretBox("short")
