"""Griglia, maschera grossolana Italia, pendenza e TWI dalle quote vicine."""

from __future__ import annotations

import math

# Contorno grossolano, basta a tenere il mare fuori dalla griglia meteo.
ITALY = [
    (47.1, 6.7),
    (46.5, 8.0),
    (46.4, 10.5),
    (46.7, 12.2),
    (46.6, 13.7),
    (45.6, 13.5),
    (45.0, 12.4),
    (44.0, 12.6),
    (43.5, 13.6),
    (42.4, 14.3),
    (41.9, 16.0),
    (40.1, 18.5),
    (39.8, 18.4),
    (39.0, 17.1),
    (37.9, 16.1),
    (37.5, 15.1),
    (36.7, 15.1),
    (36.7, 14.5),
    (37.5, 12.5),
    (38.1, 12.4),
    (38.2, 15.5),
    (40.6, 14.8),
    (41.2, 13.0),
    (42.0, 11.6),
    (42.4, 10.5),
    (43.5, 10.0),
    (44.0, 9.5),
    (44.4, 8.3),
    (43.8, 7.5),
    (44.3, 8.7),
    (45.1, 7.0),
    (45.9, 6.9),
    (46.5, 6.7),
    (47.1, 6.7),
]


def _inside(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        lat_i, lon_i = poly[i]
        lat_j, lon_j = poly[j]
        if ((lon_i > lon) != (lon_j > lon)) and (
            lat < (lat_j - lat_i) * (lon - lon_i) / ((lon_j - lon_i) or 1e-12) + lat_i
        ):
            c = not c
        j = i
    return c


def in_italy(lat: float, lon: float) -> bool:
    return _inside(lat, lon, ITALY)


def weather_points(south: float, west: float, north: float, east: float, step: float) -> list[tuple[float, float]]:
    pts = []
    lat = south
    while lat <= north + 1e-9:
        lon = west
        while lon <= east + 1e-9:
            if in_italy(lat, lon):
                pts.append((round(lat, 3), round(lon, 3)))
            lon += step
        lat += step
    return pts


_LADDER = (0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.08, 0.12, 0.16)


def lattice(south: float, west: float, north: float, east: float, step: float = 0.005, cap: int | None = None):
    import os

    if cap is None:
        cap = int(os.environ.get("PORCINI_LATTICE_CAP", "1600"))
    # Griglia fissa, agganciata a multipli dello step, con un anello fuori dal riquadro.
    step = _LADDER[-1]
    for candidate in _LADDER:
        nlat = int((north - south) / candidate) + 3
        nlon = int((east - west) / candidate) + 3
        if nlat * nlon <= cap:
            step = candidate
            break
    lat0 = math.floor(south / step) * step - step
    lon0 = math.floor(west / step) * step - step
    lat1 = math.ceil(north / step) * step + step
    lon1 = math.ceil(east / step) * step + step
    rows = []
    lat = lat0
    while lat <= lat1 + 1e-9:
        row = []
        lon = lon0
        while lon <= lon1 + 1e-9:
            row.append((round(lat, 5), round(lon, 5)))
            lon += step
        if row:
            rows.append(row)
        lat += step
    return rows, step


def slope_aspect_twi(rows: list[list[tuple[float, float]]], elev: dict[tuple[float, float], float]):
    """Finite difference sulle quote. Ritorna dict (lat,lon) -> slope_deg, aspect_deg, twi."""
    out = {}
    for i, row in enumerate(rows):
        for j, (lat, lon) in enumerate(row):
            z = elev.get((round(lat, 4), round(lon, 4)))
            if z is None:
                z = elev.get((lat, lon))
            if z is None:
                continue
            nbrs = []
            for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ii, jj = i + di, j + dj
                if 0 <= ii < len(rows) and 0 <= jj < len(rows[ii]):
                    nlat, nlon = rows[ii][jj]
                    nz = elev.get((round(nlat, 4), round(nlon, 4)))
                    if nz is None:
                        continue
                    dist_m = _meters(lat, lon, nlat, nlon)
                    if dist_m > 1:
                        nbrs.append((nlat - lat, nlon - lon, (nz - z) / dist_m, nz))
            if len(nbrs) < 2:
                slope, aspect, twi = 5.0, 180.0, 8.0
            else:
                dzdy = sum(n[2] for n in nbrs if abs(n[0]) >= abs(n[1])) or 0.0
                dzdx = sum(n[2] for n in nbrs if abs(n[1]) > abs(n[0])) or 0.0
                n_y = max(1, sum(1 for n in nbrs if abs(n[0]) >= abs(n[1])))
                n_x = max(1, sum(1 for n in nbrs if abs(n[1]) > abs(n[0])))
                dzdy /= n_y
                dzdx /= n_x
                slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
                aspect = (math.degrees(math.atan2(dzdx, -dzdy)) + 360) % 360
                lower = sum(1 for n in nbrs if n[3] < z - 5)
                twi = math.log(80.0 / math.tan(math.radians(max(slope, 0.4)))) + lower
            key = (round(lat, 4), round(lon, 4))
            out[key] = (round(slope, 1), round(aspect, 0), round(twi, 2))
    return out


def _meters(lat1, lon1, lat2, lon2) -> float:
    dlat = (lat2 - lat1) * 111_320
    dlon = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlon)


def idw_daily(stations: list[dict], lat: float, lon: float, k: int = 4, max_deg: float = 1.0) -> list[dict] | None:
    if not stations:
        return None
    ranked = sorted(
        stations,
        key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2,
    )[:k]
    d0 = (ranked[0]["lat"] - lat) ** 2 + (ranked[0]["lon"] - lon) ** 2
    if d0 > max_deg * max_deg:
        return None
    weights = []
    for s in ranked:
        d2 = (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2
        weights.append(1.0 if d2 < 1e-12 else 1.0 / d2)
    wsum = sum(weights)
    base = ranked[0]["days"]
    merged = []
    keys = ("tmean", "tsoil", "precip", "rh", "et0", "wind", "rad", "smoist")
    for i in range(len(base)):
        row = {"date": base[i]["date"]}
        for key in keys:
            acc = 0.0
            ww = 0.0
            for s, w in zip(ranked, weights):
                if i >= len(s["days"]):
                    continue
                val = s["days"][i].get(key)
                if val is None:
                    continue
                acc += w * val
                ww += w
            row[key] = None if ww == 0 else acc / ww
        merged.append(row)
    return merged
