#!/usr/bin/env python3
"""Rasterizza pickle ER+LOM → forest_grid.bin (una tantum, offline)."""

from __future__ import annotations

import time

import forest_er
import forest_grid
import forest_lom

# Copertura unione ER+Lombardia. Step 0.005° ≈ 550 m — abbastanza per heatmap.
SOUTH, WEST = 43.55, 8.35
NORTH, EAST = 46.75, 13.05
STEP = 0.005


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


def _paint(polys, grid_leaf, grid_lab, labels, lab_index, south, west, step, nrows, ncols, prefer_bosco=True):
    """Rasterizza poligoni sulla griglia. prefer_bosco: non sovrascrivere bosco con altro."""
    code = forest_grid.CODE_LEAF
    for pi, poly in enumerate(polys):
        minlat, minlon, maxlat, maxlon, leaf, label, ring = poly
        iy0 = max(0, int((minlat - south) / step))
        iy1 = min(nrows - 1, int((maxlat - south) / step))
        ix0 = max(0, int((minlon - west) / step))
        ix1 = min(ncols - 1, int((maxlon - west) / step))
        if iy0 > iy1 or ix0 > ix1:
            continue
        lc = code.get(leaf, 1)
        if label not in lab_index:
            if len(labels) >= 65534:
                li = 0
            else:
                labels.append(label)
                lab_index[label] = len(labels)  # 1-based
                li = lab_index[label]
        else:
            li = lab_index[label]
        hi, lo = (li >> 8) & 0xFF, li & 0xFF
        for iy in range(iy0, iy1 + 1):
            lat = south + (iy + 0.5) * step
            row = iy * ncols
            for ix in range(ix0, ix1 + 1):
                lon = west + (ix + 0.5) * step
                if lat < minlat or lat > maxlat or lon < minlon or lon > maxlon:
                    continue
                if not _inside(lat, lon, ring):
                    continue
                i = row + ix
                prev = grid_leaf[i]
                if prev and prefer_bosco and prev >= 2 and lc == 1:
                    continue  # tieni bosco già scritto
                if prev >= 2 and lc >= 2 and prev != lc:
                    # conflitto: misto
                    grid_leaf[i] = 4
                else:
                    grid_leaf[i] = lc
                grid_lab[i * 2] = hi
                grid_lab[i * 2 + 1] = lo
        if pi and pi % 50000 == 0:
            print(f"  … {pi}/{len(polys)} poligoni", flush=True)


def build(step: float = STEP) -> dict:
    t0 = time.perf_counter()
    print("carico pickle ER…", flush=True)
    er = forest_er._load()
    print(f"  ER {len(er)}", flush=True)
    print("carico pickle LOM…", flush=True)
    lom = forest_lom._load()
    print(f"  LOM {len(lom)}", flush=True)

    south, west = SOUTH, WEST
    nrows = int((NORTH - south) / step) + 1
    ncols = int((EAST - west) / step) + 1
    n = nrows * ncols
    print(f"griglia {nrows}×{ncols} = {n} celle @ {step}°", flush=True)

    grid_leaf = bytearray(n)  # 0
    grid_lab = bytearray(n * 2)
    labels: list[str] = []
    lab_index: dict[str, int] = {}

    print("raster ER…", flush=True)
    _paint(er, grid_leaf, grid_lab, labels, lab_index, south, west, step, nrows, ncols)
    print("raster LOM…", flush=True)
    _paint(lom, grid_leaf, grid_lab, labels, lab_index, south, west, step, nrows, ncols)

    leaf = bytes(grid_leaf)
    label_hi = bytes(grid_lab[0::2])
    label_lo = bytes(grid_lab[1::2])
    path = forest_grid.write_grid(
        south=south,
        west=west,
        step=step,
        nrows=nrows,
        ncols=ncols,
        leaf=leaf,
        label_hi=label_hi,
        label_lo=label_lo,
        labels=labels,
        source_note="Carta forestale ER 2025 + Lombardia",
    )
    bosco = sum(1 for b in leaf if b >= 2)
    dt = time.perf_counter() - t0
    info = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "cells": n,
        "bosco_cells": bosco,
        "labels": len(labels),
        "seconds": round(dt, 1),
    }
    print(f"ok {info}", flush=True)
    return info


if __name__ == "__main__":
    build()
