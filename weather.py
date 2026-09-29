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
        "wind_direction_10m_dominant",
        "wind_gusts_10m_max",
        "shortwave_radiation_sum",
        "soil_temperature_0_to_7cm_mean",
        "soil_moisture_0_to_7cm_mean",
    ]
)
# Quick: meno variabili → risposta Open-Meteo più piccola/veloce (Render outbound lento)
DAILY_QUICK = ",".join(
    [
        "temperature_2m_mean",
        "precipitation_sum",
        "et0_fao_evapotranspiration",
        "relative_humidity_2m_mean",
        "wind_speed_10m_max",
        "wind_direction_10m_dominant",
        "wind_gusts_10m_max",
        "soil_temperature_0_to_7cm_mean",
        "soil_moisture_0_to_7cm_mean",
    ]
)

LOM_ER = (44.0, 8.4, 46.6, 12.6)
ITALY_BOX = (36.6, 6.6, 47.1, 18.5)

_LAST_ERROR: str | None = None
_LAST_OK_AT: str | None = None


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


def _fetch(
    points: list[tuple[float, float]],
    past_days: int,
    forecast_days: int,
    *,
    daily: str | None = None,
    timeout: int = 45,
) -> list[dict]:
    import time

    global _LAST_ERROR, _LAST_OK_AT
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {
            "latitude": ",".join(str(p[0]) for p in points),
            "longitude": ",".join(str(p[1]) for p in points),
            "daily": daily or DAILY,
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "Europe/Rome",
        }
    )
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "porcini-heatmap/render"},
    )
    # Backoff su 429: Open-Meteo free punisce burst
    backoffs = (0, 25, 60, 120)
    payload = None
    last: Exception | None = None
    for attempt, wait in enumerate(backoffs):
        if wait:
            print(f"meteo wait {wait}s (try {attempt+1})", flush=True)
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = json.loads(res.read().decode())
            _LAST_ERROR = None
            _LAST_OK_AT = datetime.now(timezone.utc).isoformat()
            break
        except urllib.error.HTTPError as exc:
            last = exc
            _LAST_ERROR = f"HTTP {exc.code}: {exc.reason}"
            if exc.code != 429:
                raise
            continue
        except Exception as exc:
            last = exc
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
            continue
    if payload is None:
        raise last if last else RuntimeError("open-meteo fail")
    blocks = payload if isinstance(payload, list) else [payload]
    out = []
    for block in blocks:
        daily_block = block.get("daily") or {}
        times = daily_block.get("time") or []
        n = len(times)

        def col(name: str):
            arr = daily_block.get(name) or []
            return [arr[i] if i < len(arr) else None for i in range(n)]

        tmean, tsoil, precip, rh, et0, wind, wdir, gust, rad, smoist = (
            col("temperature_2m_mean"),
            col("soil_temperature_0_to_7cm_mean"),
            col("precipitation_sum"),
            col("relative_humidity_2m_mean"),
            col("et0_fao_evapotranspiration"),
            col("wind_speed_10m_max"),
            col("wind_direction_10m_dominant"),
            col("wind_gusts_10m_max"),
            col("shortwave_radiation_sum"),
            col("soil_moisture_0_to_7cm_mean"),
        )
        days = []
        for i, day in enumerate(times):
            days.append(
                {
                    "date": day,
                    "tmean": tmean[i],
                    "tsoil": tsoil[i],
                    "precip": precip[i],
                    "rh": rh[i],
                    "et0": et0[i],
                    "wind": wind[i],
                    "wdir": wdir[i],
                    "gust": gust[i],
                    "rad": rad[i],
                    "smoist": smoist[i],
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


def client_plan(*, full_italy: bool = True) -> dict:
    """Griglia da scaricare nel browser. Il server non chiama Open-Meteo."""
    step = 0.45 if full_italy else 0.22
    south, west, north, east = ITALY_BOX if full_italy else LOM_ER
    pts = weather_points(south, west, north, east, step)
    return {
        "points": [[lat, lon] for lat, lon in pts],
        "daily": DAILY,
        "past_days": 26,
        "forecast_days": 14,
        "chunk": 12,
        "timezone": "Europe/Rome",
        "count": len(pts),
    }


_DAY_KEYS = ("date", "tmean", "tsoil", "precip", "rh", "et0", "wind", "wdir", "gust", "rad", "smoist")


def ingest(rows: list, *, full_italy: bool = True) -> dict:
    """Salva stazioni già scaricate dal browser (IP del visitatore, non di Render)."""
    global _LAST_ERROR, _LAST_OK_AT
    clean: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        try:
            lat = float(raw["lat"])
            lon = float(raw["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (35.0 <= lat <= 48.5 and 6.0 <= lon <= 19.5):
            continue
        days_in = raw.get("days")
        if not isinstance(days_in, list):
            continue
        days = []
        for d in days_in[:48]:
            if not isinstance(d, dict) or not isinstance(d.get("date"), str):
                continue
            if len(d["date"]) < 8:
                continue
            days.append({k: d.get(k) for k in _DAY_KEYS})
        if len(days) < 7:
            continue
        elev = raw.get("elev")
        try:
            elev_v = float(elev) if elev is not None else None
        except (TypeError, ValueError):
            elev_v = None
        clean.append({"lat": round(lat, 3), "lon": round(lon, 3), "elev": elev_v, "days": days})
        if len(clean) >= 900:
            break
    if not clean:
        return {"count": len(stations()), "added": 0, "error": "nessuna stazione valida"}
    elev_map = _load(ELEV, {})
    for s in clean:
        if s.get("elev") is not None:
            elev_map[f"{s['lat']:.3f},{s['lon']:.3f}"] = s["elev"]
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "fetched_at": now,
        "full_italy": bool(full_italy),
        "count": len(clean),
        "stations": clean,
        "source": "browser",
    }
    with _refresh_lock:
        _save(CACHE, data)
        _save(ELEV, elev_map)
        _LAST_ERROR = None
        _LAST_OK_AT = now
    return {"count": len(clean), "fetched_at": now, "full_italy": bool(full_italy)}


def probe() -> dict:
    """1 punto Open-Meteo — diagnostica networking da Render."""
    global _LAST_ERROR
    try:
        rows = _fetch([(44.5, 10.0)], past_days=3, forecast_days=1, daily=DAILY_QUICK, timeout=20)
        return {
            "ok": True,
            "stations": len(rows),
            "sample_days": len((rows[0].get("days") or [])),
            "error": None,
        }
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return {"ok": False, "stations": 0, "error": _LAST_ERROR}


def refresh(full_italy: bool = False, step: float | None = None, *, quick: bool = False, force: bool = False) -> dict:
    import time

    if step is None:
        if quick:
            # Griglia rada: meno call → meno 429 su free tier
            step = 0.9 if full_italy else 0.55
        else:
            step = 0.45 if full_italy else 0.22
    past_days = 14 if quick else 26
    forecast_days = 5 if quick else 14
    daily_vars = DAILY_QUICK if quick else DAILY
    chunk_size = 4 if quick else 15
    south, west, north, east = ITALY_BOX if full_italy else LOM_ER
    pts = weather_points(south, west, north, east, step)
    # Cap assoluto punti (Render + Open-Meteo free)
    if quick and len(pts) > 40:
        pts = pts[:: max(1, len(pts) // 40)][:40]
    old = [] if force else stations()
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
    print(f"meteo refresh: {len(need)} punti, chunk={chunk_size}, quick={quick}", flush=True)

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
            "last_error": _LAST_ERROR,
        }
        _save(CACHE, data)
        _save(ELEV, elev)
        return data

    hit_429 = False
    for i in range(0, len(need), chunk_size):
        if hit_429 and failed >= 2:
            print("meteo stop: troppi 429, tengo cache parziale", flush=True)
            break
        chunk = need[i : i + chunk_size]
        try:
            stations_out.extend(
                _fetch(
                    chunk,
                    past_days=past_days,
                    forecast_days=forecast_days,
                    daily=daily_vars,
                    timeout=40,
                )
            )
            print(f"meteo chunk ok {len(stations_out)}", flush=True)
        except Exception as exc:
            failed += 1
            if "429" in str(exc) or (getattr(exc, "code", None) == 429):
                hit_429 = True
            print(f"meteo chunk fail: {exc}", flush=True)
            time.sleep(15)
            try:
                stations_out.extend(
                    _fetch(
                        chunk,
                        past_days=past_days,
                        forecast_days=forecast_days,
                        daily=daily_vars,
                        timeout=40,
                    )
                )
            except Exception as exc2:
                failed += 1
                if "429" in str(exc2) or (getattr(exc2, "code", None) == 429):
                    hit_429 = True
                print(f"meteo chunk retry fail: {exc2}", flush=True)
                continue
        _persist()
        time.sleep(1.5 if quick else 0.8)
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
        # Se abbiamo già stazioni fresche, non martellare Open-Meteo
        n = len(stations())
        age = cache_age_hours()
        if n >= 15 and age is not None and age < 3:
            return {"started": False, "busy": False, "count": n, "skipped": "cache fresca"}
        _refresh_busy = True

    def _run():
        global _refresh_busy
        try:
            # Una sola passata rada (nord). Italia intera solo se chiesto E nord ok.
            refresh(full_italy=False, quick=True)
            if full_italy and len(stations()) >= 8:
                import time

                time.sleep(20)  # pausa anti-429 prima della 2ª ondata
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
