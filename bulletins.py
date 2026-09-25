"""Bollettini pubblici. Fonte primaria: Geoticket. Enti solo dove GT manca."""

from __future__ import annotations

import json
import math
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path

import geoticket_dump

ROOT = Path(__file__).resolve().parent
MANUAL = ROOT / "data" / "bulletins"
DISK = ROOT / "data" / "cache" / "bulletins.json"
GEOTICKET_URL = "https://geoticket.it/mushroom-growth-table"

_STOP = {
    "consorzio",
    "comunalia",
    "comunalie",
    "forestale",
    "valorizzazione",
    "territorio",
    "riserva",
    "foresta",
    "demaniale",
    "comune",
    "zona",
    "monte",
    "aperto",
    "martedi",
    "sabato",
    "domenica",
    "giallo",
    "bianco",
    "righe",
    "nascita",
    "assente",
    "irrilevante",
    "scarsa",
    "discreta",
    "abbondante",
    "straordinaria",
    "localit",
    "borgo",
    "parco",
}

# Centri approssimati per disegnare il layer. Non sono confini catastali.
BORGOTARO_PLACES = [
    ("gorro", 44.50, 9.73),
    ("belforte", 44.47, 9.76),
    ("baselica", 44.55, 9.62),
    ("molinatico", 44.58, 9.60),
    ("bedonia", 44.51, 9.63),
    ("codorso", 44.47, 9.55),
    ("albareto", 44.45, 9.70),
    ("bardi", 44.63, 9.73),
    ("ragola", 44.62, 9.68),
    ("tornolo", 44.48, 9.63),
    ("berceto", 44.51, 9.99),
    ("tarodine", 44.46, 9.68),
    ("penna", 44.48, 9.50),
    ("val taro", 44.49, 9.77),
    ("borgo val di taro", 44.49, 9.77),
]

PARCO_PLACES = [
    ("albareto", 44.45, 9.70),
    ("alpe di succiso", 44.33, 10.19),
    ("apella", 44.35, 10.05),
    ("bagnone", 44.31, 9.99),
    ("campiglia", 44.12, 9.82),
    ("cerreto alpi", 44.30, 10.24),
    ("cerreto laghi", 44.30, 10.24),
    ("comano", 44.29, 10.13),
    ("cusna", 44.29, 10.40),
    ("lago santo", 44.40, 10.00),
    ("monte molinatico", 44.58, 9.60),
    ("monte penna", 44.48, 9.49),
    ("passo del bracco", 44.38, 9.50),
    ("passo dello zovallo", 44.55, 9.45),
    ("pradarena", 44.27, 10.30),
    ("prati di sara", 44.36, 10.15),
    ("pratospilla", 44.36, 10.08),
    ("rigoso", 44.39, 9.95),
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "porcini-heatmap/local"})
    with urllib.request.urlopen(req, timeout=40) as res:
        return res.read().decode("utf-8", "replace")


def _stale(iso: str | None) -> bool:
    if not iso:
        return True
    try:
        then = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (date.today() - then).days > 3


def _place(name: str, table) -> tuple[float, float] | None:
    low = name.lower()
    for key, lat, lon in table:
        if key in low:
            return lat, lon
    return None


def parse_borgotaro(html: str) -> dict:
    updated = None
    m = re.search(r"Ultimo aggiornamento:\s*(\d{2})/(\d{2})/(\d{4})", html)
    if m:
        updated = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    areas = []
    for row in re.findall(r"<tr[\s\S]*?</tr>", html, flags=re.I):
        level = re.search(r"(\d)\s*/\s*5", row)
        if not level:
            continue
        text = re.sub(r"<[^>]+>", " ", row)
        text = text.replace("&nbsp;", " ")
        text = re.sub(r"\s+", " ", text).strip()
        name = text.split(f"{level.group(1)}/5")[0].strip(" |-")
        if len(name) < 4:
            continue
        pos = _place(name, BORGOTARO_PLACES)
        areas.append(
            {
                "name": name[:180],
                "level": int(level.group(1)),
                "lat": None if not pos else pos[0],
                "lon": None if not pos else pos[1],
                "source": "Fungo di Borgotaro",
                "url": "https://www.fungodiborgotaro.com/stannonascendo.php",
                "paid": True,
                "paid_note": "Comunalia/consorzio a pagamento — serve permesso/tesserino raccolta.",
            }
        )
    return {
        "source": "Fungo di Borgotaro",
        "updated": updated,
        "stale": _stale(updated),
        "areas": areas,
    }


