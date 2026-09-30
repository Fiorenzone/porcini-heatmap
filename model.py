"""Indice porcini: P(buttata) e abbondanza. Parametri dai paper, non un voto unico.

Lo stadio e la decisione di *oggi* sono nowcast: solo giorni già osservati.
D+1 e D+2 restano in serie come tendenza (slider): non promuovono il colore
di oggi. Pioggia, shock e danno da vento secco sono integratori leaky.
"""

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
HORIZON_DAYS = 3

# Memorie (giorni). Con ~26 gg di storico il bordo della serie pesa già e^(-26/τ).
_TAU_RAIN = 12.0
_TAU_DAMAGE = 6.0
_TAU_SHOCK = 6.0
_TAU_ABUND = 10.0


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


# Classe carta suoli: 0 ignota, 1 acida, 2 subacida, 3 neutra, 4 subalcalina/calcare, 5 alcalina.
# Edulis e pinophilus acidofili: il calcare puro taglia, non azzera (carta 1:50k sbaglia i versanti misti).
_SOIL_F = {
    "edulis": (1.0, 1.0, 1.0, 0.85, 0.35, 0.15),
    "pinophilus": (1.0, 1.0, 1.0, 0.80, 0.30, 0.12),
    "aestivalis": (1.0, 1.0, 1.0, 1.0, 0.55, 0.30),
    "aereus": (1.0, 1.0, 1.0, 1.0, 0.75, 0.45),
}


# DUSAF: 0 assente, 1 denso/fustaia, 2 ceduo denso, 3 rado, 4 castagneto, 5 ripariale, 6 neoformazione.
_COVER_F = (1.0, 1.0, 0.90, 0.70, 0.85, 0.40, 0.35)


def cover_factor(cover: int) -> float:
    if cover <= 0 or cover >= len(_COVER_F):
        return 1.0
    return _COVER_F[cover]


def soil_factor(species: str, soil: int) -> float:
    scale = _SOIL_F.get(species)
    if not scale or soil <= 0 or soil >= len(scale):
        return 1.0
    return scale[soil]


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


