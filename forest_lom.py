"""Carta forestale Lombardia (MapServer regionale). Tipi per layer."""

from __future__ import annotations

import json
import pickle
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "forest"
CACHE = ROOT / "lom_index.pkl"
BASE = "http://www.cartografia.servizirl.it/arcgis2/rest/services/agricoltura/carta_forestale/MapServer"

# Solo i layer interrogabili, non i gruppi.
LAYERS = [
    (1, "Abieteti"),
    (3, "Aceri-frassineti"),
    (5, "Alneti di ontano bianco"),
    (6, "Alneti di ontano verde"),
    (7, "Alneti di ontano nero"),
    (9, "Betuleti e corileti"),
    (11, "Castagneti"),
    (13, "Faggete altimontane"),
    (14, "Faggete montane"),
    (15, "Faggete non classificabili"),
    (16, "Faggete primitive"),
    (17, "Faggete submontane"),
    (19, "Formazioni antropogene"),
    (21, "Robinieti"),
    (22, "Rimboschimenti"),
    (29, "Lariceti"),
    (30, "Larici-cembreti"),
    (32, "Mughete"),
    (34, "Bosco non classificato"),
    (38, "Conifere"),
    (40, "Latifoglie"),
    (41, "Misti"),
    (44, "Orno-ostrieti"),
    (46, "Peccete altimontane"),
    (47, "Peccete"),
    (48, "Peccete montane"),
    (50, "Peccete secondarie"),
    (52, "Piceo-faggeti"),
    (54, "Pinete di pino silvestre"),
    (55, "Pinete planiziali"),
    (57, "Querceti di cerro"),
    (58, "Querceti di farnia"),
    (60, "Querceti di rovere"),
    (61, "Querceti di roverella"),
    (64, "Carpineti"),
    (65, "Querco-carpineti"),
]

CONIF = ("Abiet", "Peccet", "Laric", "Pinet", "Mughet", "Conifere", "Cembret")
MIX = ("Piceo-fagget", "Misti")

_POLYS = None
_GRID = None
_CELL = 0.05
_LOCK = threading.Lock()
_READY = threading.Event()


def _leaf(name: str) -> str:
    if any(k in name for k in MIX):
        return "misto"
    if any(k in name for k in CONIF):
        return "conifere"
    return "latifoglie"


def _ring(coords) -> list[tuple[float, float]]:
    ring = [(lat, lon) for lon, lat in coords]
    if len(ring) > 80:
        ring = ring[::3]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
    return ring


def _fetch(layer: int, offset: int) -> dict:
    q = urllib.parse.urlencode(
        {
            "where": "1=1",
            "outFields": "",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": 1000,
            "f": "geojson",
        }
    )
    url = f"{BASE}/{layer}/query?{q}"
    with urllib.request.urlopen(url, timeout=120) as res:
        return json.loads(res.read().decode())


def _download() -> list[tuple]:
    polys = []
    for layer, name in LAYERS:
        offset = 0
        leaf = _leaf(name)
        label = name
        while True:
            data = _fetch(layer, offset)
            feats = data.get("features") or []
            for feat in feats:
                geom = feat.get("geometry") or {}
                gtype = geom.get("type")
                coords = geom.get("coordinates") or []
                rings = []
                if gtype == "Polygon" and coords:
                    rings = [_ring(coords[0])]
                elif gtype == "MultiPolygon":
                    rings = [_ring(poly[0]) for poly in coords if poly]
                for ring in rings:
                    if len(ring) < 3:
                        continue
                    lats = [p[0] for p in ring]
                    lons = [p[1] for p in ring]
                    polys.append((min(lats), min(lons), max(lats), max(lons), leaf, label, ring))
            if not data.get("exceededTransferLimit") or not feats:
                break
            offset += len(feats)
        print(f"lom {name}: offset {offset} tot {len(polys)}", flush=True)
    return polys


def _build_grid(polys: list[tuple]) -> dict:
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (minlat, minlon, maxlat, maxlon, *_rest) in enumerate(polys):
        for iy in range(int(minlat / _CELL), int(maxlat / _CELL) + 1):
            for ix in range(int(minlon / _CELL), int(maxlon / _CELL) + 1):
                grid.setdefault((iy, ix), []).append(i)
    return grid


def _load():
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
            ROOT.mkdir(parents=True, exist_ok=True)
            polys = _download()
            grid = _build_grid(polys)
            CACHE.write_bytes(pickle.dumps((polys, grid), protocol=pickle.HIGHEST_PROTOCOL))
            _POLYS, _GRID = polys, grid
        _READY.set()
        return _POLYS


def ready() -> bool:
    return _READY.is_set() or _POLYS is not None


def warm() -> int:
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


def in_lombardia(lat: float, lon: float) -> bool:
    return 44.55 <= lat <= 46.7 and 8.4 <= lon <= 11.05


def lookup(lat: float, lon: float) -> tuple[str, str] | None:
    if not in_lombardia(lat, lon):
        return None
    polys = _load()
    iy, ix = int(lat / _CELL), int(lon / _CELL)
    for i in (_GRID or {}).get((iy, ix), ()):
        minlat, minlon, maxlat, maxlon, leaf, label, ring = polys[i]
        if lat < minlat or lat > maxlat or lon < minlon or lon > maxlon:
            continue
        if _inside(lat, lon, ring):
            return leaf, f"Carta forestale Lombardia: {label}"
    return "altro", "Carta forestale Lombardia: non bosco"
