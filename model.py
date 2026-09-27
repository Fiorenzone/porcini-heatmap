"""Indice porcini: P(buttata) e abbondanza. Parametri dai paper, non un voto unico."""

from __future__ import annotations

import math
from datetime import date

SPECIES = {
    "edulis": {
        "name": "Boletus edulis",
        "topt": 13.0,
        "elev": (600, 1700),
        "leaf": {"latifoglie", "misto", "conifere"},
        "months": {7, 8, 9, 10, 11},
    },
    "pinophilus": {
        "name": "Boletus pinophilus",
        "topt": 11.0,
        "elev": (800, 1900),
        "leaf": {"conifere", "misto"},
        "months": {5, 6, 9, 10, 11},
    },
    "aestivalis": {
        "name": "Boletus aestivalis",
        "topt": 17.0,
        "elev": (200, 1200),
        "leaf": {"latifoglie", "misto"},
        "months": {5, 6, 7, 9},
    },
    "aereus": {
        "name": "Boletus aereus",
        "topt": 19.0,
        "elev": (0, 1000),
        "leaf": {"latifoglie"},
        "months": {6, 7, 8, 9, 10},
    },
}

P_VAI = 0.55
A_VAI = 45.0
P_ASPETTA = 0.40


def forest_proxy(lat: float, elev: float) -> tuple[str, str]:
    """Classe fogliare di ripiego. Non è la carta forestale regionale."""
    if elev >= 1500 and lat >= 45.2:
        return "conifere", "proxy quote: conifere di alta quota alpina"
    if elev >= 900:
        return "latifoglie", "proxy quote: latifoglie di quota (faggeta possibile, non certificata)"
    if elev >= 250:
        return "latifoglie", "proxy quote: latifoglie basse (quercia o castagno possibili, non certificati)"
    return "altro", "proxy quote: fuori bosco di porcino"


# Simbionti per specie. 1 = ospite tipico, 0 = incompatibile.
HOSTS = {
    "edulis": {"fagg": 1.0, "abete": 0.9, "abiet": 0.9, "pecc": 0.9, "picea": 0.9, "aceri": 0.3, "frassin": 0.3, "castagn": 0.8, "querc": 0.7, "rover": 0.7, "cerro": 0.7, "farnia": 0.6, "pino": 0.7, "laric": 0.6, "betul": 0.6, "carpin": 0.5, "ostri": 0.5, "orno": 0.5},
    "pinophilus": {"pino": 1.0, "pinet": 1.0, "mughet": 0.7, "abete": 0.8, "abiet": 0.8, "pecc": 0.8, "picea": 0.8, "aceri": 0.25, "frassin": 0.25, "laric": 0.7, "cembr": 0.7, "castagn": 0.7, "fagg": 0.6, "querc": 0.4},
    "aestivalis": {"querc": 1.0, "rover": 1.0, "cerro": 0.9, "farnia": 0.8, "castagn": 0.9, "fagg": 0.7, "aceri": 0.3, "frassin": 0.3, "carpin": 0.6, "ostri": 0.6, "orno": 0.6, "betul": 0.4},
    "aereus": {"rover": 1.0, "querc": 1.0, "cerro": 0.95, "farnia": 0.8, "castagn": 0.85, "aceri": 0.25, "frassin": 0.25, "ostri": 0.6, "orno": 0.6, "carpin": 0.5},
}

# Nessun porcino sotto questi: piantagioni e ripariali.
BAD_HOSTS = ("robini", "douglas", "ontano", "alnet", "pioppo", "salic", "eucalipt", "antropogen")


def host_factor(species: str, label: str | None) -> tuple[float, str | None]:
    """Moltiplicatore dal nome specie della carta forestale. Senza carta resta un ospite incerto."""
    if not label:
        return 0.85, "bosco non certificato"
    text = label.lower()
    if any(k in text for k in BAD_HOSTS):
        return 0.0, "bosco non da porcino"
    best = 0.0
    hit = None
    for key, val in HOSTS[species].items():
        if key in text and val > best:
            best = val
            hit = key
    if hit is None:
        return 0.75, "ospite non riconosciuto"
    return best, hit


