"""Carta forestale Emilia-Romagna 2025. Specie dal campo SPECIE1."""

from __future__ import annotations

import pickle
import threading
import zipfile
from pathlib import Path

import shapefile
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent / "data" / "forest"
CACHE = ROOT / "er_index.pkl"
TO_WGS = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True)

CONIF = ("Abies", "Picea", "Larix", "Pinus", "Pseudotsuga")
BROAD = (
    "Fagus",
    "Quercus",
    "Castanea",
    "Ostrya",
    "Carpinus",
    "Fraxinus",
    "Acer",
    "Alnus",
    "Robinia",
    "Populus",
    "Salix",
    "Tilia",
    "Ulmus",
    "Corylus",
    "Ostrya",
)

_POLYS: list[tuple] | None = None
_GRID: dict[tuple[int, int], list[int]] | None = None
_CELL = 0.05
_LOCK = threading.Lock()
_READY = threading.Event()


def _group(name: str) -> str | None:
    if any(k in name for k in CONIF):
        return "conifere"
    if any(k in name for k in BROAD):
        return "latifoglie"
    return None


def _leaf(s1: str, s2: str) -> tuple[str, str]:
    g1, g2 = _group(s1), _group(s2)
    label = (s1 or s2 or "Bosco").split(" - ")[-1].strip() or "Bosco"
    if g1 and g2 and g1 != g2:
        return "misto", label
    if g1 or g2:
        return g1 or g2, label
    return "latifoglie", label


def _rings(shape) -> list[list[tuple[float, float]]]:
    pts = shape.points
    parts = list(shape.parts) + [len(pts)]
    rings = []
    for a, b in zip(parts, parts[1:]):
        ring = []
        for x, y in pts[a:b]:
            lon, lat = TO_WGS.transform(x, y)
            ring.append((lat, lon))
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def _parse_zips() -> list[tuple]:
    polys = []
    if not ROOT.exists():
        return polys
    for zpath in sorted(ROOT.glob("CartaForestale2025*.zip")):
        dest = ROOT / zpath.stem
        dest.mkdir(exist_ok=True)
        shp = next(dest.glob("*.shp"), None)
        if shp is None:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(dest)
            shp = next(dest.glob("*.shp"))
        sf = shapefile.Reader(str(shp))
        fields = [f[0] for f in sf.fields[1:]]
        i_uso = fields.index("NOMEUSO")
        i_s1 = fields.index("SPECIE1")
        i_s2 = fields.index("SPECIE2")
        for sr in sf.iterShapeRecords():
            rec = sr.record
            if rec[i_uso] != "Bosco":
                continue
            rings = _rings(sr.shape)
            if not rings:
                continue
            lats = [p[0] for p in rings[0]]
            lons = [p[1] for p in rings[0]]
            leaf, label = _leaf(rec[i_s1] or "", rec[i_s2] or "")
            polys.append((min(lats), min(lons), max(lats), max(lons), leaf, label, rings[0]))
        sf.close()
    return polys


def _build_grid(polys: list[tuple]) -> dict:
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (minlat, minlon, maxlat, maxlon, *_rest) in enumerate(polys):
        lat0, lat1 = int(minlat / _CELL), int(maxlat / _CELL)
        lon0, lon1 = int(minlon / _CELL), int(maxlon / _CELL)
        for iy in range(lat0, lat1 + 1):
            for ix in range(lon0, lon1 + 1):
                grid.setdefault((iy, ix), []).append(i)
    return grid


def _load() -> list[tuple]:
    global _POLYS, _GRID
    if _POLYS is not None:
        return _POLYS
    with _LOCK:
        if _POLYS is not None:
            return _POLYS
        if CACHE.exists():
            with CACHE.open("rb") as fh:
                _POLYS, _GRID = pickle.load(fh)
        else:
            polys = _parse_zips()
            grid = _build_grid(polys)
            ROOT.mkdir(parents=True, exist_ok=True)
            CACHE.write_bytes(pickle.dumps((polys, grid), protocol=pickle.HIGHEST_PROTOCOL))
            _POLYS, _GRID = polys, grid
        _READY.set()
        return _POLYS


def ready() -> bool:
    return _READY.is_set() or _POLYS is not None


def warm() -> int:
    """Precarica indice (thread-safe). Ritorna n poligoni."""
    return len(_load())


def _inside(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if ((lon_i > lon) != (lon_j > lon)) and (
            lat < (lat_j - lat_i) * (lon - lon_i) / ((lon_j - lon_i) or 1e-12) + lat_i
        ):
            c = not c
        j = i
    return c


def in_emilia(lat: float, lon: float) -> bool:
    return 43.6 <= lat <= 45.2 and 9.15 <= lon <= 12.95


def lookup(lat: float, lon: float) -> tuple[str, str] | None:
    """None se il punto non cade in Emilia. ('altro', ...) se in regione ma non bosco."""
    if not in_emilia(lat, lon):
        return None
    polys = _load()
    grid = _GRID or {}
    iy, ix = int(lat / _CELL), int(lon / _CELL)
    cands = grid.get((iy, ix), ())
    # Cella vuota = fuori copertura reale shapefile (bbox ER è grosso, include Toscana).
    # None → server usa proxy, non "non bosco" falso.
    if not cands:
        return None
    for i in cands:
        minlat, minlon, maxlat, maxlon, leaf, label, ring = polys[i]
        if lat < minlat or lat > maxlat or lon < minlon or lon > maxlon:
            continue
        if _inside(lat, lon, ring):
            return leaf, f"Carta forestale ER 2025: {label}"
    return "altro", "Carta forestale ER 2025: non bosco"
