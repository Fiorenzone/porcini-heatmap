"""Cifra la mappa a riposo. La chiave non sta nel repo: env PORCINI_KEY o .porcini_key."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
VAULT = STATIC / "vault"
MAGIC = b"PORC1"
_KDF = 200_000

_html: bytes | None = None
_js: bytes | None = None
_password: bytes | None = None
_tokens: set[str] = set()


def passphrase() -> str | None:
    env = os.environ.get("PORCINI_KEY")
    if env:
        return env
    path = ROOT / ".porcini_key"
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    return None


def _derive(key: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, _KDF, dklen=32)


def seal(data: bytes, key: str) -> bytes:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ct = AESGCM(_derive(key, salt)).encrypt(nonce, data, None)
    return MAGIC + salt + nonce + ct


def open_vault(blob: bytes, key: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < 5 + 16 + 12 + 16:
        raise ValueError("vault")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    return AESGCM(_derive(key, salt)).decrypt(nonce, ct, None)


def ensure_sources(key: str) -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "heatmap.js"):
        src = STATIC / name
        dst = VAULT / f"{name}.enc"
        if not src.is_file():
            continue
        if dst.is_file() and src.stat().st_mtime <= dst.stat().st_mtime:
            continue
        dst.write_bytes(seal(src.read_bytes(), key))


def seal_password(password: str, key: str) -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    (VAULT / "gate.enc").write_bytes(seal(password.encode("utf-8"), key))


def startup() -> None:
    global _html, _js, _password
    key = passphrase()
    if not key:
        print("PORCINI_KEY mancante", flush=True)
        return
    ensure_sources(key)
    try:
        _html = open_vault((VAULT / "index.html.enc").read_bytes(), key)
        _js = open_vault((VAULT / "heatmap.js.enc").read_bytes(), key)
        _password = open_vault((VAULT / "gate.enc").read_bytes(), key)
    except Exception:
        _html = _js = _password = None
        print("vault illeggibile", flush=True)
        return
    print("vault ok", flush=True)


def password_ok(got: str) -> bool:
    if _password is None or not isinstance(got, str):
        return False
    return hmac.compare_digest(
        hashlib.sha256(_password).digest(),
        hashlib.sha256(got.encode("utf-8")).digest(),
    )


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    if len(_tokens) > 64:
        _tokens.pop()
    return token


def token_ok(header: str | None, query: str) -> bool:
    token = header or ""
    if not token and query:
        from urllib.parse import parse_qs

        token = (parse_qs(query).get("t") or [""])[0]
    return bool(token) and token in _tokens


def app_js() -> bytes | None:
    return _js


def render_app(token: str) -> bytes:
    if _html is None or _js is None:
        raise RuntimeError("vault")
    boot = (
        "<script>(function(){var TOKEN="
        + json.dumps(token)
        + ";var _fetch=window.fetch.bind(window);"
        + "window.fetch=function(input,init){"
        + "var url=typeof input==='string'?input:((input&&input.url)||'');"
        + "var abs=/^[a-z][a-z0-9+.-]*:/i.test(url);"
        + "if(abs&&url.indexOf(location.origin)!==0)return _fetch(input,init);"
        + "var next=init?Object.assign({},init):{};"
        + "var headers=new Headers(next.headers||{});"
        + "headers.set('X-Porcini-Token',TOKEN);next.headers=headers;"
        + "return _fetch(input,next);};})();</script>\n<script>"
        + _js.decode("utf-8")
        + "</script>"
    )
    needle = '<script src="./heatmap.js"></script>'
    html = _html.decode("utf-8")
    if needle not in html:
        raise RuntimeError("script mappa assente")
    return html.replace(needle, boot, 1).encode("utf-8")
