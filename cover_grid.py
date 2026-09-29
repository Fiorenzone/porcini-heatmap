"""Struttura del bosco (DUSAF). uint8, stessa griglia della carta forestale."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "cover"
GRID_BIN = ROOT / "cover_grid.bin"
GRID_META = ROOT / "cover_grid.json"

MAGIC = b"PORCCOV1"
HEADER_LEN = 48

CLASS_LABEL = {
    0: "non in carta",
    1: "bosco denso o fustaia",
    2: "ceduo denso",
    3: "bosco rado",
    4: "castagneto da frutto",
    5: "formazione ripariale",
    6: "rimboschimento o arbusteto",
}

_LOCK = threading.Lock()
_READY = threading.Event()
_META: dict | None = None
_CELLS: memoryview | None = None


def exists() -> bool:
    return GRID_BIN.exists() and GRID_META.exists()


def ready() -> bool:
    return _READY.is_set()


def _open() -> bool:
    global _META, _CELLS
    if not exists():
        return False
    meta = json.loads(GRID_META.read_text())
    raw = GRID_BIN.read_bytes()
    if len(raw) < HEADER_LEN or raw[:8] != MAGIC:
        raise ValueError("cover_grid.bin non valido")
    _META = meta
    _CELLS = memoryview(raw)[HEADER_LEN:]
    n = int(meta["nrows"]) * int(meta["ncols"])
    if len(_CELLS) < n:
        raise ValueError("cover_grid.bin troncato")
    _READY.set()
    return True


def warm() -> dict:
    with _LOCK:
        if _READY.is_set() and _META is not None:
            n = int(_META["nrows"]) * int(_META["ncols"])
            return {"ok": True, "cells": n, "step": _META.get("step"), "bytes": GRID_BIN.stat().st_size}
        try:
            if not _open():
                return {"ok": False, "error": "grid assente"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        n = int(_META["nrows"]) * int(_META["ncols"])
        return {"ok": True, "cells": n, "step": _META.get("step"), "bytes": GRID_BIN.stat().st_size}


def lookup(lat: float, lon: float) -> tuple[int, str] | None:
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
    code = int(_CELLS[iy * ncols + ix])
    return code, CLASS_LABEL.get(code, CLASS_LABEL[0])


def write_grid(*, south, west, step, nrows, ncols, cells: bytes, counts: dict, source_note: str | None = None) -> Path:
    n = nrows * ncols
    if len(cells) != n:
        raise ValueError("dimensioni non allineate")
    ROOT.mkdir(parents=True, exist_ok=True)
    header = MAGIC + struct.pack("<dddII", south, west, step, nrows, ncols)
    header = header + b"\x00" * (HEADER_LEN - len(header))
    GRID_BIN.write_bytes(header + cells)
    meta = {
        "south": south,
        "west": west,
        "north": south + nrows * step,
        "east": west + ncols * step,
        "step": step,
        "nrows": nrows,
        "ncols": ncols,
        "classes": {str(k): v for k, v in CLASS_LABEL.items()},
        "counts": counts,
        "source_note": source_note or "DUSAF 7.0 Lombardia 2021 — struttura del bosco",
        "sources": [
            "Regione Lombardia, DUSAF 7.0 Uso e copertura del suolo 2021. Ceduo, fustaia, densità, ripariali, rimboschimenti.",
            "Regione Emilia-Romagna, Uso del suolo di dettaglio 2023. Solo celle fuori dal DUSAF.",
        ],
        "bytes": GRID_BIN.stat().st_size,
        "encoding": "uint8 class; 0=outside DUSAF forest",
    }
    GRID_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    global _META, _CELLS
    with _LOCK:
        _READY.clear()
        _META = None
        _CELLS = None
    return GRID_BIN
