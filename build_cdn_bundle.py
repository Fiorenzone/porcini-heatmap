#!/usr/bin/env python3
"""Precalcola heatmap Italia → static/data/ per GitHub Pages / CDN.

Uso:
  python3 build_cdn_bundle.py
  PORCINI_HEAT_STEP=0.02 python3 build_cdn_bundle.py

Output:
  static/data/meta.json
  static/data/heatmap.bin.gz
  static/data/bulletins.json
  static/data/weather.json.gz  (stazioni, click dettaglio opzionale)
"""

from __future__ import annotations

import gzip
import json
import math
import os
import struct
import time
from datetime import date, datetime, timezone
from pathlib import Path

import bulletins
import forest_grid
import weather
from geo import idw_daily, in_italy, slope_aspect_twi
from model import forest_proxy, score_series

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "static" / "data"
MAGIC = b"PORCAMP1"
HEADER_LEN = 64
CELL_BYTES = 11  # i16 elev + u8 leaf + 4*(stage,p)
SPECIES = ("edulis", "pinophilus", "aestivalis", "aereus")
ITALY = (36.6, 6.6, 47.1, 18.5)
LEAF_CODE = {"altro": 1, "latifoglie": 2, "conifere": 3, "misto": 4}


def _forest_at(lat: float, lon: float, elev: float) -> tuple[str, str]:
    # CI/CDN: solo griglia compatta — niente pickle/shapefile.
    if forest_grid.exists():
        forest_grid.warm()
        g = forest_grid.lookup(lat, lon)
        if g is not None:
            return g
    return forest_proxy(lat, elev)


def _today_index(days: list[dict]) -> int:
    today = date.today().isoformat()
    for i, d in enumerate(days):
        if d["date"] >= today:
            return max(0, i - 1) if d["date"] > today else i
    return len(days) - 1


def _step() -> float:
    return float(os.environ.get("PORCINI_HEAT_STEP", "0.02"))


def _grid(south: float, west: float, north: float, east: float, step: float):
    lat0 = math.floor(south / step) * step
    lon0 = math.floor(west / step) * step
    nrows = int(round((north - lat0) / step)) + 1
    ncols = int(round((east - lon0) / step)) + 1
    rows = []
    for i in range(nrows):
        lat = round(lat0 + i * step, 5)
        row = []
        for j in range(ncols):
            lon = round(lon0 + j * step, 5)
            row.append((lat, lon))
        rows.append(row)
    return rows, lat0, lon0, nrows, ncols


def _pack_header(south, west, north, east, step, nrows, ncols) -> bytes:
    body = struct.pack(
        "<5dIIBB",
        float(south),
        float(west),
        float(north),
        float(east),
        float(step),
        int(nrows),
        int(ncols),
        len(SPECIES),
        CELL_BYTES,
    )
    raw = MAGIC + body
    if len(raw) < HEADER_LEN:
        raw = raw + b"\0" * (HEADER_LEN - len(raw))
    elif len(raw) > HEADER_LEN:
        raw = raw[:HEADER_LEN]
    return raw


def _pack_cell(elev, leaf_code: int, by_sp: list[tuple[int, int]]) -> bytes:
    # elev None → -32768
    ev = -32768 if elev is None else int(max(-32000, min(32000, round(elev))))
    parts = [struct.pack("<hB", ev, leaf_code & 0xFF)]
    for stage, p100 in by_sp:
        parts.append(struct.pack("BB", stage & 0xFF, p100 & 0xFF))
    while len(parts) < 1 + len(SPECIES):
        parts.append(struct.pack("BB", 0, 0))
    return b"".join(parts)


