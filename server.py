"""Server HTTP. Locale: 127.0.0.1 — hosting: 0.0.0.0 via HOST env."""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bulletins
import forest_er
import forest_grid
import forest_lom
import weather
from geo import idw_daily, lattice, slope_aspect_twi
from model import forest_proxy, score_series

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")
_lock = threading.Lock()


def _json(handler, code: int, payload) -> None:
    raw = json.dumps(payload).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _today_index(days: list[dict]) -> int:
    today = date.today().isoformat()
    for i, d in enumerate(days):
        if d["date"] >= today:
            return max(0, i - 1) if d["date"] > today else i
    return len(days) - 1


def _forest_at(lat: float, lon: float, elev: float) -> tuple[str, str]:
    # Griglia compatta (hosting): se c'è il file, non caricare i pickle da 450MB.
    if forest_grid.exists():
        forest_grid.warm()
        g = forest_grid.lookup(lat, lon)
        if g is not None:
            return g
        return forest_proxy(lat, elev)
    hits = []
    for src in (forest_er.lookup(lat, lon), forest_lom.lookup(lat, lon)):
        if src and src[0] != "altro":
            hits.append(src)
    if hits:
        return hits[0]
    inside = forest_er.lookup(lat, lon) or forest_lom.lookup(lat, lon)
    if inside:
        return inside
    return forest_proxy(lat, elev)


def _feature(
    lat: float,
    lon: float,
    elev: float,
    slope: float,
    aspect: float,
    twi: float,
    species: str,
    stations: list,
    month: int,
    with_days: bool = False,
) -> dict | None:
    leaf, leaf_note = _forest_at(lat, lon, elev)
    series = idw_daily(stations, lat, lon)
    if not series:
        return None
    scored = score_series(
        series,
        species=species,
        leaf=leaf,
        elev=elev,
        slope=slope,
        aspect=aspect,
        twi=twi,
        month=month,
        today_index=_today_index(series),
        label=leaf_note.split(": ")[-1] if leaf_note.startswith("Carta forestale") else None,
    )
    today = scored.get("today") or {}
    out = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "elev": elev,
        "slope": slope,
        "aspect": aspect,
        "twi": twi,
        "leaf": leaf,
        "leaf_note": leaf_note,
        "decision": scored["decision"],
        "stage": scored.get("stage") or 0,
        "p": today.get("p"),
        "abundance": today.get("abundance"),
        "t": today.get("t"),
        "shock": today.get("shock"),
        "best_date": (scored.get("best") or {}).get("date"),
        "habitat": scored.get("habitat"),
        "host": scored.get("host"),
        "host_hit": scored.get("host_hit"),
        "since_rain": today.get("since_rain"),
        "rain26": today.get("rain26"),
        "vpd": today.get("vpd"),
        "wind_ms": today.get("wind_ms"),
        "evap_demand": today.get("evap_demand"),
        "desiccation": today.get("desiccation"),
    }
    if with_days:
        out["days"] = scored.get("days") or []
    return out


def viewport(south, west, north, east, species: str, with_days: bool = False) -> dict:
    rows, step = lattice(south, west, north, east)
    flat = [p for row in rows for p in row]
    elev = weather.elev_local(flat)
    terrain = slope_aspect_twi(rows, elev)
    stations = weather.stations()
    features = []
    month = date.today().month
    for lat, lon in flat:
        key = (round(lat, 4), round(lon, 4))
        z = elev.get(key)
        if z is None:
            continue
        slope, aspect, twi = terrain.get(key, (5.0, 180.0, 8.0))
        feat = _feature(lat, lon, z, slope, aspect, twi, species, stations, month, with_days)
        if feat:
            features.append(feat)
    return {"step_deg": round(step, 4), "count": len(features), "cells": features, "species": species}


def score_point(lat: float, lon: float, species: str, with_days: bool = False) -> dict | None:
    """Score esatto sul click — niente nearest di una mini-griglia."""
    pad = 0.012
    rows, _step = lattice(lat - pad, lon - pad, lat + pad, lon + pad)
    flat = [p for row in rows for p in row]
    if (lat, lon) not in flat:
        flat = list(flat) + [(lat, lon)]
    elev_map = weather.elev_local(flat)
    terrain = slope_aspect_twi(rows, elev_map)
    key = (round(lat, 4), round(lon, 4))
    z = elev_map.get(key)
    if z is None:
        # fallback: punto più vicino con quota
        if not elev_map:
            return None
        z = min(elev_map.items(), key=lambda kv: (kv[0][0] - lat) ** 2 + (kv[0][1] - lon) ** 2)[1]
    slope, aspect, twi = terrain.get(key, (5.0, 180.0, 8.0))
    if key not in terrain and terrain:
        tk = min(terrain.keys(), key=lambda k: (k[0] - lat) ** 2 + (k[1] - lon) ** 2)
        slope, aspect, twi = terrain[tk]
    return _feature(lat, lon, z, slope, aspect, twi, species, weather.stations(), date.today().month, with_days)


