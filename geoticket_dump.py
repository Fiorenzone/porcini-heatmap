"""Dump tabella crescite Geoticket su disco. Fonte primaria bollettini."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DISK = ROOT / "data" / "cache" / "geoticket_areas_dump.json"
API = "https://api.geoticket.it/app/area/list/level"

LEVEL = {
    None: None,
    0: "assente",
    1: "insufficiente",
    2: "scarsa",
    3: "buona",
    4: "abbondante",
    5: "miracolosa",
}

OURS = [
    {"name": "Fungo di Borgotaro", "url": "https://www.fungodiborgotaro.com/stannonascendo.php"},
    {"name": "Parco Appennino Tosco-Emiliano", "url": "https://www.parcoappennino.it/cercafunghi.php"},
]


def refresh() -> dict:
    """POST Geoticket → riscrive geoticket_areas_dump.json. Solo dump."""
    body = urllib.parse.urlencode({"type": "TYPE_MUSHROOM"}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        method="POST",
        headers={
            "User-Agent": "porcini-heatmap/local-dump",
            "Accept": "application/json",
            "Origin": "https://geoticket.it",
            "Referer": "https://geoticket.it/mushroom-growth-table",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=40) as res:
        payload = json.loads(res.read().decode())

    areas = []
    for a in payload.get("list") or []:
        lvl = a.get("area_level")
        areas.append(
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "region": a.get("region"),
                "province": a.get("province"),
                "city": a.get("city"),
                "lat": a.get("lat"),
                "lon": a.get("lon"),
                "level": lvl,
                "level_word": LEVEL.get(lvl, str(lvl)),
                "updated": a.get("date_update_level"),
                "paid": True,
                "paid_note": "Permesso raccolta a pagamento (ticket Geoticket)",
            }
        )
    areas.sort(key=lambda x: ((x.get("region") or ""), (x.get("name") or "")))

    out = {
        "note": "Dump Geoticket. Usato come fonte primaria bollettini (lat/lon API).",
        "geoticket_url": "https://geoticket.it/mushroom-growth-table",
        "api": f"POST {API} type=TYPE_MUSHROOM",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_update": payload.get("last_update"),
        "count": len(areas),
        "ours_sources": OURS,
        "comparison": {
            "ours_sources": len(OURS),
            "geoticket_areas": len(areas),
            "extra": "Primary=Geoticket. Enti HTML solo zone non coperte da GT.",
        },
        "areas": areas,
    }
    DISK.parent.mkdir(parents=True, exist_ok=True)
    DISK.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return {"count": len(areas), "last_update": out["last_update"], "path": str(DISK)}