def build(*, skip_meteo: bool = False) -> dict:
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    step = _step()
    south, west, north, east = ITALY

    forest_grid.warm()

    if not skip_meteo or not weather.stations():
        print("meteo refresh Italia…", flush=True)
        info = weather.refresh(full_italy=True, step=0.45, quick=False)
        print(f"meteo: {info}", flush=True)
    else:
        print(f"meteo cache: {len(weather.stations())} stazioni", flush=True)

    stations = weather.stations()
    if len(stations) < 20:
        raise SystemExit(f"troppo poche stazioni: {len(stations)}")

    rows, lat0, lon0, nrows, ncols = _grid(south, west, north, east, step)
    flat = [p for row in rows for p in row if in_italy(p[0], p[1])]
    print(f"griglia {nrows}x{ncols} step={step} punti IT={len(flat)}", flush=True)

    print("quote (IDW da stazioni, no elevation API)…", flush=True)
    elev_map: dict[tuple[float, float], float] = {}
    known = [(s["lat"], s["lon"], float(s["elev"])) for s in stations if s.get("elev") is not None]
    if not known:
        raise SystemExit("stazioni senza elev")
    for lat, lon in flat:
        # IDW k=4
        ranked = sorted(known, key=lambda t: (t[0] - lat) ** 2 + (t[1] - lon) ** 2)[:4]
        wsum = 0.0
        zsum = 0.0
        for la, lo, zz in ranked:
            d2 = (la - lat) ** 2 + (lo - lon) ** 2
            w = 1e12 if d2 < 1e-12 else 1.0 / d2
            wsum += w
            zsum += w * zz
        elev_map[(round(lat, 4), round(lon, 4))] = zsum / wsum
        elev_map[(lat, lon)] = zsum / wsum
    terrain = slope_aspect_twi(rows, elev_map)
    month = date.today().month

    buf = bytearray(_pack_header(lat0, lon0, lat0 + (nrows - 1) * step, lon0 + (ncols - 1) * step, step, nrows, ncols))
    filled = 0
    t_score = time.perf_counter()
    for i, row in enumerate(rows):
        if i % 40 == 0:
            print(f"  riga {i}/{nrows} filled={filled}", flush=True)
        for lat, lon in row:
            if not in_italy(lat, lon):
                buf.extend(_pack_cell(None, 0, [(0, 0)] * 4))
                continue
            key = (round(lat, 4), round(lon, 4))
            z = elev_map.get(key)
            if z is None:
                z = elev_map.get((lat, lon))
            if z is None:
                buf.extend(_pack_cell(None, 0, [(0, 0)] * 4))
                continue
            slope, aspect, twi = terrain.get(key, (5.0, 180.0, 8.0))
            leaf, leaf_note = _forest_at(lat, lon, z)
            series = idw_daily(stations, lat, lon)
            if not series:
                buf.extend(_pack_cell(z, LEAF_CODE.get(leaf, 1), [(0, 0)] * 4))
                continue
            label = leaf_note.split(": ")[-1] if leaf_note.startswith("Carta forestale") else None
            ti = _today_index(series)
            by = []
            for sp in SPECIES:
                scored = score_series(
                    series,
                    species=sp,
                    leaf=leaf,
                    elev=z,
                    slope=slope,
                    aspect=aspect,
                    twi=twi,
                    month=month,
                    today_index=ti,
                    label=label,
                )
                today = scored.get("today") or {}
                stage = int(scored.get("stage") or 0)
                p = today.get("p") or 0.0
                by.append((stage, int(max(0, min(100, round(p * 100))))))
            buf.extend(_pack_cell(z, LEAF_CODE.get(leaf, 1), by))
            filled += 1

    raw_path = OUT / "heatmap.bin"
    gz_path = OUT / "heatmap.bin.gz"
    raw_path.write_bytes(buf)
    with gzip.open(gz_path, "wb", compresslevel=6) as gz:
        gz.write(buf)

    # weather slim for client (optional detail)
    weather_slim = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stations": [
            {
                "lat": s["lat"],
                "lon": s["lon"],
                "elev": s.get("elev"),
                "days": s.get("days") or [],
            }
            for s in stations
        ],
    }
    with gzip.open(OUT / "weather.json.gz", "wb", compresslevel=6) as gz:
        gz.write(json.dumps(weather_slim, separators=(",", ":")).encode())

    print("bollettini…", flush=True)
    bulls = bulletins.prefetch()
    (OUT / "bulletins.json").write_text(json.dumps(bulls, ensure_ascii=False))

    meta = {
        "magic": "PORCAMP1",
        "fetched_at": weather_slim["fetched_at"],
        "south": lat0,
        "west": lon0,
        "north": lat0 + (nrows - 1) * step,
        "east": lon0 + (ncols - 1) * step,
        "step": step,
        "nrows": nrows,
        "ncols": ncols,
        "species": list(SPECIES),
        "cell_bytes": CELL_BYTES,
        "header_bytes": HEADER_LEN,
        "filled": filled,
        "stations": len(stations),
        "heatmap_bytes": len(buf),
        "heatmap_gz_bytes": gz_path.stat().st_size,
        "build_seconds": round(time.perf_counter() - t0, 1),
        "score_seconds": round(time.perf_counter() - t_score, 1),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2), flush=True)
    return meta


if __name__ == "__main__":
    skip = os.environ.get("PORCINI_SKIP_METEO", "") in ("1", "true", "yes")
    build(skip_meteo=skip)
