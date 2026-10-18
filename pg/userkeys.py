"""Viewers' own AI keys, for a public copy (PG_USER_KEYS=on).

Stremio has nowhere to keep an addon's settings except the install link, and it sends that link with every request.
So the configure page asks the server to seal the viewer's AI settings ({base_url, key, model}) into a token, and the
install link becomes https://<host>/k/<token>/manifest.json. Nothing is stored on the server; the token works until
PG_CONFIG_SECRET changes.

The token is encrypted and signed with PG_CONFIG_SECRET, so the key can't be read from the link (or from logs, or
from the Stremio account it's saved in) without the server's secret, and it can't be tampered with. Standard library
only: HMAC-SHA256 as the keystream (counter mode) and HMAC-SHA256 again to sign (encrypt-then-MAC).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
from typing import Optional
from urllib.parse import urlsplit

log = logging.getLogger("pg.userkeys")

ENABLED = os.environ.get("PG_USER_KEYS", "off").strip().lower() == "on"
_SECRET = os.environ.get("PG_CONFIG_SECRET", "")
PREFIX = "/k/"  # install links with a viewer's key: /k/<token>/manifest.json
_NONCE, _TAG = 16, 16


def ready() -> bool:
    """Viewers' keys are on and there's a secret to seal them with."""
    return ENABLED and len(_SECRET) >= 32


def _keys() -> tuple:
    root = hashlib.sha256(_SECRET.encode()).digest()
    return (hmac.new(root, b"couch-check encrypt", hashlib.sha256).digest(),
            hmac.new(root, b"couch-check sign", hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def seal(settings: dict) -> str:
    """The settings as a URL-safe token only this server can open."""
    enc, sign = _keys()
    data = json.dumps(settings, separators=(",", ":")).encode()
    nonce = secrets.token_bytes(_NONCE)
    body = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(sign, nonce + body, hashlib.sha256).digest()[:_TAG]
    return base64.urlsafe_b64encode(nonce + body + tag).decode().rstrip("=")


def unseal(token: str) -> Optional[dict]:
    """The settings in a token, or None if it's damaged, forged or sealed with another secret."""
    if not ready() or not token or len(token) > 2000:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError:
        return None
    if len(raw) < _NONCE + _TAG + 2:
        return None
    nonce, body, tag = raw[:_NONCE], raw[_NONCE:-_TAG], raw[-_TAG:]
    enc, sign = _keys()
    if not hmac.compare_digest(tag, hmac.new(sign, nonce + body, hashlib.sha256).digest()[:_TAG]):
        return None
    try:
        settings = json.loads(bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body)))))
    except ValueError:
        return None
    return settings if isinstance(settings, dict) and all(settings.get(k) for k in ("base_url", "key", "model")) else None


def safe_base_url(url: str) -> Optional[str]:
    """The AI service address if it's an https address on the public internet, else None.

    Viewers type this in, and the server calls it, so it must never reach the server's own network.
    """
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except OSError:
        return None
    if not addresses or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        return None
    return url
