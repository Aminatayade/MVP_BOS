"""
Fonctions pures d'agrégation pour le cockpit "Compréhension du taux d'usage
des espaces" -- séparées de api_server.py (même découpage domain/application
que le reste du pipeline) pour rester testables indépendamment d'ArcadeDB.

Toutes prennent des lignes brutes `{"ts": "YYYY-MM-DD HH:MM:SS.ffffff",
"value": float, "global_id": str}` telles que renvoyées par
`SELECT ts, value, global_id FROM EquipmentReading` (même format que
`_bucket_and_aggregate` dans api_server.py).

Règle "occupé" : `value >= OCCUPIED_MIN_COUNT`. Avec les paramètres actuels
du catalogue (base=0.0, peak=6.0 pour l'occupation ; capacity=8.0 pour la
réservation), le bruit laisse un intervalle non-occupé dans [-0.6, 0.6] et
occupé dans [5.4, 6.6]/[8.0] -- séparation nette à 1.0. C'est un seuil DE
DÉTECTION (distingue une salle vide d'une salle occupée), différent du
"seuil d'occupation" (`seuil`, un pourcentage) utilisé par
`classify_threshold_counts` pour classer les salles entre elles.
"""

from __future__ import annotations

from datetime import datetime

OCCUPIED_MIN_COUNT = 1.0

PLAGE_HOURS = {
    "7-19": (7, 19),
    "7-12": (7, 12),
    "12-19": (12, 19),
}


def parse_ts(ts_raw: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S.%f")
    except (ValueError, TypeError):
        return None


def filter_by_plage(rows: list[dict], plage: str) -> list[dict]:
    start_h, end_h = PLAGE_HOURS.get(plage, PLAGE_HOURS["7-19"])
    filtered = []
    for row in rows:
        dt = parse_ts(row.get("ts"))
        if dt is None:
            continue
        if start_h <= dt.hour < end_h:
            filtered.append(row)
    return filtered


def bucket_daily_occupancy_pct(rows: list[dict], *, occupied_min: float = OCCUPIED_MIN_COUNT) -> list[dict]:
    """Un point par jour calendaire (UTC, comme le reste du pipeline) : %
    d'échantillons occupés ce jour-là, tous capteurs du périmètre confondus."""
    buckets: dict[str, list[float]] = {}
    for row in rows:
        dt = parse_ts(row.get("ts"))
        value = row.get("value")
        if dt is None or value is None:
            continue
        day_label = dt.strftime("%Y-%m-%d")
        buckets.setdefault(day_label, []).append(float(value))

    points = []
    for day_label in sorted(buckets):
        values = buckets[day_label]
        occupied = sum(1 for v in values if v >= occupied_min)
        pct = round(100 * occupied / len(values), 1) if values else 0.0
        points.append({"label": day_label, "count": pct})
    return points


def compute_donut(rows: list[dict], *, occupied_min: float = OCCUPIED_MIN_COUNT) -> dict:
    total = len(rows)
    occupied = sum(1 for row in rows if row.get("value") is not None and float(row["value"]) >= occupied_min)
    free = total - occupied
    occupied_pct = round(100 * occupied / total, 1) if total else 0.0
    return {"occupied_pct": occupied_pct, "occupied_count": occupied, "free_count": free}


def compute_reservation_rate(
    occupancy_rows: list[dict], reservation_rows: list[dict], *, occupied_min: float = OCCUPIED_MIN_COUNT
) -> dict:
    """Taux de réservation = part des créneaux DÉCLARÉS réservés qui ont
    effectivement été mesurés occupés -- détecte le no-show. Les deux
    métriques partagent la même grille de temps par capteur (générées dans
    le même appel `backfill_for_equipment`, même `now`/`points_per_day`),
    donc l'appariement par (global_id, ts) est exact, pas une approximation
    par agrégats indépendants."""
    reserved_slots = {
        (row.get("global_id"), row.get("ts"))
        for row in reservation_rows
        if row.get("value") is not None and float(row["value"]) >= occupied_min
    }
    if not reserved_slots:
        return {"pct": None}

    occupied_and_reserved = sum(
        1
        for row in occupancy_rows
        if row.get("value") is not None
        and float(row["value"]) >= occupied_min
        and (row.get("global_id"), row.get("ts")) in reserved_slots
    )
    return {"pct": round(100 * occupied_and_reserved / len(reserved_slots), 1)}


def per_sensor_occupancy_pct(rows: list[dict], *, occupied_min: float = OCCUPIED_MIN_COUNT) -> dict[str, float]:
    by_sensor: dict[str, list[float]] = {}
    for row in rows:
        gid, value = row.get("global_id"), row.get("value")
        if gid is None or value is None:
            continue
        by_sensor.setdefault(gid, []).append(float(value))
    return {
        gid: round(100 * sum(1 for v in values if v >= occupied_min) / len(values), 1)
        for gid, values in by_sensor.items()
        if values
    }


def classify_threshold_counts(
    occupancy_pct_by_sensor: dict[str, float],
    reservation_pct_by_sensor: dict[str, float],
    *,
    seuil_bas: float,
    seuil_haut: float,
) -> dict:
    """Deux bornes, pas une seule valeur de coupure : "< seuil_bas" signale
    un espace SOUS-utilisé, "> seuil_haut" un espace SATURÉ -- confirmé avec
    le client (ex. 20%/80%). L'entre-deux (]seuil_bas, seuil_haut]) n'est
    compté nulle part, ce n'est ni un problème de sous-utilisation ni de
    saturation."""
    occ_values = list(occupancy_pct_by_sensor.values())
    res_values = list(reservation_pct_by_sensor.values())
    return {
        "seuil_bas": seuil_bas,
        "seuil_haut": seuil_haut,
        "occupancy_below": sum(1 for v in occ_values if v < seuil_bas),
        "occupancy_above": sum(1 for v in occ_values if v > seuil_haut),
        "reservation_below": sum(1 for v in res_values if v < seuil_bas),
        "reservation_above": sum(1 for v in res_values if v > seuil_haut),
    }