def cell_detail(lat: float, lon: float, species: str) -> dict:
    here = score_point(lat, lon, species, with_days=True)
    if not here:
        return {"error": "nessuna cella"}
    year = None
    year_note = None
    try:
        past = weather.history_year(lat, lon)
        if past:
            rain = sum((d.get("precip") or 0) for d in past)
            year = {"rain26": round(rain, 1), "from": past[0]["date"], "to": past[-1]["date"]}
    except Exception as exc:
        year_note = str(exc)
    here["last_year"] = year
    here["last_year_note"] = year_note
    return here


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[porcini]", fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        if parsed.path == "/api/status":
            age = weather.cache_age_hours()
            _json(
                self,
                200,
                {
                    "weather_age_hours": None if age is None else round(age, 2),
                    "stations": len(weather.stations()),
                    "needs_refresh": weather.needs_refresh(),
                    "refresh_busy": weather.refresh_busy(),
                    "meteo_error": weather._LAST_ERROR,
                    "meteo_ok_at": weather._LAST_OK_AT,
                    "forest_er": forest_er.ready(),
                    "forest_lom": forest_lom.ready(),
                    "forest_grid": forest_grid.ready(),
                },
            )
            return
        if parsed.path == "/api/meteo/probe":
            _json(self, 200, weather.probe())
            return
        if parsed.path == "/api/bulletins":
            _json(self, 200, bulletins.fetch_all())
            return
        if parsed.path == "/api/bulletins/compare":
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
                stage = int(q.get("stage", ["0"])[0])
            except (KeyError, ValueError):
                _json(self, 400, {"error": "punto o stage mancante"})
                return
            try:
                _json(self, 200, bulletins.compare(lat, lon, stage))
            except Exception as exc:
                _json(self, 502, {"error": str(exc)})
            return
        if parsed.path == "/api/viewport":
            try:
                south, west, north, east = (float(q[k][0]) for k in ("s", "w", "n", "e"))
            except (KeyError, ValueError):
                _json(self, 400, {"error": "bbox mancante"})
                return
            species = q.get("species", ["edulis"])[0]
            if species not in ("edulis", "pinophilus", "aestivalis", "aereus"):
                _json(self, 400, {"error": "specie"})
                return
            if not weather.stations():
                _json(self, 409, {"error": "meteo assente", "needs_refresh": True})
                return
            try:
                payload = viewport(south, west, north, east, species)
            except Exception as exc:
                _json(self, 502, {"error": str(exc)})
                return
            _json(self, 200, payload)
            return
        if parsed.path == "/api/cell":
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
            except (KeyError, ValueError):
                _json(self, 400, {"error": "punto mancante"})
                return
            species = q.get("species", ["edulis"])[0]
            _json(self, 200, cell_detail(lat, lon, species))
            return
        if parsed.path == "/api/refresh":
            full = q.get("italy", ["0"])[0] == "1"
            # async=1 (default su hosting): non bloccare HTTP oltre timeout Render
            async_mode = q.get("async", ["1"])[0] != "0"
            if async_mode:
                info = weather.start_refresh_bg(full_italy=full, quick=True)
                _json(self, 200, info)
                return
            with _lock:
                try:
                    info = weather.refresh(full_italy=full, quick=True)
                except Exception as exc:
                    _json(self, 502, {"error": str(exc)})
                    return
            _json(self, 200, info)
            return
        path = STATIC / ("index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/"))
        if not path.resolve().is_relative_to(STATIC.resolve()) or not path.is_file():
            self.send_error(404)
            return
        kind = (
            "text/html" if path.suffix == ".html"
            else "application/json" if path.suffix == ".json"
            else "text/css" if path.suffix == ".css"
            else "text/javascript"
        )
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    # Griglia compatta (~MB). Se manca e ci sono pickle locali → build una tantum.
    def _forest():
        import os
        import time

        t0 = time.perf_counter()
        build = os.environ.get("PORCINI_BUILD_GRID", "1") not in ("0", "false", "no")
        print("carta forestale (griglia)…", flush=True)
        info = forest_grid.ensure(build_if_missing=build)
        if info.get("ok"):
            print(
                f"grid ok: {info.get('cells')} celle, {info.get('bytes', 0)/1e6:.1f}MB, "
                f"step={info.get('step')} ({time.perf_counter() - t0:.1f}s)",
                flush=True,
            )
            return
        print(f"grid skip: {info.get('error')} — fallback pickle/proxy", flush=True)
        # Legacy: pickle in background se presenti
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fer = pool.submit(forest_er.warm)
            flm = pool.submit(forest_lom.warm)
            try:
                n_er = fer.result()
                n_lom = flm.result()
                print(f"pickle ER {n_er}, LOM {n_lom} ({time.perf_counter() - t0:.1f}s)", flush=True)
            except Exception as exc:
                print(f"pickle skip: {exc}", flush=True)

    threading.Thread(target=_forest, daemon=True, name="forest-warm").start()

    def _bull():
        try:
            print("scarico bollettini (Geoticket + enti)…", flush=True)
            info = bulletins.prefetch()
            n = sum(len(r.get("areas") or []) for r in info.get("reports") or [])
            by = {r.get("source"): len(r.get("areas") or []) for r in info.get("reports") or []}
            print(f"bollettini: {n} aree {by}, errori {len(info.get('errors') or [])}", flush=True)
        except Exception as exc:
            print(f"bollettini skip: {exc}", flush=True)

    threading.Thread(target=_bull, daemon=True, name="bulletins-warm").start()

    if weather.needs_refresh():
        print("avvio meteo background (quick)…", flush=True)
        weather.start_refresh_bg(full_italy=True, quick=True)
    else:
        print(f"meteo cache ok ({weather.cache_age_hours():.1f}h)", flush=True)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"porcini heatmap su http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
