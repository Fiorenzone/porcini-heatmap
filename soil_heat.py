"""Calore estivo del suolo profondo. Anomalia JJA vs le due estati precedenti."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "soil" / "jja_heat.json"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
VAR = "soil_temperature_28_to_100cm_mean"

_LOCK = threading.Lock()
_POINTS: list[dict] | None = None


def _window(today: date | None = None) -> tuple[int, int, int]:
    """Anno dell'estate che carica l'autunno, più i due anni di confronto."""
    today = today or date.today()
    year = today.year if today.month >= 9 else today.year - 1
    return year, year - 1, year - 2


def _mean(vals: list) -> float | None:
    clean = [float(v) for v in vals if v is not None]
    if len(clean) < 20:
        return None
    return sum(clean) / len(clean)


def _jja(times: list, values: list, year: int) -> float | None:
    picked = []
    for i, day in enumerate(times):
        if i >= len(values):
            break
        if day.startswith(f"{year}-06-") or day.startswith(f"{year}-07-") or day.startswith(f"{year}-08-"):
            picked.append(values[i])
    return _mean(picked)


def _fetch_chunk(points: list[tuple[float, float]], start: date, end: date) -> list[dict]:
    url = ARCHIVE + "?" + urllib.parse.urlencode(
        {
            "latitude": ",".join(str(p[0]) for p in points),
            "longitude": ",".join(str(p[1]) for p in points),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": VAR,
            "timezone": "Europe/Rome",
        }
    )
    req = urllib.request.Request(url, headers={"User-Agent": "porcini-heatmap"})
    with urllib.request.urlopen(req, timeout=90) as res:
        payload = json.loads(res.read().decode())
    blocks = payload if isinstance(payload, list) else [payload]
    cur, prev, old = _window()
    out = []
    for block, (lat, lon) in zip(blocks, points):
        daily = block.get("daily") or {}
        times = daily.get("time") or []
        vals = daily.get(VAR) or []
        now = _jja(times, vals, cur)
        base_vals = [v for v in (_jja(times, vals, prev), _jja(times, vals, old)) if v is not None]
        if now is None or not base_vals:
            continue
        base = sum(base_vals) / len(base_vals)
        out.append(
            {
                "lat": round(float(lat), 3),
                "lon": round(float(lon), 3),
                "elev": block.get("elevation"),
                "jja": round(now, 2),
                "base": round(base, 2),
                "anomaly": round(now - base, 2),
            }
        )
    return out


def _targets() -> list[tuple[float, float]]:
    import weather
    from geo import weather_points

    stations = weather.stations()
    if len(stations) >= 8:
        return [(round(float(s["lat"]), 3), round(float(s["lon"]), 3)) for s in stations]
    south, west, north, east = weather.ITALY_BOX
    return weather_points(south, west, north, east, 0.45)


def _load() -> list[dict]:
    global _POINTS
    if _POINTS is not None:
        return _POINTS
    if not CACHE.exists():
        _POINTS = []
        return _POINTS
    data = json.loads(CACHE.read_text())
    _POINTS = data.get("points") or []
    return _POINTS


def lookup(lat: float, lon: float) -> float | None:
    """Anomalia °C interpolata. Fuori portata → None (niente falsa penalità)."""
    pts = _load()
    if not pts:
        return None
    ranked = sorted(pts, key=lambda p: (p["lat"] - lat) ** 2 + (p["lon"] - lon) ** 2)[:4]
    d0 = (ranked[0]["lat"] - lat) ** 2 + (ranked[0]["lon"] - lon) ** 2
    if d0 > 0.55 * 0.55:
        return None
    acc = ww = 0.0
    for p in ranked:
        d2 = (p["lat"] - lat) ** 2 + (p["lon"] - lon) ** 2
        w = 1.0 if d2 < 1e-12 else 1.0 / d2
        acc += w * float(p["anomaly"])
        ww += w
    return None if ww == 0 else acc / ww


def cache_age_hours() -> float | None:
    if not CACHE.exists():
        return None
    data = json.loads(CACHE.read_text())
    stamp = data.get("fetched_at")
    if not stamp:
        return None
    then = datetime.fromisoformat(stamp)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


def _stored() -> tuple[int | None, dict[tuple[float, float], dict]]:
    if not CACHE.exists():
        return None, {}
    data = json.loads(CACHE.read_text())
    have = {}
    for row in data.get("points") or []:
        have[(round(float(row["lat"]), 3), round(float(row["lon"]), 3))] = row
    return data.get("year"), have


def refresh(*, max_age_hours: float = 24 * 14, force: bool = False) -> dict:
    import time
    import urllib.error

    age = cache_age_hours()
    cur, _prev, old = _window()
    year, have = _stored()
    if force or year != cur:
        have = {}
    points = _targets()
    def _near(lat: float, lon: float) -> bool:
        # Open-Meteo snap: un punto già preso conta se cade sulla stessa cella.
        for slat, slon in have:
            if abs(slat - lat) <= 0.08 and abs(slon - lon) <= 0.08:
                return True
        return False

    missing = [p for p in points if (round(p[0], 3), round(p[1], 3)) not in have and not _near(p[0], p[1])]
    if not force and year == cur and not missing and age is not None and age < max_age_hours:
        return {"ok": True, "cached": True, "points": len(have), "year": cur}
    if force or year != cur:
        missing = points
    start = date(old, 6, 1)
    end = date(cur, 8, 31)
    print(f"suolo JJA {old}–{cur}: {len(missing)} punti mancanti", flush=True)
    for i in range(0, len(missing), 8):
        chunk = missing[i : i + 8]
        got = False
        for attempt in range(3):
            try:
                for row in _fetch_chunk(chunk, start, end):
                    have[(row["lat"], row["lon"])] = row
                got = True
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    print(f"suolo JJA chunk {i}: {exc}", flush=True)
                    break
                wait = 35 * (attempt + 1)
                print(f"suolo JJA 429, attendo {wait}s", flush=True)
                time.sleep(wait)
            except Exception as exc:
                print(f"suolo JJA chunk {i}: {exc}", flush=True)
                break
        if not got:
            break
        time.sleep(1.5)
    rows = list(have.values())
    if len(rows) < 30:
        _, old = _stored()
        for key, row in old.items():
            have.setdefault(key, row)
        rows = list(have.values())
    if not rows:
        return {"ok": False, "error": "nessun punto", "points": 0}
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "year": cur,
        "var": VAR,
        "points": rows,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload))
    global _POINTS
    with _LOCK:
        _POINTS = rows
    return {"ok": True, "points": len(rows), "year": cur}


def start_bg() -> None:
    def run() -> None:
        try:
            print(f"suolo JJA: {refresh()}", flush=True)
        except Exception as exc:
            print(f"suolo JJA skip: {exc}", flush=True)

    threading.Thread(target=run, daemon=True).start()
