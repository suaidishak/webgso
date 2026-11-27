"""Encryption at rest for plant API keys (Fernet: AES-128-CBC + HMAC-SHA256).

Beta stand-in for AWS Secrets Manager: the ciphertext lives in the plant table and the
encryption key comes from PLANT_KEY_SECRET.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

log = logging.getLogger("gso.secrets")
_warned = False


def _fernet() -> Fernet:
    global _warned
    cfg = get_settings()
    secret = cfg.plant_key_secret or cfg.jwt_secret
    if not cfg.plant_key_secret and not _warned:
        log.warning("PLANT_KEY_SECRET is not set; deriving the plant key cipher from JWT_SECRET")
        _warned = True
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("A stored plant API key could not be decrypted (was PLANT_KEY_SECRET changed?)")
        return None


def hint(plain: str) -> str:
    return plain[-4:] if len(plain) >= 8 else ""


def auth_parts(method: str | None, name: str | None, key: str | None) -> tuple[dict, dict]:
    """(headers, query params) to send for a plant's auth settings."""
    if not key or method in (None, "none"):
        return {}, {}
    if method == "header":
        return {name or "X-API-Key": key}, {}
    if method == "query":
        return {}, {name or "apiKey": key}
    return {}, {}


def scrub(text: str | None, key: str | None) -> str | None:
    """Remove a key from error text (httpx errors can echo the full URL)."""
    if text and key:
        return text.replace(key, "***")
    return text
