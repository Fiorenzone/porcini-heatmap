"""Maschera reazione del suolo ER+LOM. uint8, stessa griglia della carta forestale."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "soil"
GRID_BIN = ROOT / "soil_grid.bin"
GRID_META = ROOT / "soil_grid.json"

MAGIC = b"PORCSOL1"
HEADER_LEN = 48

# 0 = cella nel riquadro senza classe (buco di carta). 1–5 = reazione.
CLASS_LABEL = {
    0: "non classificato",
    1: "acido",
    2: "subacido",
    3: "neutro",
    4: "subalcalino o calcareo",
    5: "alcalino o calcareo puro",
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
        raise ValueError("soil_grid.bin non valido")
    _META = meta
    _CELLS = memoryview(raw)[HEADER_LEN:]
    n = int(meta["nrows"]) * int(meta["ncols"])
    if len(_CELLS) < n:
        raise ValueError("soil_grid.bin troncato")
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


def lookup(lat: float, lon: float) -> tuple[int, str] | None:
    """(classe, etichetta) dentro il riquadro. None fuori da ER+LOM."""
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


def write_grid(
    *,
    south: float,
    west: float,
    step: float,
    nrows: int,
    ncols: int,
    cells: bytes,
    counts: dict,
    source_note: str,
) -> Path:
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
        "source_note": source_note,
        "sources": [
            "Regione Emilia-Romagna, Carta dei suoli 1:50.000 ed. 2021 (MinERva). Classe da WRB/Soil Taxonomy e descrizione del suolo dominante.",
            "ERSAF / Regione Lombardia, Carta pedologica 1:250.000, campo PH_1M.",
            "Regione Emilia-Romagna, Carta geologica 50k. Solo calcari e gessi sulle celle senza carta dei suoli.",
        ],
        "bytes": GRID_BIN.stat().st_size,
        "encoding": "uint8 class; 0=unclassified inside bbox",
    }
    GRID_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    global _META, _CELLS
    with _LOCK:
        _READY.clear()
        _META = None
        _CELLS = None
    return GRID_BIN
