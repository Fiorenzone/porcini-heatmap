"""Campo meteo Open-Meteo. Griglia grossa, cache su disco."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from geo import weather_points

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache" / "weather.json"
ELEV = ROOT / "data" / "cache" / "elev.json"

DAILY = ",".join(
    [
        "temperature_2m_mean",
        "precipitation_sum",
        "et0_fao_evapotranspiration",
        "relative_humidity_2m_mean",
        "wind_speed_10m_max",
        "shortwave_radiation_sum",
        "soil_temperature_0_to_7cm_mean",
        "soil_moisture_0_to_7cm_mean",
    ]
)

LOM_ER = (44.0, 8.4, 46.6, 12.6)
ITALY_BOX = (36.6, 6.6, 47.1, 18.5)


def _load(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def cache_age_hours() -> float | None:
    data = _load(CACHE, None)
    if not data or "fetched_at" not in data:
        return None
    then = datetime.fromisoformat(data["fetched_at"])
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def stations() -> list[dict]:
    data = _load(CACHE, {})
    return data.get("stations") or []


def needs_refresh(max_hours: float = 6) -> bool:
    age = cache_age_hours()
    return age is None or age >= max_hours


def _fetch(points: list[tuple[float, float]], past_days: int, forecast_days: int) -> list[dict]:
    import time

    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {
            "latitude": ",".join(str(p[0]) for p in points),
            "longitude": ",".join(str(p[1]) for p in points),
            "daily": DAILY,
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "Europe/Rome",
        }
    )
    req = urllib.request.Request(url, method="GET")
    last = None
    for wait in (0, 4, 12, 25):
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                payload = json.loads(res.read().decode())
            break
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429:
                raise
    else:
        raise last
    blocks = payload if isinstance(payload, list) else [payload]
    out = []
    for block in blocks:
        daily = block["daily"]
        days = []
        for i, day in enumerate(daily["time"]):
            days.append(
                {
                    "date": day,
                    "tmean": daily["temperature_2m_mean"][i],
                    "tsoil": daily["soil_temperature_0_to_7cm_mean"][i],
                    "precip": daily["precipitation_sum"][i],
                    "rh": daily["relative_humidity_2m_mean"][i],
                    "et0": daily["et0_fao_evapotranspiration"][i],
                    "wind": daily["wind_speed_10m_max"][i],
                    "rad": daily["shortwave_radiation_sum"][i],
                    "smoist": daily["soil_moisture_0_to_7cm_mean"][i],
                }
            )
        out.append(
            {
                "lat": block["latitude"],
                "lon": block["longitude"],
                "elev": block.get("elevation"),
                "days": days,
            }
        )
    return out


def refresh(full_italy: bool = False, step: float | None = None, *, quick: bool = False) -> dict:
    import time

    if step is None:
        if quick:
            step = 0.55 if full_italy else 0.35
        else:
            step = 0.45 if full_italy else 0.22
    past_days = 20 if quick else 26
    forecast_days = 7 if quick else 14
    south, west, north, east = ITALY_BOX if full_italy else LOM_ER
    pts = weather_points(south, west, north, east, step)
    old = stations()
    kept = []
    need = []
    if full_italy and old:
        for lat, lon in pts:
            near = min(((s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2) for s in old)
            if near < (step * 0.55) ** 2:
                continue
            need.append((lat, lon))
        kept = list(old)
    else:
        need = pts
    stations_out = list(kept)
    failed = 0
    elev = _load(ELEV, {})

    def _persist():
        for s in stations_out:
            if s.get("elev") is not None:
                elev[f"{s['lat']:.3f},{s['lon']:.3f}"] = s["elev"]
        data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "full_italy": bool(full_italy and failed == 0 and len(stations_out) > 50),
            "count": len(stations_out),
            "stations": stations_out,
            "failed_chunks": failed,
            "quick": quick,
        }
        _save(CACHE, data)
        _save(ELEV, elev)
        return data

    for i in range(0, len(need), 25):
        chunk = need[i : i + 25]
        try:
            stations_out.extend(_fetch(chunk, past_days=past_days, forecast_days=forecast_days))
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            failed += 1
            time.sleep(8)
            try:
                stations_out.extend(_fetch(chunk, past_days=past_days, forecast_days=forecast_days))
            except urllib.error.HTTPError:
                failed += 1
                continue
        # Salva subito: Render timeout non lascia cache vuota
        _persist()
        time.sleep(0.35)
    if len(stations_out) <= len(kept) and full_italy and kept:
        return {
            "count": len(kept),
            "fetched_at": None,
            "full_italy": False,
            "error": "429, restano solo stazioni nord",
            "failed_chunks": failed,
            "needed": len(need),
        }
    data = _persist()
    return {
        "count": len(stations_out),
        "fetched_at": data["fetched_at"],
        "full_italy": data["full_italy"],
        "failed_chunks": failed,
        "added": len(stations_out) - len(kept),
        "quick": quick,
    }


_refresh_lock = __import__("threading").Lock()
_refresh_busy = False


def start_refresh_bg(*, full_italy: bool = False, quick: bool = True) -> dict:
    """Avvia refresh in background (Render: risposta HTTP immediata)."""
    global _refresh_busy
    with _refresh_lock:
        if _refresh_busy:
            return {"started": False, "busy": True, "count": len(stations())}
        _refresh_busy = True

    def _run():
        global _refresh_busy
        try:
            refresh(full_italy=False, quick=True)
            if full_italy:
                refresh(full_italy=True, quick=True)
        except Exception as exc:
            print(f"meteo bg fail: {exc}", flush=True)
        finally:
            _refresh_busy = False

    __import__("threading").Thread(target=_run, daemon=True, name="meteo-bg").start()
    return {"started": True, "busy": True, "count": len(stations())}


def refresh_busy() -> bool:
    return _refresh_busy


def _elevation_api(points: list[tuple[float, float]]) -> list[float]:
    import time
    url = "https://api.open-meteo.com/v1/elevation?" + urllib.parse.urlencode(
        {
            "latitude": ",".join(str(p[0]) for p in points),
            "longitude": ",".join(str(p[1]) for p in points),
        }
    )
    last = None
    for wait in (0, 2, 5):
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=60) as res:
                payload = json.loads(res.read().decode())
            return payload["elevation"]
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429:
                raise
    raise last


def _fill_from_stations(points: list[tuple[float, float]], found: dict) -> None:
    known = [(s["lat"], s["lon"], s["elev"]) for s in stations() if s.get("elev") is not None]
    if not known:
        return
    for lat, lon in points:
        best = min(known, key=lambda s: (s[0] - lat) ** 2 + (s[1] - lon) ** 2)
        found[(lat, lon)] = best[2]


def elev_local(points: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    """Quota già in cache, altrimenti IDW dalle stazioni. Nessuna chiamata di rete."""
    elev = _load(ELEV, {})
    known = [(s["lat"], s["lon"], s["elev"]) for s in stations() if s.get("elev") is not None]
    found = {}
    for lat, lon in points:
        key = (round(lat, 4), round(lon, 4))
        stored = elev.get(f"{key[0]:.4f},{key[1]:.4f}")
        if stored is None:
            stored = elev.get(f"{key[0]:.3f},{key[1]:.3f}")
        if stored is not None:
            found[key] = stored
            continue
        if not known:
            continue
        ranked = sorted(known, key=lambda s: (s[0] - lat) ** 2 + (s[1] - lon) ** 2)[:4]
        weights = []
        for s in ranked:
            d2 = (s[0] - lat) ** 2 + (s[1] - lon) ** 2
            weights.append(1.0 if d2 < 1e-12 else 1.0 / d2)
        wsum = sum(weights) or 1.0
        found[key] = sum(z * w for (_, _, z), w in zip(ranked, weights)) / wsum
    return found


def elevations_for(points: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    elev = _load(ELEV, {})
    missing = []
    found = {}
    for lat, lon in points:
        key = (round(lat, 4), round(lon, 4))
        stored = elev.get(f"{key[0]:.4f},{key[1]:.4f}")
        if stored is None:
            stored = elev.get(f"{key[0]:.3f},{key[1]:.3f}")
        if stored is None:
            missing.append(key)
        else:
            found[key] = stored
    for i in range(0, len(missing), 80):
        chunk = missing[i : i + 80]
        try:
            values = _elevation_api(chunk)
        except urllib.error.HTTPError:
            _fill_from_stations(chunk, found)
            continue
        for (lat, lon), z in zip(chunk, values):
            if z is None:
                continue
            found[(lat, lon)] = z
            elev[f"{lat:.4f},{lon:.4f}"] = z
    if missing:
        _save(ELEV, elev)
    return found


def history_year(lat: float, lon: float) -> list[dict]:
    """Stessa finestra di 26 giorni, un anno fa. Archive Open-Meteo."""
    from datetime import date, timedelta

    end = date.today() - timedelta(days=365)
    start = end - timedelta(days=25)
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": DAILY,
            "timezone": "Europe/Rome",
        }
    )
    with urllib.request.urlopen(url, timeout=60) as res:
        payload = json.loads(res.read().decode())
    daily = payload["daily"]
    days = []
    for i, day in enumerate(daily["time"]):
        days.append(
            {
                "date": day,
                "tmean": daily["temperature_2m_mean"][i],
                "tsoil": daily["soil_temperature_0_to_7cm_mean"][i],
                "precip": daily["precipitation_sum"][i],
                "rh": daily["relative_humidity_2m_mean"][i],
                "et0": daily["et0_fao_evapotranspiration"][i],
                "wind": daily["wind_speed_10m_max"][i],
                "rad": daily["shortwave_radiation_sum"][i],
            }
        )
    return days
