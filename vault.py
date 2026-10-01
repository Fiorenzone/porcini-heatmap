"""Cifra formula, modello e dati a riposo. Chiave fuori dal repo: PORCINI_KEY o .porcini_key."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
VAULT = STATIC / "vault"
CORE = ROOT / "secret" / "core.enc"
MAGIC = b"PORC1"
_KDF = 200_000
PUBLIC_PY = frozenset({"vault.py", "boot.py"})
DATA_FILES = (
    "data/forest/forest_grid.bin",
    "data/forest/forest_grid.json",
    "data/soil/soil_grid.bin",
    "data/soil/soil_grid.json",
    "data/soil/jja_heat.json",
    "data/cover/cover_grid.bin",
    "data/cover/cover_grid.json",
    "data/arpa/erg5_cells.csv",
)
_core_src: dict[str, bytes] = {}

_html: bytes | None = None
_js: bytes | None = None
_password: bytes | None = None
_tokens: set[str] = set()


def passphrase() -> str | None:
    env = (os.environ.get("PORCINI_KEY") or "").strip()
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


def _pack(files: dict[str, bytes]) -> bytes:
    out = bytearray()
    for name in sorted(files):
        nb = name.encode("utf-8")
        data = files[name]
        out += struct.pack(">H", len(nb))
        out += nb
        out += struct.pack(">I", len(data))
        out += data
    return bytes(out)


def _unpack(blob: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    i = 0
    n = len(blob)
    while i < n:
        if i + 2 > n:
            raise ValueError("vault")
        (ln,) = struct.unpack_from(">H", blob, i)
        i += 2
        if ln <= 0 or i + ln + 4 > n:
            raise ValueError("vault")
        name = blob[i : i + ln].decode("utf-8")
        i += ln
        (dn,) = struct.unpack_from(">I", blob, i)
        i += 4
        if i + dn > n:
            raise ValueError("vault")
        files[name] = blob[i : i + dn]
        i += dn
    return files


def _core_inputs() -> dict[str, bytes] | None:
    files: dict[str, bytes] = {}
    for path in sorted(ROOT.glob("*.py")):
        if path.name in PUBLIC_PY:
            continue
        files[path.name] = path.read_bytes()
    if not files:
        return None
    missing = [rel for rel in DATA_FILES if not (ROOT / rel).is_file()]
    if missing:
        print("core incompleto", flush=True)
        return None
    for rel in DATA_FILES:
        files[rel] = (ROOT / rel).read_bytes()
    return files


def refresh_core() -> None:
    key = passphrase()
    files = _core_inputs()
    if not key or files is None:
        return
    CORE.parent.mkdir(parents=True, exist_ok=True)
    if CORE.is_file():
        newest = max((ROOT / name).stat().st_mtime for name in files)
        if newest <= CORE.stat().st_mtime:
            return
    CORE.write_bytes(seal(_pack(files), key))
    print("core aggiornato", flush=True)


class _Loader:
    def __init__(self, source: bytes, origin: str) -> None:
        self.source = source
        self.origin = origin

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        module.__file__ = self.origin
        exec(compile(self.source, self.origin, "exec"), module.__dict__)


class _Finder:
    def find_spec(self, fullname, path, target=None):
        if path is not None:
            return None
        rel = fullname + ".py"
        src = _core_src.get(rel)
        if src is None or (ROOT / rel).is_file():
            return None
        origin = str(ROOT / rel)
        return importlib.util.spec_from_loader(fullname, _Loader(src, origin), origin=origin)


def install_core() -> None:
    global _core_src
    refresh_core()
    key = passphrase()
    if (ROOT / "model.py").is_file():
        return
    if not key or not CORE.is_file():
        print("PORCINI_KEY mancante", flush=True)
        raise SystemExit(1)
    try:
        files = _unpack(open_vault(CORE.read_bytes(), key))
    except Exception:
        print("core illeggibile", flush=True)
        raise SystemExit(1)
    _core_src = {name: data for name, data in files.items() if name.endswith(".py")}
    for name, data in files.items():
        if name.endswith(".py"):
            continue
        dest = ROOT / name
        if dest.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if not any(isinstance(item, _Finder) for item in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


def startup() -> None:
    global _html, _js, _password
    refresh_core()
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


def export_pages(dest: Path) -> None:
    """Pages: mappa decifrata + hash gate. Niente /api/login sul CDN."""
    key = passphrase()
    if not key:
        print("PORCINI_KEY mancante", flush=True)
        raise SystemExit(1)
    try:
        html = open_vault((VAULT / "index.html.enc").read_bytes(), key)
        js = open_vault((VAULT / "heatmap.js.enc").read_bytes(), key)
        pw = open_vault((VAULT / "gate.enc").read_bytes(), key)
    except Exception:
        print("vault illeggibile", flush=True)
        raise SystemExit(1)
    dest.mkdir(parents=True, exist_ok=True)
    guard = (
        b"<script>(function(){if(sessionStorage.getItem('porcini_gate')!=='1')"
        b"{location.replace('./');}})();</script>\n"
    )
    text = html.decode("utf-8")
    if "<head>" in text:
        text = text.replace("<head>", "<head>\n" + guard.decode("utf-8"), 1)
    else:
        text = guard.decode("utf-8") + text
    (dest / "map.html").write_text(text, encoding="utf-8")
    (dest / "heatmap.js").write_bytes(js)
    (dest / "gate.json").write_text(
        json.dumps({"h": hashlib.sha256(pw).hexdigest()}, separators=(",", ":")),
        encoding="utf-8",
    )
    print("pages ui ok", flush=True)