def _smoothstep(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


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


def _dry_sector(deg: float | None) -> bool | None:
    """Tramontana, grecale, favonio: 300°–60° passando per il nord."""
    if deg is None:
        return None
    d = float(deg) % 360.0
    return d >= 300.0 or d <= 60.0


def _triggers(daily: list[float]) -> list[tuple[int, float]]:
    """Innesco continuo: 0 sotto 10 mm in 3 giorni, saturo da ~22 mm. Niente soglia a 20."""
    out = []
    for i in range(len(daily)):
        block = sum(daily[max(0, i - 2) : i + 1])
        strength = _smoothstep((block - 10.0) / 12.0)
        if strength > 0.02:
            out.append((i, strength))
    return out


def _kernel(age: int, peak: float) -> float:
    return math.exp(-0.5 * ((age - peak) / 4.0) ** 2)


def _best_flush(
    triggers: list[tuple[int, float]], day_index: int, peak: float
) -> tuple[int | None, float]:
    """Max dei kernel: una pioggia nuova non azzera una buttata già in corso."""
    best_k = 0.0
    best_age: int | None = None
    for i, strength in triggers:
        if i > day_index:
            break
        age = day_index - i
        k = strength * _kernel(age, peak)
        if k > best_k:
            best_k = k
            best_age = age
    return best_age, best_k


def _rain_term(days: list[dict]) -> float:
    """
    Somma leaky della pioggia (mm), τ = 12 gg.
    La scala eguaglia il livello stazionario della vecchia somma rigida 26 gg / 80 mm,
    così un autunno stabilmente umido non cambia P: cambia solo il bordo.
    """
    lam = math.exp(-1.0 / _TAU_RAIN)
    tau_eff = 1.0 / (1.0 - lam)
    scale = 80.0 * tau_eff / 26.0
    acc = 0.0
    for d in days:
        acc = lam * acc + float(d.get("precip") or 0.0)
    return min(1.0, acc / scale)


def _desiccation(
    past: list[dict], leaf: str, aspect: float, slope: float
) -> tuple[float, dict]:
    """
    Moltiplicatore su P da domanda evaporativa al suolo/sporocarpo.

    Basi:
    - Lilleskov et al. 2009 (New Phytol.): VPD predice water-loss rate degli sporocarpi;
      vento alza la conductance aerodinamica del boundary layer.
    - ET0 FAO-56 (già in Open-Meteo) porta il vento nel percorso *suolo* via _bucket —
      qui NON moltiplichiamo di nuovo ET0 (evita doppio conteggio).
    - Karavani / Ogaya: umidità suolo + domanda evaporativa > pioggia grezza.

    Lo stress entra in un integratore leaky (τ = 6 gg). Un giorno di tramontana
    sposta il danno di poco; una settimana lo porta verso il pavimento 0.35.
    Il recupero ha la stessa memoria. Niente soglia su settore, vento o giorno 16.
    L'esposizione segue il kernel di incubazione (primordi fuori), non uno scalino.
    """
    meta = {"vpd": None, "wind_ms": None, "evap_demand": None, "desiccation": 1.0, "dry_wind": None}
    if not past:
        return 1.0, meta

    shelter = _canopy_wind_frac(leaf)
    peak = _incub_peak(aspect, slope)
    triggers = _triggers([(d.get("precip") or 0) for d in past])
    lam = math.exp(-1.0 / _TAU_DAMAGE)
    damage = 0.0
    vpd_e = 0.0
    wind_e = 0.0
    dem_e = 0.0
    dry_e = 0.0
    have_vpd = False
    have_dry = False

    for i, d in enumerate(past):
        v = _vpd_kpa(d.get("tmean"), d.get("rh"))
        u = _wind_ms(d.get("wind")) * shelter
        gust_u = _wind_ms(d.get("gust")) * shelter
        wind_u = max(u, gust_u)
        flag = _dry_sector(d.get("wdir"))
        dry = 0.0 if flag is None else (1.0 if flag else 0.0)
        if flag is not None:
            have_dry = True
        if v is None:
            damage *= lam
            continue
        have_vpd = True
        aero = u / (u + 1.4)
        demand = v * (0.50 + 0.95 * aero)
        factor = 1.0 / (1.0 + (demand / 1.30) ** 2.3)
        factor = max(0.42, min(1.0, factor))
        wind_hit = dry * _smoothstep((wind_u - 0.8) / 3.2)
        _age, flush = _best_flush(triggers, i, peak)
        exposure = 0.25 + 0.75 * flush
        stress = exposure * (0.70 * (1.0 - factor) + 0.30 * wind_hit)
        damage = lam * damage + (1.0 - lam) * stress
        vpd_e = lam * vpd_e + (1.0 - lam) * v
        wind_e = lam * wind_e + (1.0 - lam) * u
        dem_e = lam * dem_e + (1.0 - lam) * demand
        dry_e = lam * dry_e + (1.0 - lam) * dry

    if not have_vpd:
        return 1.0, meta
    # Equilibrio di stress severo (~0.67) → pavimento fisiologico 0.35. Un giorno solo no.
    gain = min(1.0, damage / 0.73)
    mult = max(0.35, min(1.0, 1.0 - (1.0 - 0.35) * gain))
    meta = {
        "vpd": round(vpd_e, 2),
        "wind_ms": round(wind_e, 2),
        "evap_demand": round(dem_e, 2),
        "desiccation": round(mult, 2),
        "dry_wind": None if not have_dry else round(dry_e, 2),
    }
    return mult, meta


def _shock_level(days: list[dict]) -> float:
    """
    Impulso pioggia+raffreddamento, 0–1, decadimento τ = 6 gg.
    Soglie morbide (niente bool). Il giorno in cui l'evento esce da una
    scatola di 7 gg non spegne più il termine di colpo.
    """
    n = len(days)
    if n < 8:
        return 0.0
    best = 0.0
    for i in range(1, n):
        age = n - 1 - i
        if age < 3 or age > 28:
            continue
        rain2 = (days[i].get("precip") or 0) + (days[i - 1].get("precip") or 0)
        before = days[max(0, i - 7) : i]
        after = days[i + 1 : i + 8]
        if len(before) < 3 or len(after) < 3:
            continue
        tb = _mean([d.get("tmean") for d in before])
        ta = _mean([d.get("tmean") for d in after])
        if tb is None or ta is None:
            continue
        cool = _smoothstep(((tb - ta) - 1.5) / 3.5)
        wet = _smoothstep((rain2 - 8.0) / 17.0)
        level = cool * wet * math.exp(-age / _TAU_SHOCK)
        if level > best:
            best = level
    return best


def _bucket(days: list[dict], slope: float, twi: float, rad: float) -> tuple[float, float]:
    """
    Bilancio idrico lettiera/suolo superficiale → (swc, abbondanza).

    ET0 Open-Meteo = FAO-56 Penman–Monteith: include già vento, Rad, T, RH.
    Non moltiplicare ET0 per un secondo fattore vento (doppio conteggio).
    """
    cap = min(0.42, 0.20 + 0.04 * max(0.0, twi))
    swc = min(cap, 0.22)
    fits: list[float] = []
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
        fits.append(math.exp(-0.5 * ((blend - 0.24) / 0.05) ** 2))
    # Pesi esponenziali, più recenti più forti. A regime costante = media piatta di prima.
    # Il giorno che esce dai 26 gg pesa e^(-25/τ) e non sposta l'abbondanza di 1/26.
    lam = math.exp(-1.0 / _TAU_ABUND)
    w = 1.0
    wsum = 0.0
    fsum = 0.0
    for fit in reversed(fits):
        wsum += w
        fsum += w * fit
        w *= lam
    return swc, 100.0 * fsum / wsum


def _incub_peak(aspect: float, slope: float) -> float:
    """Nord ritarda, sud anticipa. Piano: picco al giorno 11, come prima."""
    northness = math.cos(math.radians(aspect))
    tilt = math.sin(math.radians(min(max(slope, 0.0), 45.0)))
    return 11.0 + 6.0 * northness * tilt


def _incubation(days: list[dict], aspect: float = 180.0, slope: float = 0.0) -> tuple[int | None, float]:
    """
    Età e ampiezza della buttata più viva. Gaussiana σ = 4 gg intorno al picco
    di versante. Più innesco convivono: si tiene il massimo, non si riazzera l'orologio.
    """
    daily = [(d.get("precip") or 0) for d in days]
    triggers = _triggers(daily)
    if not triggers:
        return None, 0.0
    peak = _incub_peak(aspect, slope)
    return _best_flush(triggers, len(daily) - 1, peak)


# Quanto un'estate fredda pesa in più sulle specie termofile.
_HEAT_BIAS = {"edulis": 0.0, "pinophilus": -1.0, "aestivalis": 0.8, "aereus": 1.2}


def heat_factor(species: str, anomaly: float | None) -> float:
    """Anomalia JJA del suolo 28–100 cm rispetto alle due estati precedenti. Assente → ×1."""
    if anomaly is None:
        return 1.0
    a = float(anomaly) - _HEAT_BIAS.get(species, 0.0)
    if a >= -0.8:
        return 1.0
    if a <= -3.0:
        return 0.45
    t = (a - (-3.0)) / ((-0.8) - (-3.0))
    return 0.45 + t * 0.55


def radiation_factor(aspect: float, slope: float) -> float:
    """1 = piano. Versante sud pende di più, nord di meno. aspect: 0 nord, 180 sud."""
    south = math.cos(math.radians(aspect))
    # cos(0)=1 è nord se aspect 0. Vogliamo sud = più ET.
    southness = -south  # aspect 180 → southness 1
    tilt = math.sin(math.radians(min(slope, 45)))
    return max(0.65, min(1.35, 1 + 0.25 * southness * tilt))


def _abund_gate(p: float) -> float:
    """Logistica: sotto ~0.32 la quantità sfuma. Niente azzeramento a 0.35."""
    return 1.0 / (1.0 + math.exp(-(p - 0.32) / 0.05))


def _stage(hab: float, today: dict) -> int:
    """1 idonea ferma, 2 incubazione, 3 avvio, 4 buona, 5 eccezionale. 0 = habitat chiuso.

    Stesso criterio su ogni giorno della serie. Il colore di *oggi* usa solo il
    giorno osservato; D+1/D+2 sono tendenza se il meteo di oggi tiene.
    """
    if hab <= 0:
        return 0
    p = today.get("p") or 0
    abund = today.get("abundance") or 0
    if p >= 0.62 and abund >= 65:
        return 5
    if p >= P_VAI and abund >= A_VAI:
        return 4
    if p >= 0.38 and abund >= 15:
        return 3
    if (today.get("incub") or 0) >= 0.25:
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
    soil: int = 0,
    cover: int = 0,
    heat: float | None = None,
) -> dict:
    """days ordinati nel tempo. today_index è l'ultimo giorno osservato."""
    hab = habitat_factor(species, leaf, elev, month)
    host, host_hit = host_factor(species, label)
    soil_class = int(soil or 0)
    soil_f = soil_factor(species, soil_class)
    cover_class = int(cover or 0)
    cover_f = cover_factor(cover_class)
    heat_f = heat_factor(species, heat)
    hab *= host * soil_f * cover_f * heat_f
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
        rain_term = _rain_term(past)
        shock = _shock_level(past)
        therm = _therm(tmean, spec["topt"])
        since, incub = _incubation(past, aspect, slope)
        rh = _mean([d.get("rh") for d in past[-5:]])
        rh_term = 1.0 if rh is None else max(0.75, min(1.1, 0.55 + rh / 150.0))
        dry, dry_meta = _desiccation(past, leaf, aspect, slope)
        p = hab * therm * rh_term * dry * (
            0.25 + 0.25 * rain_term + 0.35 * incub + 0.15 * shock
        )
        p = min(1.0, p)
        swc, abund = _bucket(past, slope, twi, rad)
        # Carpofori esposti: stessa domanda evaporativa taglia anche abbondanza utile
        abund *= 0.55 + 0.45 * dry
        abund *= _abund_gate(p)
        day_row = {
            "date": past[-1].get("date"),
            "p": round(p, 3),
            "abundance": round(abund, 1),
            "t": None if tmean is None else round(tmean, 1),
            "shock": round(shock, 2),
            "forecast": idx > today_index,
            "swc": round(swc, 3),
            "rain26": round(rain26, 1),
            "since_rain": since,
            "incub": round(incub, 2),
            "vpd": dry_meta.get("vpd"),
            "wind_ms": dry_meta.get("wind_ms"),
            "evap_demand": dry_meta.get("evap_demand"),
            "desiccation": dry_meta.get("desiccation"),
            "dry_wind": dry_meta.get("dry_wind"),
        }
        day_row["stage"] = _stage(hab, day_row)
        out_days.append(day_row)
    if not out_days:
        return {"decision": "prudenza", "days": [], "incomplete": True}
    today = out_days[0]
    # Picco nel finestra oggi…D+2. Non i 14 giorni GFS: troppo ballerini.
    window = out_days[:HORIZON_DAYS]
    best_day = max(
        window,
        key=lambda d: ((d.get("stage") or 0), (d.get("p") or 0), (d.get("abundance") or 0)),
    )
    later = best_day.get("date") != today.get("date")
    richer = (best_day.get("stage") or 0) > (today.get("stage") or 0) or (
        (best_day.get("p") or 0) > (today.get("p") or 0) + 0.04
    )
    best = dict(best_day) if later and richer else {}
    if hab == 0 or today["t"] is None:
        decision = "lascia" if hab == 0 else "prudenza"
    elif today["p"] >= P_VAI and today["abundance"] >= A_VAI:
        decision = "vai"
    elif (today.get("incub") or 0) >= 0.25:
        decision = "aspetta"
    else:
        decision = "lascia"
    if incomplete and decision == "vai":
        decision = "prudenza"
    stage = today.get("stage") or _stage(hab, today)
    return {
        "decision": decision,
        "stage": stage,
        "host": round(host, 2),
        "host_hit": host_hit,
        "today": today,
        "best": best,
        "horizon": [
            {
                "date": d.get("date"),
                "p": d.get("p"),
                "stage": d.get("stage") or 0,
                "forecast": bool(d.get("forecast")),
                "abundance": d.get("abundance"),
            }
            for d in out_days[:HORIZON_DAYS]
        ],
        "days": out_days,
        "habitat": round(hab, 2),
        "soil": soil_class,
        "soil_factor": round(soil_f, 2),
        "cover": cover_class,
        "cover_factor": round(cover_f, 2),
        "heat": None if heat is None else round(float(heat), 2),
        "heat_factor": round(heat_f, 2),
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
