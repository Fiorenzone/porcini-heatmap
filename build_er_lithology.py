#!/usr/bin/env python3
"""Calcari e gessi dell'Appennino sulle celle di suolo ancora a 0. Non tocca la carta dei suoli."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer

import soil_grid

ROOT = Path(__file__).resolve().parent
ZIP = ROOT / "data" / "soil" / "_src" / "Unita_geologiche_50K.zip"
SOUTH, WEST = 43.55, 8.35
NORTH, EAST = 46.75, 13.05


def _carbonate(name: str) -> bool:
    low = (name or "").lower()
    if any(bad in low for bad in ("argill", "marnoso", "arenac", "clasti", "pelitic")):
        return False
    return any(k in low for k in ("gessos", "maiolica", "calcare", "calcari", "calcarenite", "dolom"))


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


def build() -> dict:
    if not ZIP.exists():
        raise SystemExit(f"manca {ZIP}")
    if not soil_grid.exists():
        raise SystemExit("soil_grid.bin assente")
    meta = json.loads(soil_grid.GRID_META.read_text())
    raw = soil_grid.GRID_BIN.read_bytes()
    grid = bytearray(raw[soil_grid.HEADER_LEN :])
    south, west = float(meta["south"]), float(meta["west"])
    step = float(meta["step"])
    nrows, ncols = int(meta["nrows"]), int(meta["ncols"])
    before = grid.count(0)

    src = zipfile.ZipFile(ZIP)
    base = Path("/tmp/er_unita")
    base.mkdir(parents=True, exist_ok=True)
    prj = ""
    shp_name = ""
    for name in src.namelist():
        if name.lower().endswith((".shp", ".shx", ".dbf", ".prj", ".cpg")):
            dest = base / Path(name).name
            dest.write_bytes(src.read(name))
            if name.lower().endswith(".prj"):
                prj = dest.read_text()
            if name.lower().endswith(".shp"):
                shp_name = dest.name
    to_ll = Transformer.from_crs(CRS.from_wkt(prj), 4326, always_xy=True)
    reader = shapefile.Reader(str(base / shp_name))
    fields = [f[0] for f in reader.fields[1:]]
    idx = fields.index("NOME")
    painted = 0
    seen = 0
    for sr in reader.iterShapeRecords():
        if not _carbonate(str(sr.record[idx] or "")):
            continue
        seen += 1
        shape = sr.shape
        if shape.shapeType not in (5, 15, 25):
            continue
        parts = list(shape.parts) + [len(shape.points)]
        rings = []
        for a, b in zip(parts, parts[1:]):
            pts = shape.points[a:b]
            if len(pts) < 3:
                continue
            lons, lats = to_ll.transform([p[0] for p in pts], [p[1] for p in pts])
            rings.append(list(zip(lats, lons)))
        if not rings:
            continue
        minlat = min(p[0] for ring in rings for p in ring)
        maxlat = max(p[0] for ring in rings for p in ring)
        minlon = min(p[1] for ring in rings for p in ring)
        maxlon = max(p[1] for ring in rings for p in ring)
        if maxlat < SOUTH or minlat > NORTH or maxlon < WEST or minlon > EAST:
            continue
        iy0 = max(0, int((minlat - south) / step))
        iy1 = min(nrows - 1, int((maxlat - south) / step))
        ix0 = max(0, int((minlon - west) / step))
        ix1 = min(ncols - 1, int((maxlon - west) / step))
        for iy in range(iy0, iy1 + 1):
            lat = south + (iy + 0.5) * step
            row = iy * ncols
            for ix in range(ix0, ix1 + 1):
                cell = row + ix
                if grid[cell] != 0:
                    continue
                lon = west + (ix + 0.5) * step
                hit = False
                for ring in rings:
                    if len(ring) >= 3 and _inside(lat, lon, ring):
                        hit = not hit
                if hit:
                    grid[cell] = 5
                    painted += 1
    if painted:
        counts = {str(i): grid.count(i) for i in range(6)}
        note = json.loads(soil_grid.GRID_META.read_text()).get("source_note") or ""
        soil_grid.write_grid(
            south=south,
            west=west,
            step=step,
            nrows=nrows,
            ncols=ncols,
            cells=bytes(grid),
            counts=counts,
            source_note=(note + " Calcari e gessi 50k sulle celle vuote.").strip(),
        )
    after = grid.count(0)
    print(f"formazioni carbonatiche {seen}, celle nuove classe 5: {painted}, vuote {before} → {after}", flush=True)
    return {"painted": painted, "seen": seen, "empty": after}


if __name__ == "__main__":
    build()