def parse_parco(html: str) -> dict:
    areas = []
    for row in re.findall(r"<tr[\s\S]*?</tr>", html, flags=re.I):
        text = re.sub(r"<[^>]+>", " ", row)
        text = text.replace("&nbsp;", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 3 or text.lower().startswith("localit"):
            continue
        pos = _place(text, PARCO_PLACES)
        if not pos:
            continue
        # Icone: fungo-10 … fungo-30 (o desat = spento). Scala 10–30 → 1–5.
        level = None
        fresh = "sconosciuta"
        for src in re.findall(r"src=\"([^\"]+)\"", row, flags=re.I):
            low = src.lower()
            m = re.search(r"fungo-(\d+)", low)
            if not m:
                continue
            if "desat" in low:
                level = 0
            else:
                # 10→1 … 30→5
                level = max(0, min(5, int(round(int(m.group(1)) / 5))))
            break
        if "2 gior" in text.lower():
            fresh = "<=2 giorni"
        note = None
        if level is None:
            note = "Località in elenco, nessuna icona di crescita in tabella oggi"
        areas.append(
            {
                "name": text[:120],
                "level": level,
                "freshness": fresh,
                "lat": pos[0],
                "lon": pos[1],
                "source": "Parco Appennino Tosco-Emiliano",
                "url": "https://www.parcoappennino.it/cercafunghi.php",
                "note": note,
            }
        )
    # Senza data in pagina: l'icona vuota non è zero. Segnato osservato oggi solo se c'è una riga.
    updated = date.today().isoformat() if areas else None
    return {
        "source": "Parco Appennino Tosco-Emiliano",
        "updated": updated,
        "stale": False if areas else True,
        "areas": areas,
        "note": "La crescita è un'icona. Senza numero nel nome file il voto resta vuoto, non zero.",
    }


def manual_files() -> list[dict]:
    out = []
    if not MANUAL.exists():
        return out
    for path in sorted(MANUAL.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        data["stale"] = _stale(data.get("updated"))
        data.setdefault("source", path.stem)
        out.append(data)
    return out


def _tokens(name: str) -> set[str]:
    s = re.sub(r"[^a-zàèéìòù0-9\s]", " ", (name or "").lower())
    return {t for t in s.split() if len(t) >= 4 and t not in _STOP}


def _parse_gt_updated(raw: str | None) -> str | None:
    """'25/09/2026 16:50' → '2026-09-25'."""
    if not raw:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw.strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def geoticket_report(refresh: bool = True) -> dict | None:
    """Report Geoticket (lat/lon reali). Prima fonte mappa."""
    if refresh:
        try:
            geoticket_dump.refresh()
        except Exception:
            pass
    if not geoticket_dump.DISK.exists():
        return None
    try:
        data = json.loads(geoticket_dump.DISK.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    areas = []
    for a in data.get("areas") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        aid = a.get("id")
        areas.append(
            {
                "name": a.get("name"),
                "level": a.get("level"),
                "level_word": a.get("level_word"),
                "lat": a["lat"],
                "lon": a["lon"],
                "region": a.get("region"),
                "province": a.get("province"),
                "city": a.get("city"),
                "source": "Geoticket",
                "url": f"https://geoticket.it/area/{aid}" if aid else GEOTICKET_URL,
                "paid": True,
                "paid_note": "Permesso raccolta a pagamento — serve ticket Geoticket (non accesso libero).",
            }
        )
    if not areas:
        return None
    updated = _parse_gt_updated(data.get("last_update"))
    return {
        "source": "Geoticket",
        "updated": updated,
        "stale": _stale(updated),
        "url": GEOTICKET_URL,
        "areas": areas,
        "note": "Fonte primaria. Punti ticket con lat/lon API.",
    }


def _covered_by_gt(area: dict, gt_areas: list[dict], max_km: float = 22.0) -> bool:
    """True se ente già coperto da Geoticket (stesso nome-zona + vicino)."""
    lat, lon = area.get("lat"), area.get("lon")
    if lat is None or lon is None:
        return False
    atok = _tokens(area.get("name") or "")
    if not atok:
        return False
    for g in gt_areas:
        glat, glon = g.get("lat"), g.get("lon")
        if glat is None or glon is None:
            continue
        if _km(float(lat), float(lon), float(glat), float(glon)) > max_km:
            continue
        gtok = _tokens(g.get("name") or "") | _tokens(g.get("city") or "")
        if atok & gtok:
            return True
    return False


def _filter_enti(report: dict, gt_areas: list[dict]) -> dict:
    """Tieni solo zone enti assenti su Geoticket."""
    if not gt_areas:
        return report
    kept = [a for a in (report.get("areas") or []) if not _covered_by_gt(a, gt_areas)]
    out = dict(report)
    out["areas"] = kept
    dropped = len(report.get("areas") or []) - len(kept)
    if dropped:
        out["dropped_overlap_gt"] = dropped
    return out


_CACHE: dict | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL = 1800.0  # 30 min in RAM; disco = fallback + avvio


def _read_disk() -> dict | None:
    if not DISK.exists():
        return None
    try:
        return json.loads(DISK.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_disk(payload: dict) -> None:
    DISK.parent.mkdir(parents=True, exist_ok=True)
    DISK.write_text(json.dumps(payload, ensure_ascii=False))


def _pull_net() -> dict:
    """Geoticket prima, poi HTML enti (solo zone non coperte da GT)."""
    from datetime import timezone

    reports = []
    errors = []
    gt_areas: list[dict] = []

    try:
        gt = geoticket_report(refresh=True)
        if gt:
            reports.append(gt)
            gt_areas = gt.get("areas") or []
        else:
            errors.append({"source": "geoticket", "error": "nessuna area"})
    except Exception as exc:
        errors.append({"source": "geoticket", "error": str(exc)})
        try:
            gt = geoticket_report(refresh=False)
            if gt:
                reports.append(gt)
                gt_areas = gt.get("areas") or []
        except Exception:
            pass

    for name, url, parser in (
        ("borgotaro", "https://www.fungodiborgotaro.com/stannonascendo.php", parse_borgotaro),
        ("parco", "https://www.parcoappennino.it/cercafunghi.php", parse_parco),
    ):
        try:
            reports.append(_filter_enti(parser(_get(url)), gt_areas))
        except Exception as exc:  # rete o HTML cambiato
            errors.append({"source": name, "error": str(exc)})
    reports.extend(manual_files())
    return {
        "reports": reports,
        "errors": errors,
        "primary": "Geoticket",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def prefetch() -> dict:
    """Avvio: rete → disco. Se rete fallisce, tieni disco vecchio."""
    import time

    global _CACHE, _CACHE_AT
    try:
        fresh = _pull_net()
        # Se almeno un report ha aree o nessun errore totale → salva
        if fresh.get("reports") or not fresh.get("errors"):
            _write_disk(fresh)
            _CACHE, _CACHE_AT = fresh, time.time()
            return fresh
    except Exception as exc:
        fresh = {"reports": [], "errors": [{"source": "prefetch", "error": str(exc)}]}
    disk = _read_disk()
    if disk:
        disk = dict(disk)
        disk["errors"] = list(disk.get("errors") or []) + list(fresh.get("errors") or [])
        disk["stale_disk"] = True
        _CACHE, _CACHE_AT = disk, time.time()
        return disk
    _CACHE, _CACHE_AT = fresh, time.time()
    return fresh


def _has_geoticket(payload: dict | None) -> bool:
    return any(r.get("source") == "Geoticket" for r in (payload or {}).get("reports") or [])


def fetch_all(force: bool = False) -> dict:
    import time

    global _CACHE, _CACHE_AT
    now = time.time()
    disk = _read_disk()
    if not force and _CACHE is not None and now - _CACHE_AT < _CACHE_TTL:
        # Disco aggiornato con GT ma RAM ancora pre-merge → preferisci disco
        if disk is not None and _has_geoticket(disk) and not _has_geoticket(_CACHE):
            _CACHE, _CACHE_AT = disk, now
            return disk
        return _CACHE
    if force:
        return prefetch()
    # RAM scaduta: prova disco subito (UI snappy), poi rete in background non qui
    if disk is not None:
        _CACHE, _CACHE_AT = disk, now
        return disk
    return prefetch()


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111.32
    dlon = (lon2 - lon1) * 111.32 * max(0.2, abs(math.cos(math.radians(lat1))))
    return (dlat * dlat + dlon * dlon) ** 0.5


# Heatmap stage → fascia bollettino 0–5 attesa (min, max).
_STAGE_BAND = {
    1: (0, 1),  # idonea ferma
    2: (0, 2),  # incubazione: nascite ancora poche o nulle
    3: (2, 3),  # avvio
    4: (3, 4),  # buona
    5: (4, 5),  # eccezionale
}

_LEVEL_WORD = {
    0: "assente",
    1: "irrilevante",
    2: "scarsa",
    3: "discreta",
    4: "abbondante",
    5: "straordinaria",
}


def compare(lat: float, lon: float, stage: int, max_km: float = 28.0) -> dict:
    """Incrocio heatmap ↔ bollettini. Preferisci Geoticket se distanze simili."""
    data = fetch_all()
    near = []
    for rep in data.get("reports") or []:
        for a in rep.get("areas") or []:
            if a.get("lat") is None or a.get("lon") is None:
                continue
            if a.get("level") is None:
                continue
            dist = _km(lat, lon, a["lat"], a["lon"])
            if dist > max_km:
                continue
            src = a.get("source") or rep.get("source")
            near.append(
                {
                    "name": a.get("name"),
                    "level": a["level"],
                    "level_word": a.get("level_word")
                    or _LEVEL_WORD.get(a["level"], str(a["level"])),
                    "source": src,
                    "url": a.get("url") or rep.get("url"),
                    "km": round(dist, 1),
                    "stale": bool(rep.get("stale")),
                    "updated": rep.get("updated"),
                }
            )
    # km, poi Geoticket prima a parità
    near.sort(key=lambda x: (x["km"], 0 if x.get("source") == "Geoticket" else 1))
    if not near:
        return {
            "verdict": "assente",
            "title": "Nessun bollettino vicino",
            "body": "Entro 28 km non c’è un voto pubblico numerico. La heatmap resta sola.",
            "areas": [],
        }
    best = near[0]
    lvl = best["level"]
    band = _STAGE_BAND.get(int(stage or 0))

    def _smentisce(why: str) -> dict:
        body = (
            f"{best['source']} su {best['name']} ({best['km']} km): "
            f"{lvl}/5 {best['level_word']}. {why}"
        )
        if best.get("stale"):
            body += " Attenzione: bollettino più vecchio di 3 giorni."
        return {
            "verdict": "smentisce",
            "title": "Il bollettino smentisce",
            "body": body,
            "areas": near[:3],
        }

    # Habitat chiuso in mappa ma campo forte (o viceversa) = smentita clamorosa
    if not band or stage <= 0:
        if lvl >= 3:
            return _smentisce(
                "La mappa dice habitat chiuso / 0%, il campo segnala nascite buone o abbondanti."
            )
        if lvl <= 1:
            return {
                "verdict": "conferma",
                "title": "Il bollettino conferma",
                "body": (
                    f"{best['source']} su {best['name']} ({best['km']} km): "
                    f"{lvl}/5 {best['level_word']}. Poco anche sul campo, allineato alla mappa chiusa."
                ),
                "areas": near[:3],
            }
        return {
            "verdict": "neutro",
            "title": "Bollettino vicino, habitat chiuso qui",
            "body": (
                f"{best['source']}: {best['name']} a {best['km']} km → "
                f"{lvl}/5 ({best['level_word']})."
            ),
            "areas": near[:3],
        }

    lo, hi = band
    mid = (lo + hi) / 2
    if lo <= lvl <= hi:
        verdict, title = "conferma", "Il bollettino conferma"
        body = (
            f"{best['source']} su {best['name']} ({best['km']} km): "
            f"{lvl}/5 {best['level_word']}. Allineato allo stadio della mappa."
        )
    elif abs(lvl - mid) < 1.6:
        verdict, title = "vicino", "Quasi allineato"
        body = (
            f"{best['source']} su {best['name']} ({best['km']} km): "
            f"{lvl}/5 {best['level_word']}. Piccola differenza, non un ribaltamento."
        )
    else:
        # Gap ≥ ~2 punti sulla scala 0–5
        if lvl > hi:
            why = "Il campo vede molte più nascite di quanto dica la mappa."
        else:
            why = "Il campo vede molte meno nascite di quanto dica la mappa."
        return _smentisce(why)

    if best.get("stale"):
        body += " Attenzione: bollettino più vecchio di 3 giorni."
    return {"verdict": verdict, "title": title, "body": body, "areas": near[:3]}
