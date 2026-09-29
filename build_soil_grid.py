#!/usr/bin/env python3
"""Rasterizza pH/litologia ER+LOM → data/soil/soil_grid.bin (una tantum, offline).

ER: Carta dei suoli 1:50.000, classe da WRB + descrizione del suolo dominante.
LOM: Carta pedologica 1:250.000, PH_1M. Pittura prima LOM, poi ER (più fine vince).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import shapefile
from pyproj import Transformer

import soil_grid

# Stesso riquadro e step della carta forestale (build_forest_grid.py).
SOUTH, WEST = 43.55, 8.35
NORTH, EAST = 46.75, 13.05
STEP = 0.005

ER_ZIP_URL = "https://mappegis.regione.emilia-romagna.it/moka/ckan/suolo/Carta_Suoli_50k.zip"
LOM_QUERY = (
    "https://www.cartografia.servizirl.it/expo/rest/services/gpt/"
    "basi_informative_uso_suoli/MapServer/2/query"
)

ROOT = Path(__file__).resolve().parent / "data" / "soil"
SRC = ROOT / "_src"
ER_TO_WGS = Transformer.from_crs("EPSG:7791", "EPSG:4326", always_xy=True)

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def reaction_class(wrb: str, tax: str, desc: str) -> int:
    """1 acido … 5 alcalino/calcareo puro. 0 = non si capisce, niente cancello."""
    blob = f"{wrb or ''} {tax or ''}".lower()
    d = (desc or "").lower()
    d_pos = (
        d.replace("non calcarei", " ")
        .replace("non calcareo", " ")
        .replace("non calcaree", " ")
        .replace("non calcare", " ")
    )

    def acid_word(text: str) -> int | None:
        if any(k in text for k in ("molto acid", "fortemente acid", "estremamente acid")):
            return 1
        if any(k in text for k in ("debolmente acid", "moderatamente acid", "subacid", "acidi", "acido", "acida")):
            return 2
        return None

    def alk_word(text: str) -> int | None:
        if any(k in text for k in ("molto alcalin", "fortemente alcalin", "estremamente alcalin")):
            return 5
        if any(k in text for k in ("moderatamente alcalin", "debolmente alcalin", "subalcalin", "alcalin")):
            return 4
        return None

    strong = any(k in d_pos for k in ("molto calcare", "fortemente calcare", "estremamente calcare"))
    if "calcisol" in blob or "rendz" in blob or "rendz" in d or strong:
        return 5
    if "non calcare" in d:
        a = acid_word(d)
        if a:
            return a
        if "neutr" in d:
            return 3
        k = alk_word(d)
        if k:
            return k
    if any(k in blob for k in ("dystric", "dystrudept")) and "calcaric" not in blob:
        return 1
    a = acid_word(d)
    calcare = "calcaric" in blob or "calcare" in d_pos
    if a and not calcare:
        return a
    if calcare:
        return 4
    k = alk_word(d)
    if k:
        return k
    if "eutric" in blob or "neutr" in d:
        return 3
    if a:
        return a
    return 0


def _self_check() -> None:
    assert reaction_class("(2007) Haplic Cambisols (Calcaric)", "", "moderatamente calcarei") == 4
    assert reaction_class("(2007) Hypocalcic Haplic Calcisols", "", "") == 5
    assert reaction_class("(2014) Dystric Skeletic Cambisol (Humic)", "Typic Dystrudepts", "") == 1
    assert reaction_class("", "", "non calcarei, debolmente acidi") == 2
    assert reaction_class("", "", "non calcarei, neutri") == 3
    assert reaction_class("(2014) Eutric Cambisols (Loamic)", "", "") == 3
    assert reaction_class("", "", "molto calcarei, moderatamente alcalini") == 5
    assert reaction_class("", "", "non calcarei, moderatamente alcalini") == 4
    assert reaction_class("", "", "") == 0


def ph_class(ph: float | None) -> int:
    """Soglie allineate a DESCR_PH della carta lombarda."""
    if ph is None:
        return 0
    if ph < 5.6:
        return 1
    if ph < 6.6:
        return 2
    if ph < 7.4:
        return 3
    if ph < 7.9:
        return 4
    return 5


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


def _paint(polys, grid: bytearray, south, west, step, nrows, ncols) -> None:
    for pi, poly in enumerate(polys):
        minlat, minlon, maxlat, maxlon, klass, rings = poly
        if klass <= 0:
            continue
        iy0 = max(0, int((minlat - south) / step))
        iy1 = min(nrows - 1, int((maxlat - south) / step))
        ix0 = max(0, int((minlon - west) / step))
        ix1 = min(ncols - 1, int((maxlon - west) / step))
        if iy0 > iy1 or ix0 > ix1:
            continue
        for iy in range(iy0, iy1 + 1):
            lat = south + (iy + 0.5) * step
            row = iy * ncols
            for ix in range(ix0, ix1 + 1):
                lon = west + (ix + 0.5) * step
                if lat < minlat or lat > maxlat or lon < minlon or lon > maxlon:
                    continue
                if _hit(lat, lon, rings):
                    grid[row + ix] = klass
        if pi and pi % 2000 == 0:
            print(f"  … {pi}/{len(polys)} poligoni", flush=True)


def _xlsx_strings(z: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("m:si", _NS):
        texts = [t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        out.append("".join(texts))
    return out


def _xlsx_rows(z: zipfile.ZipFile, sheet: str, strings: list[str], cols: int) -> dict[int, dict[int, str]]:
    root = ET.fromstring(z.read(sheet))
    rows: dict[int, dict[int, str]] = {}
    for c in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = c.attrib.get("r")
        if not ref:
            continue
        col = "".join(ch for ch in ref if ch.isalpha())
        row = int("".join(ch for ch in ref if ch.isdigit()) or 0)
        n = 0
        for ch in col:
            n = n * 26 + (ord(ch) - 64)
        if n > cols:
            continue
        t = c.attrib.get("t")
        v = c.find("m:v", _NS)
        if v is None or v.text is None:
            val = ""
        elif t == "s":
            val = strings[int(v.text)]
        else:
            val = v.text
        rows.setdefault(row, {})[n] = val
    return rows


def _load_er_tables(xlsx: Path) -> tuple[dict[str, tuple[str, str, str]], dict[int, list[tuple[float, str]]]]:
    z = zipfile.ZipFile(xlsx)
    strings = _xlsx_strings(z)
    classif = _xlsx_rows(z, "xl/worksheets/sheet4.xml", strings, 7)
    by_soil: dict[str, tuple[str, str, str]] = {}
    for ri, cols in classif.items():
        if ri == 1:
            continue
        code = (cols.get(3) or "").strip()
        if not code:
            continue
        by_soil[code] = (cols.get(7) or "", cols.get(6) or "", cols.get(5) or "")
    delin = _xlsx_rows(z, "xl/worksheets/sheet2.xml", strings, 4)
    by_xid: dict[int, list[tuple[float, str]]] = {}
    for ri, cols in delin.items():
        if ri == 1:
            continue
        try:
            xid = int(float(cols.get(1) or 0))
            perc = float(cols.get(4) or 0)
        except ValueError:
            continue
        soil = (cols.get(2) or "").strip()
        if xid and soil:
            by_xid.setdefault(xid, []).append((perc, soil))
    return by_soil, by_xid


def _ensure_er_zip() -> Path:
    SRC.mkdir(parents=True, exist_ok=True)
    dest = SRC / "Carta_Suoli_50k.zip"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    print("scarico carta suoli ER…", flush=True)
    urllib.request.urlretrieve(ER_ZIP_URL, dest)
    return dest


def _er_polys(by_soil, by_xid) -> list[tuple]:
    zpath = _ensure_er_zip()
    outdir = SRC / "Carta_Suoli_50k"
    if not (outdir / "Carta_Suoli_50k.shp").exists():
        with zipfile.ZipFile(zpath) as z:
            z.extractall(SRC)
    sf = shapefile.Reader(str(outdir / "Carta_Suoli_50k.shp"))
    names = [f[0] for f in sf.fields[1:]]
    i_xid = names.index("XID_DELINE")
    i_sigla = names.index("SIGLA_UC")
    polys = []
    missed = 0
    for shape, rec in zip(sf.shapes(), sf.records()):
        if not shape.points:
            continue
        try:
            xid = int(float(rec[i_xid]))
        except (TypeError, ValueError):
            xid = 0
        comps = sorted(by_xid.get(xid) or [], key=lambda t: -t[0])
        code = comps[0][1] if comps else str(rec[i_sigla] or "").strip()
        wrb, tax, desc = by_soil.get(code, ("", "", ""))
        if not wrb and not desc:
            missed += 1
            continue
        klass = reaction_class(wrb, tax, desc)
        if klass <= 0:
            missed += 1
            continue
        pts = shape.points
        parts = list(shape.parts) + [len(pts)]
        rings = []
        minlat, minlon, maxlat, maxlon = 90.0, 180.0, -90.0, -180.0
        for a, b in zip(parts, parts[1:]):
            xs = [p[0] for p in pts[a:b]]
            ys = [p[1] for p in pts[a:b]]
            if len(xs) < 3:
                continue
            lons, lats = ER_TO_WGS.transform(xs, ys)
            ring = list(zip(lats, lons))
            rings.append(ring)
            minlat = min(minlat, min(lats))
            maxlat = max(maxlat, max(lats))
            minlon = min(minlon, min(lons))
            maxlon = max(maxlon, max(lons))
        if rings:
            polys.append((minlat, minlon, maxlat, maxlon, klass, rings))
    print(f"  ER poligoni classificati {len(polys)}, senza classe {missed}", flush=True)
    return polys


def _lom_polys() -> list[tuple]:
    cache = SRC / "lom_ph.json"
    features = []
    if cache.exists():
        features = json.loads(cache.read_text())
    else:
        SRC.mkdir(parents=True, exist_ok=True)
        offset = 0
        while True:
            q = urllib.parse.urlencode(
                {
                    "where": "1=1",
                    "outFields": "PH_1M",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "geometryPrecision": "4",
                    "resultOffset": offset,
                    "resultRecordCount": 1000,
                    "f": "json",
                }
            )
            print(f"  LOM offset {offset}", flush=True)
            with urllib.request.urlopen(LOM_QUERY + "?" + q, timeout=120) as res:
                payload = json.loads(res.read().decode())
            batch = payload.get("features") or []
            features.extend(batch)
            if not payload.get("exceededTransferLimit") or not batch:
                break
            offset += len(batch)
        cache.write_text(json.dumps(features))
    polys = []
    for ft in features:
        attrs = ft.get("attributes") or {}
        klass = ph_class(attrs.get("PH_1M"))
        if klass <= 0:
            continue
        rings_raw = (ft.get("geometry") or {}).get("rings") or []
        rings = []
        minlat, minlon, maxlat, maxlon = 90.0, 180.0, -90.0, -180.0
        for ring in rings_raw:
            if len(ring) < 3:
                continue
            latlon = [(p[1], p[0]) for p in ring]
            rings.append(latlon)
            lats = [p[0] for p in latlon]
            lons = [p[1] for p in latlon]
            minlat = min(minlat, min(lats))
            maxlat = max(maxlat, max(lats))
            minlon = min(minlon, min(lons))
            maxlon = max(maxlon, max(lons))
        if rings:
            polys.append((minlat, minlon, maxlat, maxlon, klass, rings))
    print(f"  LOM poligoni {len(polys)}", flush=True)
    return polys


def build(step: float = STEP) -> dict:
    _self_check()
    t0 = time.perf_counter()
    print("tabelle ER…", flush=True)
    zpath = _ensure_er_zip()
    xlsx = SRC / "Carta_Suoli_50k" / "Carta_Suoli50k.xlsx"
    if not xlsx.exists():
        with zipfile.ZipFile(zpath) as z:
            z.extractall(SRC)
    by_soil, by_xid = _load_er_tables(xlsx)
    print(f"  suoli {len(by_soil)}, delineazioni {len(by_xid)}", flush=True)
    print("geometrie LOM…", flush=True)
    lom = _lom_polys()
    print("geometrie ER…", flush=True)
    er = _er_polys(by_soil, by_xid)

    south, west = SOUTH, WEST
    nrows = int((NORTH - south) / step) + 1
    ncols = int((EAST - west) / step) + 1
    n = nrows * ncols
    print(f"griglia {nrows}×{ncols} = {n} @ {step}°", flush=True)
    grid = bytearray(n)
    print("raster LOM…", flush=True)
    _paint(lom, grid, south, west, step, nrows, ncols)
    print("raster ER…", flush=True)
    _paint(er, grid, south, west, step, nrows, ncols)
    counts = {str(k): grid.count(k) for k in range(6)}
    path = soil_grid.write_grid(
        south=south,
        west=west,
        step=step,
        nrows=nrows,
        ncols=ncols,
        cells=bytes(grid),
        counts=counts,
        source_note="Suoli ER 1:50.000 (2021) + Lombardia 1:250.000 PH_1M",
    )
    info = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "cells": n,
        "counts": counts,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    print(f"ok {info}", flush=True)
    return info


if __name__ == "__main__":
    build()