def habitat_factor(species: str, leaf: str, elev: float, month: int) -> float:
    spec = SPECIES[species]
    lo, hi = spec["elev"]
    if month not in spec["months"] or leaf not in spec["leaf"]:
        return 0.0
    if elev < lo or elev > hi:
        # fascia morbida: 150 m fuori ancora un po', oltre zero
        dist = min(abs(elev - lo), abs(elev - hi))
        if dist > 200:
            return 0.0
        return 0.35
    mid = (lo + hi) / 2
    half = (hi - lo) / 2
    return 0.55 + 0.45 * (1 - abs(elev - mid) / half)


def _mean(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None and not math.isnan(v)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _therm(tmean: float | None, topt: float) -> float:
    if tmean is None:
        return 0.0
    return math.exp(-0.5 * ((tmean - topt) / 4.0) ** 2)


def _vpd_kpa(tmean: float | None, rh: float | None) -> float | None:
    """Deficit di pressione di vapore (kPa), Tetens. Driver primario perdita H2O sporocarpo."""
    if tmean is None or rh is None:
        return None
    # Magnus/Tetens sull'acqua (FAO-56 / meteorologia operativa)
    es = 0.6108 * math.exp((17.27 * tmean) / (tmean + 237.3))
    rh_c = max(0.0, min(100.0, float(rh)))
    return max(0.0, es * (1.0 - rh_c / 100.0))


def _wind_ms(wind_kmh: float | None) -> float:
    """Open-Meteo wind_speed_10m_max è in km/h → m/s."""
    return max(0.0, float(wind_kmh or 0.0) / 3.6)


def _canopy_wind_frac(leaf: str) -> float:
    """Frazione del vento a 10 m che arriva al sottobosco (rugosità chioma)."""
    return {"conifere": 0.28, "misto": 0.35, "latifoglie": 0.42}.get(leaf, 0.55)


def _desiccation(
    past: list[dict], leaf: str, since: int | None
) -> tuple[float, dict]:
    """
    Moltiplicatore su P da domanda evaporativa al suolo/sporocarpo.

    Basi:
    - Lilleskov et al. 2009 (New Phytol.): VPD predice water-loss rate degli sporocarpi;
      vento alza la conductance aerodinamica del boundary layer.
    - ET0 FAO-56 (già in Open-Meteo) porta il vento nel percorso *suolo* via _bucket —
      qui NON moltiplichiamo di nuovo ET0 (evita doppio conteggio).
    - Karavani / Ogaya: umidità suolo + domanda evaporativa > pioggia grezza.

    Finestra: peso massimo quando i primordia/carpofori sono esposti (5–16 gg post-innesco).
    """
    meta = {"vpd": None, "wind_ms": None, "evap_demand": None, "desiccation": 1.0}
    recent = past[-3:] if len(past) >= 3 else past
    if not recent:
        return 1.0, meta

    shelter = _canopy_wind_frac(leaf)
    vpds: list[float] = []
    us: list[float] = []
    for d in recent:
        v = _vpd_kpa(d.get("tmean"), d.get("rh"))
        if v is not None:
            vpds.append(v)
        # vento sotto chioma ≈ frazione del max giornaliero a 10 m
        us.append(_wind_ms(d.get("wind")) * shelter)

    if not vpds:
        return 1.0, meta

    vpd = sum(vpds) / len(vpds)
    u = sum(us) / len(us)
    # Conductance aerodinamica normalizzata: g_a ∝ u/(u+u0), u0 tipico understory
    aero = u / (u + 1.4)
    # Domanda evaporativa effettiva (kPa-eq): VPD amplificato dal rimescolamento
    # (0.50 vs quiete → fino a ~1.45× con vento forte sottobosco)
    demand = vpd * (0.50 + 0.95 * aero)

    # Fattore continuo: ~1 a domanda bassa/media autunnale; cala sopra ~1.2–1.4 kPa-eq
    # Forma: 1 / (1 + (D/D0)^n) — monotona, calibrata su range VPD campo
    d0 = 1.30
    factor = 1.0 / (1.0 + (demand / d0) ** 2.3)
    factor = max(0.42, min(1.0, factor))

    # Peso fisiologico: quanto i carpofori/primordia sono esposti all'aria
    if since is None:
        weight = 0.30
    elif since <= 4:
        weight = 0.60  # lettiera bagnata: vento ri-asciuga film superficiale
    elif since <= 16:
        weight = 1.00  # finestra fruizione
    else:
        weight = 0.35

    blended = 1.0 - weight * (1.0 - factor)
    meta = {
        "vpd": round(vpd, 2),
        "wind_ms": round(u, 2),
        "evap_demand": round(demand, 2),
        "desiccation": round(blended, 2),
    }
    return blended, meta


def _shock(days: list[dict]) -> bool:
    if len(days) < 14:
        return False
    window = days[-14:-7]
    after = days[-7:]
    best = 0.0
    for i in range(len(window) - 1):
        best = max(best, (window[i].get("precip") or 0) + (window[i + 1].get("precip") or 0))
    t_before = _mean([d.get("tmean") for d in window])
    t_after = _mean([d.get("tmean") for d in after])
    if t_before is None or t_after is None:
        return False
    return best >= 15 and (t_before - t_after) >= 3


def _bucket(days: list[dict], slope: float, twi: float, rad: float) -> tuple[float, float]:
    """
    Bilancio idrico lettiera/suolo superficiale → (swc, abbondanza).

    ET0 Open-Meteo = FAO-56 Penman–Monteith: include già vento, Rad, T, RH.
    Non moltiplicare ET0 per un secondo fattore vento (doppio conteggio).
    """
    cap = min(0.42, 0.20 + 0.04 * max(0.0, twi))
    swc = min(cap, 0.22)
    good = 0.0
    used = days[-26:] if len(days) > 26 else days
    if not used:
        return swc, 0.0
    for d in used:
        rain = (d.get("precip") or 0) / 1000.0
        # et0 già wind-aware (FAO-56); rad = esposizione versante
        et = (d.get("et0") or 0) / 1000.0 * rad
        runoff = rain * min(0.65, max(0.0, slope) / 40.0)
        swc = swc + rain - et - runoff
        swc = max(0.04, min(cap, swc))
        model = d.get("smoist")
        blend = swc if model is None else 0.5 * swc + 0.5 * min(cap, max(0.04, model))
        good += math.exp(-0.5 * ((blend - 0.24) / 0.05) ** 2)
    return swc, 100.0 * good / len(used)


def _incubation(days: list[dict]) -> tuple[int | None, float]:
    """Giorni dall'ultima pioggia innescante (>=20 mm in 3 giorni) e peso della finestra 8-15 gg."""
    daily = [(d.get("precip") or 0) for d in days]
    last = None
    for i in range(len(daily) - 1, -1, -1):
        block = sum(daily[max(0, i - 2) : i + 1])
        if block >= 20:
            last = len(daily) - 1 - i
            break
    if last is None:
        return None, 0.0
    if last < 5:
        return last, 0.35
    if last <= 18:
        return last, math.exp(-0.5 * ((last - 11) / 4.0) ** 2)
    return last, 0.15


def radiation_factor(aspect: float, slope: float) -> float:
    """1 = piano. Versante sud pende di più, nord di meno. aspect: 0 nord, 180 sud."""
    south = math.cos(math.radians(aspect))
    # cos(0)=1 è nord se aspect 0. Vogliamo sud = più ET.
    southness = -south  # aspect 180 → southness 1
    tilt = math.sin(math.radians(min(slope, 45)))
    return max(0.65, min(1.35, 1 + 0.25 * southness * tilt))


def _stage(hab: float, today: dict, best: dict) -> int:
    """1 idonea ferma, 2 incubazione, 3 avvio, 4 buona, 5 eccezionale. 0 = habitat chiuso."""
    if hab <= 0:
        return 0
    p = today.get("p") or 0
    abund = today.get("abundance") or 0
    since = today.get("since_rain")
    ahead = (best.get("p") or 0) >= P_ASPETTA and (best.get("p") or 0) > p + 0.08 and best.get("date") != today.get("date")
    if p >= 0.62 and abund >= 65:
        return 5
    if p >= P_VAI and abund >= A_VAI:
        return 4
    if p >= 0.38 and abund >= 15:
        return 3
    if since is not None and since <= 18:
        return 2
    if ahead:
        return 2
    return 1


def score_series(
    days: list[dict],
    *,
    species: str,
    leaf: str,
    elev: float,
    slope: float,
    aspect: float,
    twi: float,
    month: int,
    today_index: int,
    label: str | None = None,
) -> dict:
    """days ordinati nel tempo. today_index è l'ultimo giorno osservato."""
    hab = habitat_factor(species, leaf, elev, month)
    host, host_hit = host_factor(species, label)
    hab *= host
    spec = SPECIES[species]
    rad = radiation_factor(aspect, slope)
    out_days = []
    incomplete = False
    last = min(len(days) - 1, today_index + 14)
    start = today_index
    for idx in range(start, last + 1):
        past = days[: idx + 1]
        t_window = past[-20:] if len(past) >= 5 else past
        temps = []
        for d in t_window:
            soil = d.get("tsoil")
            air = d.get("tmean")
            temps.append(soil if soil is not None else air)
            if soil is None:
                incomplete = True
        tmean = _mean(temps)
        rain26 = sum((d.get("precip") or 0) for d in past[-26:])
        rain_term = min(1.0, rain26 / 80.0)
        shock = _shock(past)
        therm = _therm(tmean, spec["topt"])
        since, incub = _incubation(past)
        rh = _mean([d.get("rh") for d in past[-5:]])
        rh_term = 1.0 if rh is None else max(0.75, min(1.1, 0.55 + rh / 150.0))
        dry, dry_meta = _desiccation(past, leaf, since)
        p = hab * therm * rh_term * dry * (
            0.25 + 0.25 * rain_term + 0.35 * incub + (0.15 if shock else 0.0)
        )
        p = min(1.0, p)
        swc, abund = _bucket(past, slope, twi, rad)
        # Carpofori esposti: stessa domanda evaporativa taglia anche abbondanza utile
        abund *= 0.55 + 0.45 * dry
        if p < 0.35:
            abund = 0.0
        out_days.append(
            {
                "date": past[-1].get("date"),
                "p": round(p, 3),
                "abundance": round(abund, 1),
                "t": None if tmean is None else round(tmean, 1),
                "shock": shock,
                "swc": round(swc, 3),
                "rain26": round(rain26, 1),
                "since_rain": since,
                "incub": round(incub, 2),
                "vpd": dry_meta.get("vpd"),
                "wind_ms": dry_meta.get("wind_ms"),
                "evap_demand": dry_meta.get("evap_demand"),
                "desiccation": dry_meta.get("desiccation"),
            }
        )
    if not out_days:
        return {"decision": "prudenza", "days": [], "incomplete": True}
    today = out_days[0]
    best = max(out_days, key=lambda d: d["p"])
    if hab == 0 or today["t"] is None:
        decision = "lascia" if hab == 0 else "prudenza"
    elif today["p"] >= P_VAI and today["abundance"] >= A_VAI:
        decision = "vai"
    elif best["p"] >= P_ASPETTA and best["p"] > today["p"] + 0.08:
        decision = "aspetta"
    else:
        decision = "lascia"
    if incomplete and decision == "vai":
        decision = "prudenza"
    stage = _stage(hab, today, best)
    return {
        "decision": decision,
        "stage": stage,
        "host": round(host, 2),
        "host_hit": host_hit,
        "today": today,
        "best": best,
        "days": out_days,
        "habitat": round(hab, 2),
        "radiation": round(rad, 2),
        "incomplete": incomplete,
        "species": spec["name"],
    }


def month_of(iso: str | None, fallback: date | None = None) -> int:
    if iso:
        try:
            return int(iso[5:7])
        except ValueError:
            pass
    return (fallback or date.today()).month
