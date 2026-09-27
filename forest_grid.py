"""Carta forestale compatta: griglia uint8+uint16 (~MB). Sostituisce pickle ~450MB."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "forest"
GRID_BIN = ROOT / "forest_grid.bin"
GRID_META = ROOT / "forest_grid.json"

MAGIC = b"PORCGRD1"
HEADER_LEN = 48
LEAF_CODE = {1: "altro", 2: "latifoglie", 3: "conifere", 4: "misto"}
CODE_LEAF = {v: k for k, v in LEAF_CODE.items()}

_LOCK = threading.Lock()
_READY = threading.Event()
_META: dict | None = None
_CELLS: memoryview | None = None
_LABELS: list[str] = []


def exists() -> bool:
    return GRID_BIN.exists() and GRID_META.exists()


def ready() -> bool:
    return _READY.is_set()


def _open() -> bool:
    global _META, _CELLS, _LABELS
    if not exists():
        return False
    meta = json.loads(GRID_META.read_text())
    raw = GRID_BIN.read_bytes()
    if len(raw) < HEADER_LEN or raw[:8] != MAGIC:
        raise ValueError("forest_grid.bin non valido")
    _META = meta
    _LABELS = list(meta.get("labels") or [])
    _CELLS = memoryview(raw)[HEADER_LEN:]
    n = int(meta["nrows"]) * int(meta["ncols"])
    if len(_CELLS) < n * 3:
        raise ValueError("forest_grid.bin troncato")
    _READY.set()
    return True


def warm() -> dict:
    with _LOCK:
        if _READY.is_set() and _META is not None:
            n = int(_META["nrows"]) * int(_META["ncols"])
            return {
                "ok": True,
                "cells": n,
                "step": _META.get("step"),
                "bytes": GRID_BIN.stat().st_size if GRID_BIN.exists() else 0,
            }
        try:
            if not _open():
                return {"ok": False, "error": "grid assente"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        n = int(_META["nrows"]) * int(_META["ncols"])
        return {
            "ok": True,
            "cells": n,
            "step": _META.get("step"),
            "bytes": GRID_BIN.stat().st_size,
        }


def ensure(build_if_missing: bool = False) -> dict:
    info = warm()
    if info.get("ok"):
        return info
    if not build_if_missing:
        return info
    try:
        from build_forest_grid import build

        build()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return warm()


def lookup(lat: float, lon: float) -> tuple[str, str] | None:
    if not _READY.is_set():
        with _LOCK:
            if not _READY.is_set() and not _open():
                return None
    assert _META is not None and _CELLS is not None
    south = float(_META["south"])
    west = float(_META["west"])
    step = float(_META["step"])
    nrows = int(_META["nrows"])
    ncols = int(_META["ncols"])
    iy = int((lat - south) / step)
    ix = int((lon - west) / step)
    if iy < 0 or ix < 0 or iy >= nrows or ix >= ncols:
        return None
    off = (iy * ncols + ix) * 3
    code = int(_CELLS[off])
    if code == 0:
        return None
    leaf = LEAF_CODE.get(code)
    if leaf is None:
        return None
    lab_i = (int(_CELLS[off + 1]) << 8) | int(_CELLS[off + 2])
    label = _LABELS[lab_i - 1] if 0 < lab_i <= len(_LABELS) else leaf
    src = _META.get("source_note") or "Carta forestale (griglia)"
    if leaf == "altro":
        return "altro", f"{src}: non bosco"
    return leaf, f"{src}: {label}"


def write_grid(
    *,
    south: float,
    west: float,
    step: float,
    nrows: int,
    ncols: int,
    leaf: bytes,
    label_hi: bytes,
    label_lo: bytes,
    labels: list[str],
    source_note: str = "Carta forestale ER+LOM",
) -> Path:
    n = nrows * ncols
    if len(leaf) != n or len(label_hi) != n or len(label_lo) != n:
        raise ValueError("dimensioni non allineate")
    ROOT.mkdir(parents=True, exist_ok=True)
    header = MAGIC + struct.pack("<dddII", south, west, step, nrows, ncols)
    header = header + b"\x00" * (HEADER_LEN - len(header))
    payload = bytearray(n * 3)
    for i in range(n):
        payload[i * 3] = leaf[i]
        payload[i * 3 + 1] = label_hi[i]
        payload[i * 3 + 2] = label_lo[i]
    GRID_BIN.write_bytes(header + bytes(payload))
    meta = {
        "south": south,
        "west": west,
        "north": south + nrows * step,
        "east": west + ncols * step,
        "step": step,
        "nrows": nrows,
        "ncols": ncols,
        "labels": labels,
        "source_note": source_note,
        "bytes": GRID_BIN.stat().st_size,
        "encoding": "uint8 leaf + uint16 label BE; 0=outside",
    }
    GRID_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    global _META, _CELLS, _LABELS
    with _LOCK:
        _READY.clear()
        _META = None
        _CELLS = None
        _LABELS = []
    return GRID_BIN
