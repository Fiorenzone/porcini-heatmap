#!/usr/bin/env python3
"""Uso del suolo ER 2023 → celle ancora vuote di cover_grid. Non tocca il DUSAF."""

from __future__ import annotations

import zipfile
from pathlib import Path

import shapefile
from pyproj import Transformer

import cover_grid

ROOT = Path(__file__).resolve().parent
ZIP = ROOT / "data" / "cover" / "_src" / "uso_2023_7791.zip"
SOUTH, WEST = 43.55, 8.35
NORTH, EAST = 46.75, 13.05

# COD_TOT intero. Solo bosco e arbusteto: prati, rocce e calanchi non sono struttura.
CODE_CLASS = {
    3111: 1,  # faggi
    3112: 1,  # querce, carpini, castagni
    3114: 1,  # planiziari
    3120: 1,  # conifere
    3130: 1,  # misti
    3115: 4,  # castagneto da frutto
    3113: 5,  # salici e pioppi
    3116: 6,  # boscaglie ruderali
    3220: 6,  # cespuglieti
    3231: 6,  # arbusteto in evoluzione
    3232: 6,  # rimboschimenti recenti
}


def _klass(raw) -> int | None:
    try:
        return CODE_CLASS.get(int(raw))
    except (TypeError, ValueError):
        return None


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


def build() -> dict:
    if not ZIP.exists():
        raise SystemExit(f"manca {ZIP}")
    if not cover_grid.exists():
        raise SystemExit("cover_grid.bin assente: prima DUSAF")
    meta = __import__("json").loads(cover_grid.GRID_META.read_text())
    raw = cover_grid.GRID_BIN.read_bytes()
    grid = bytearray(raw[cover_grid.HEADER_LEN :])
    south, west = float(meta["south"]), float(meta["west"])
    step = float(meta["step"])
    nrows, ncols = int(meta["nrows"]), int(meta["ncols"])
    before = [grid.count(i) for i in range(7)]
    original = bytes(grid)
    # Il bosco chiuso copre l'arbusteto se i poligoni si sovrappongono. Il DUSAF non si tocca.
    pri = {0: 0, 6: 1, 3: 2, 5: 3, 4: 4, 1: 5}

    src = zipfile.ZipFile(ZIP)
    shp_name = next(n for n in src.namelist() if n.endswith(".shp"))
    base = Path("/tmp/er_uso")
    base.mkdir(parents=True, exist_ok=True)
    for name in src.namelist():
        if name.endswith((".shp", ".shx", ".dbf", ".prj", ".cpg")):
            (base / Path(name).name).write_bytes(src.read(name))
    reader = shapefile.Reader(str(base / Path(shp_name).name))
    fields = [f[0] for f in reader.fields[1:]]
    if "COD_TOT" not in fields:
        raise SystemExit(f"COD_TOT assente: {fields}")
    idx = fields.index("COD_TOT")
    code_field = "COD_TOT"
    to_ll = Transformer.from_crs(7791, 4326, always_xy=True)
    painted = 0
    seen = 0
    for sr in reader.iterShapeRecords():
        klass = _klass(sr.record[idx])
        if klass is None:
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
                if original[cell] != 0 or pri[klass] < pri[grid[cell]]:
                    continue
                lon = west + (ix + 0.5) * step
                if _hit(lat, lon, rings):
                    if grid[cell] == 0:
                        painted += 1
                    grid[cell] = klass
        if seen % 5000 == 0:
            print(f"  ER uso {seen} poligoni, celle nuove {painted}", flush=True)
    counts = {str(i): grid.count(i) for i in range(7)}
    cover_grid.write_grid(
        south=south,
        west=west,
        step=step,
        nrows=nrows,
        ncols=ncols,
        cells=bytes(grid),
        counts={"before": before, "after": counts},
        source_note="DUSAF 7 Lombardia + uso suolo ER 2023 sulle celle vuote",
    )
    print(f"campo {code_field}, poligoni bosco {seen}, celle nuove {painted}", flush=True)
    print(counts, flush=True)
    return {"painted": painted, "seen": seen, "counts": counts}


if __name__ == "__main__":
    build()
