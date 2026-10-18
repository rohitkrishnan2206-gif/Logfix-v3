"""Authentication, password hashing, JWT and encrypted secret storage."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.fernet import Fernet, InvalidToken

PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 10
ROLE_ORDER = {"viewer": 1, "analyst": 2, "admin": 3}


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def check_password_policy(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if password.lower() == password or password.upper() == password or not any(ch.isdigit() for ch in password):
        return "Password must mix upper case, lower case and a digit"
    return None


def create_token(secret: str, user_id: int, role: str, ttl_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def role_allows(user_role: str, required: str) -> bool:
    return ROLE_ORDER.get(user_role, 0) >= ROLE_ORDER[required]


def _load_or_create(path: Path, factory) -> str:
    if path.exists():
        return path.read_text().strip()
    value = factory()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def load_jwt_secret(data_dir: Path, configured: str | None) -> str:
    return configured or _load_or_create(data_dir / "jwt_secret", lambda: secrets.token_urlsafe(48))


class SecretBox:
    """Fernet (AES-128-CBC + HMAC) envelope for source credentials stored in the DB.
    The key comes from LOGFIX_SECRET_KEY or a 0600 file in the data dir. For production
    keep the key in KMS / Key Vault / Secrets Manager and inject it as an env var."""

    def __init__(self, key: str):
        raw = key.encode() if isinstance(key, str) else key
        try:
            self._f = Fernet(raw)
        except ValueError:
            # Not a Fernet key. Accept any long passphrase by hashing it to 32 bytes.
            # Short values are refused: hashing a weak secret does not make it strong.
            if len(raw) < 32:
                raise ValueError(
                    "LOGFIX_SECRET_KEY must be either a Fernet key or a random passphrase of at least 32 characters. "
                    "Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\""
                ) from None
            self._f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))

    @staticmethod
    def from_settings(data_dir: Path, configured: str | None) -> "SecretBox":
        key = configured or _load_or_create(data_dir / "secret.key", lambda: Fernet.generate_key().decode())
        return SecretBox(key)

    def encrypt(self, data: dict) -> str:
        return self._f.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str | None) -> dict:
        if not token:
            return {}
        try:
            return json.loads(self._f.decrypt(token.encode()))
        except (InvalidToken, ValueError):
            return {}


class LoginLimiter:
    """Tiny in-memory brute-force guard: 5 failures per key in 5 minutes -> locked."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max = max_failures
        self.window = window_seconds
        self._fails: dict[str, list[float]] = {}

    def _prune(self, key: str) -> list[float]:
        cutoff = time.time() - self.window
        self._fails[key] = [t for t in self._fails.get(key, []) if t > cutoff]
        return self._fails[key]

    def locked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max

    def fail(self, key: str) -> None:
        self._prune(key).append(time.time())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
