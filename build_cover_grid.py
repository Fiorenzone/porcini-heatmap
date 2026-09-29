#!/usr/bin/env python3
"""Rasterizza DUSAF 7 (bosco) → data/cover/cover_grid.bin. Una tantum, offline."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import cover_grid

SOUTH, WEST = 43.55, 8.35
NORTH, EAST = 46.75, 13.05
STEP = 0.005

QUERY = (
    "https://www.cartografia.servizirl.it/arcgis1/rest/services/"
    "territorio/dusaf7/MapServer/1/query"
)

# Pittura dal più debole al più boschivo: in overlap vince il bosco.
LAYERS = (
    ("DESCR LIKE '324%' OR DESCR LIKE '314%'", 6),
    ("DESCR LIKE '3113%'", 5),
    ("DESCR LIKE '3114%'", 4),
    ("DESCR LIKE '3112%' OR DESCR LIKE '3122%' OR DESCR LIKE '3132%'", 3),
    ("DESCR LIKE '3111%' OR DESCR LIKE '3121%' OR DESCR LIKE '3131%'", 1),
    ("DESCR LIKE '%ceduo%' AND DESCR LIKE '%media e alta%'", 2),
)


def _inside(lat: float, lon: float, ring) -> bool:
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


def _hit(lat: float, lon: float, rings) -> bool:
    c = False
    for ring in rings:
        if len(ring) >= 3 and _inside(lat, lon, ring):
            c = not c
    return c


def _paint_one(grid, south, west, step, nrows, ncols, rings, klass: int) -> None:
    minlat = min(p[0] for ring in rings for p in ring)
    maxlat = max(p[0] for ring in rings for p in ring)
    minlon = min(p[1] for ring in rings for p in ring)
    maxlon = max(p[1] for ring in rings for p in ring)
    iy0 = max(0, int((minlat - south) / step))
    iy1 = min(nrows - 1, int((maxlat - south) / step))
    ix0 = max(0, int((minlon - west) / step))
    ix1 = min(ncols - 1, int((maxlon - west) / step))
    if iy0 > iy1 or ix0 > ix1:
        return
    for iy in range(iy0, iy1 + 1):
        lat = south + (iy + 0.5) * step
        row = iy * ncols
        for ix in range(ix0, ix1 + 1):
            lon = west + (ix + 0.5) * step
            if _hit(lat, lon, rings):
                grid[row + ix] = klass


def _pages(where: str):
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {
                "where": where,
                "outFields": "DESCR",
                "returnGeometry": "true",
                "outSR": "4326",
                "geometryPrecision": "4",
                "resultOffset": offset,
                "resultRecordCount": 500,
                "f": "json",
            }
        )
        req = urllib.request.Request(QUERY + "?" + q, headers={"User-Agent": "porcini-heatmap"})
        with urllib.request.urlopen(req, timeout=120) as res:
            payload = json.loads(res.read().decode())
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        batch = payload.get("features") or []
        yield batch
        if not payload.get("exceededTransferLimit") or not batch:
            break
        offset += len(batch)


def _rings(geom: dict) -> list:
    out = []
    for ring in (geom or {}).get("rings") or []:
        if len(ring) >= 3:
            out.append([(p[1], p[0]) for p in ring])
    return out


def build(step: float = STEP) -> dict:
    t0 = time.perf_counter()
    south, west = SOUTH, WEST
    nrows = int((NORTH - south) / step) + 1
    ncols = int((EAST - west) / step) + 1
    grid = bytearray(nrows * ncols)
    painted = 0
    for where, klass in LAYERS:
        n = 0
        print(f"DUSAF classe {klass}…", flush=True)
        for batch in _pages(where):
            for ft in batch:
                rings = _rings(ft.get("geometry") or {})
                if not rings:
                    continue
                _paint_one(grid, south, west, step, nrows, ncols, rings, klass)
                n += 1
                painted += 1
            print(f"  … {n}", flush=True)
        print(f"  classe {klass}: {n} poligoni", flush=True)
    counts = {str(k): grid.count(k) for k in range(7)}
    path = cover_grid.write_grid(
        south=south,
        west=west,
        step=step,
        nrows=nrows,
        ncols=ncols,
        cells=bytes(grid),
        counts=counts,
    )
    info = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "painted": painted,
        "counts": counts,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    print(f"ok {info}", flush=True)
    return info


if __name__ == "__main__":
    build()
