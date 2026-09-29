"""Pioggia osservata ARPA sopra Open-Meteo. Solo i giorni già chiusi.

Emilia-Romagna: ERG5 Arpae, griglia ~5 km, DAILY_PREC (analisi da rete).
Lombardia: pluviometri ARPA (SODA), somma giornaliera. Se il servizio risponde 429
si tiene Open-Meteo per quei punti. Il forecast non si tocca.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CELLS_CSV = ROOT / "data" / "arpa" / "erg5_cells.csv"
CACHE = ROOT / "data" / "cache" / "arpa_rain.json"
ERG5_ZIP = "https://dati-simc.arpae.it/opendata/erg5v2/timeseries/{code}/{code}_{year}.zip"
LOM_META = "https://www.dati.lombardia.it/resource/nf78-nj6b.json"
LOM_DATA = "https://www.dati.lombardia.it/resource/647i-nhxk.json"

# Mezza cella ERG5 è ~0.023°. 0.04° tiene il vicino senza saltare alla cella dopo.
ERG5_MAX_DEG = 0.04
LOM_MAX_DEG = 0.25
UA = {"User-Agent": "porcini-heatmap"}

_LOCK = threading.Lock()
_CELLS: list[tuple[float, float, dict[str, float]]] = []
_GAUGES: list[tuple[float, float, dict[str, float]]] = []
_READY = False
_FETCHING = False


def ready() -> bool:
    return _READY and bool(_CELLS or _GAUGES)


def _load_cache() -> bool:
    global _CELLS, _GAUGES, _READY
    if not CACHE.exists():
        return False
    try:
        data = json.loads(CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    cells = []
    for row in data.get("erg5") or []:
        days = {k: float(v) for k, v in (row.get("days") or {}).items()}
        cells.append((float(row["lat"]), float(row["lon"]), days))
    gauges = []
    for row in data.get("lom") or []:
        days = {k: float(v) for k, v in (row.get("days") or {}).items()}
        gauges.append((float(row["lat"]), float(row["lon"]), days))
    with _LOCK:
        _CELLS = cells
        _GAUGES = gauges
        _READY = bool(cells or gauges)
    return _READY


def warm() -> dict:
    if not _READY:
        _load_cache()
    return {
        "ok": _READY,
        "erg5": len(_CELLS),
        "lom": len(_GAUGES),
        "fetched_at": _fetched_at(),
    }


def _fetched_at() -> str | None:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text()).get("fetched_at")
    except (OSError, json.JSONDecodeError):
        return None


def cache_age_hours() -> float | None:
    stamp = _fetched_at()
    if not stamp:
        return None
    then = datetime.fromisoformat(stamp)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _erg5_catalog() -> list[tuple[str, float, float]]:
    out = []
    with CELLS_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            lat = float(row["Lat (WGS84)"].replace(",", "."))
            lon = float(row["Lon (WGS84)"].replace(",", "."))
            out.append((row["Code"].strip(), lat, lon))
    return out


def _erg5_one(code: str, lat: float, lon: float, year: int, since: str) -> tuple | None:
    url = ERG5_ZIP.format(code=code, year=year)
    try:
        raw = _get(url, timeout=40)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        name = f"{code}_{year}_d.csv"
        if name not in zf.namelist():
            hits = [n for n in zf.namelist() if n.endswith("_d.csv")]
            if not hits:
                return None
            name = hits[0]
        text = zf.read(name).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, UnicodeError):
        return None
    days: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        day = (row.get("PragaDate") or "")[:10]
        if len(day) < 10 or day < since:
            continue
        try:
            mm = float(row.get("DAILY_PREC") or "")
        except ValueError:
            continue
        if mm < 0 or mm > 400:
            continue
        days[day] = round(mm, 1)
    if not days:
        return None
    return lat, lon, days


def _lom_sensors() -> list[tuple[str, float, float]]:
    out = []
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {
                "$select": "idsensore,lat,lng,datastop",
                "$where": "tipologia='Precipitazione'",
                "$limit": "500",
                "$offset": str(offset),
            }
        )
        payload = json.loads(_get(LOM_META + "?" + q, timeout=40).decode())
        if not isinstance(payload, list) or not payload:
            break
        for row in payload:
            if row.get("datastop"):
                continue
            try:
                out.append((str(row["idsensore"]), float(row["lat"]), float(row["lng"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(payload) < 500:
            break
        offset += 500
    return out


def _lom_chunk(ids: list[str], since: str) -> dict[str, dict[str, float]]:
    quoted = ",".join("'" + i.replace("'", "") + "'" for i in ids)
    q = urllib.parse.urlencode(
        {
            "$select": "date_trunc_ymd(data) as day, idsensore, sum(valore) as mm",
            "$where": f"data >= '{since}T00:00:00' AND idsensore in({quoted})",
            "$group": "day,idsensore",
            "$limit": "50000",
        }
    )
    payload = json.loads(_get(LOM_DATA + "?" + q, timeout=60).decode())
    if not isinstance(payload, list):
        return {}
    by: dict[str, dict[str, float]] = {}
    for row in payload:
        sid = str(row.get("idsensore") or "")
        day = str(row.get("day") or "")[:10]
        try:
            mm = float(row.get("mm"))
        except (TypeError, ValueError):
            continue
        if not sid or len(day) < 10 or mm < 0 or mm > 400:
            continue
        by.setdefault(sid, {})[day] = round(mm, 1)
    return by


def _lom_gauges(since: str) -> tuple[list, str | None]:
    """Pluviometri lombardi. Un 429 non butta via i chunk già letti."""
    try:
        sensors = _lom_sensors()
    except Exception as exc:
        print(f"arpa LOM skip: {exc}", flush=True)
        return [], str(exc)
    print(f"arpa LOM pluviometri: {len(sensors)}", flush=True)
    by_id: dict[str, dict[str, float]] = {}
    failed = 0
    stop = False
    for i in range(0, len(sensors), 12):
        if stop:
            break
        chunk = [s[0] for s in sensors[i : i + 12]]
        got = False
        for attempt in range(3):
            try:
                by_id.update(_lom_chunk(chunk, since))
                got = True
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    print(f"arpa LOM chunk fail: {exc}", flush=True)
                    break
                wait = 25 * (attempt + 1)
                print(f"arpa LOM 429, attendo {wait}s", flush=True)
                time.sleep(wait)
            except Exception as exc:
                print(f"arpa LOM chunk fail: {exc}", flush=True)
                break
        if not got:
            failed += 1
            stop = True
        time.sleep(1.2)
    gauges = []
    for sid, lat, lon in sensors:
        days = by_id.get(sid)
        if days:
            gauges.append((lat, lon, days))
    err = f"{failed} chunk senza dati" if failed else None
    return gauges, err


def _lom_retry_due(data: dict) -> bool:
    stamp = data.get("lom_retry_at")
    if not stamp:
        return True
    then = datetime.fromisoformat(stamp)
    return (datetime.now(timezone.utc) - then).total_seconds() >= 3600


def _topup_lom(data: dict) -> dict:
    """Completa i pluviometri senza riscaricare ERG5."""
    since = data.get("since") or (date.today() - timedelta(days=40)).isoformat()
    gauges, err = _lom_gauges(since)
    merged: dict[tuple[float, float], dict] = {}
    for row in data.get("lom") or []:
        merged[(round(float(row["lat"]), 4), round(float(row["lon"]), 4))] = row
    for lat, lon, days in gauges:
        merged[(round(lat, 4), round(lon, 4))] = {"lat": lat, "lon": lon, "days": days}
    data["lom"] = list(merged.values())
    data["lom_error"] = err
    data["lom_retry_at"] = datetime.now(timezone.utc).isoformat()
    CACHE.write_text(json.dumps(data))
    _load_cache()
    info = {"ok": True, "topup": True, "erg5": len(data.get("erg5") or []), "lom": len(data["lom"]), "lom_error": err}
    print(f"arpa lom topup {info}", flush=True)
    return info


def refresh(*, max_age_hours: float = 6.0, workers: int = 12, force: bool = False) -> dict:
    global _FETCHING
    age = cache_age_hours()
    if not force and age is not None and age < max_age_hours and _load_cache():
        data = json.loads(CACHE.read_text())
        if data.get("lom_error") and _lom_retry_due(data):
            return _topup_lom(data)
        return {"ok": True, "cached": True, "erg5": len(_CELLS), "lom": len(_GAUGES), "age_hours": round(age, 2)}
    with _LOCK:
        if _FETCHING:
            return {"ok": False, "error": "fetch già in corso"}
        _FETCHING = True
    try:
        return _refresh(workers)
    finally:
        with _LOCK:
            _FETCHING = False


def _refresh(workers: int) -> dict:
    since = (date.today() - timedelta(days=40)).isoformat()
    year = date.today().year
    catalog = _erg5_catalog()
    erg5 = []
    print(f"arpa ERG5: {len(catalog)} celle {year}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_erg5_one, code, lat, lon, year, since) for code, lat, lon in catalog]
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 200 == 0:
                print(f"  erg5 {done}/{len(catalog)}", flush=True)
            row = fut.result()
            if row:
                erg5.append(row)
    gauges, lom_err = _lom_gauges(since)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "erg5": [{"lat": lat, "lon": lon, "days": days} for lat, lon, days in erg5],
        "lom": [{"lat": lat, "lon": lon, "days": days} for lat, lon, days in gauges],
        "lom_error": lom_err,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload))
    _load_cache()
    info = {"ok": bool(erg5 or gauges), "erg5": len(erg5), "lom": len(gauges), "lom_error": lom_err}
    print(f"arpa ok {info}", flush=True)
    return info


def _nearest(lat: float, lon: float, rows: list, max_deg: float) -> dict[str, float] | None:
    best = None
    best_d = max_deg * max_deg
    for rlat, rlon, days in rows:
        d = (rlat - lat) ** 2 + (rlon - lon) ** 2
        if d < best_d:
            best_d = d
            best = days
    return best


def overlay(lat: float, lon: float, days: list[dict] | None) -> tuple[list[dict] | None, int]:
    """Sostituisce precip dei giorni osservati. Ritorna (serie, n giorni ARPA)."""
    if not days:
        return days, 0
    if not _READY:
        _load_cache()
    if not _READY:
        return days, 0
    today = date.today().isoformat()
    with _LOCK:
        cells = _CELLS
        gauges = _GAUGES
    erg = _nearest(lat, lon, cells, ERG5_MAX_DEG)
    gauge = _nearest(lat, lon, gauges, LOM_MAX_DEG)
    d_erg = _dist2_days(lat, lon, cells, erg) if erg is not None else None
    d_gau = _dist2_days(lat, lon, gauges, gauge) if gauge is not None else None
    src = None
    if d_erg is not None and (d_gau is None or d_erg <= d_gau):
        src = erg
    elif d_gau is not None:
        src = gauge
    if src is None:
        return days, 0
    out = []
    n = 0
    for d in days:
        row = dict(d)
        day = row.get("date") or ""
        if day and day <= today and day in src:
            row["precip"] = src[day]
            row["precip_src"] = "arpa"
            n += 1
        out.append(row)
    return out, n


def _dist2_days(lat: float, lon: float, rows: list, days: dict | None) -> float:
    if days is None:
        return 9e9
    for rlat, rlon, got in rows:
        if got is days:
            return (rlat - lat) ** 2 + (rlon - lon) ** 2
    return 9e9


def start_bg() -> None:
    def _run():
        try:
            refresh()
        except Exception as exc:
            print(f"arpa skip: {exc}", flush=True)

    threading.Thread(target=_run, daemon=True, name="arpa-rain").start()
