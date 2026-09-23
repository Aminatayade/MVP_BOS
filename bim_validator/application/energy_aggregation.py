"""
Fonctions pures d'agrégation pour le cockpit fixe "Consommation
énergétique" (BOS Exécution) -- même découpage domain/application que
occupancy_aggregation.py (voir ce module), séparées de api_server.py pour
rester testables sans ArcadeDB.

Toutes prennent des lignes brutes `{"ts": "YYYY-MM-DD HH:MM:SS.ffffff",
"value": float, "global_id": str}` telles que renvoyées par
`SELECT ts, value, global_id FROM EquipmentReading` -- même contrat que
occupancy_aggregation.py, pour réutiliser le même code de fetch SQL.

Différence volontaire avec l'occupation : la consommation énergétique d'un
bâtiment tertiaire ne s'arrête PAS le week-end (CVC de maintien hors gel,
IT 24/7...) -- filter_by_plage ici ne filtre JAMAIS par jour de semaine,
seulement par heure de la journée (contrairement à
occupancy_aggregation.filter_by_plage, qui coupe implicitement les
week-ends via l'occupation mesurée).
"""

from __future__ import annotations

from datetime import datetime

# "ouvrable"/"hors_ouvrable" : bornes convenues avec le client (8h-19h),
# INDÉPENDANTES des plages 7-19/7-12/12-19 d'occupancy_aggregation -- deux
# cockpits, deux conventions horaires, jamais partagées entre elles.
PLAGE_HOURS = {"ouvrable": (8, 19)}

# Nombre d'heures couvertes PAR JOUR par chaque plage -- sert à convertir une
# puissance moyenne filtrée (kW) en énergie (kWh) : kWh = kW_moyen * heures
# de la plage, jamais * 24 sinon "période ouvrable" (11h/jour réelles)
# surestimerait l'énergie d'un facteur 24/11.
PLAGE_WINDOW_HOURS = {"toute_journee": 24, "ouvrable": 11, "hors_ouvrable": 13}


def parse_ts(ts_raw: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S.%f")
    except (ValueError, TypeError):
        return None


def _in_plage_hour(hour: int, plage: str) -> bool:
    # "hors_ouvrable" n'a pas sa propre entrée dans PLAGE_HOURS : c'est le
    # COMPLÉMENT de "ouvrable" (mêmes bornes 8h-19h, inversées), jamais une
    # plage indépendante qui pourrait diverger de "ouvrable" si les horaires
    # changent un jour.
    lookup_key = "ouvrable" if plage == "hors_ouvrable" else plage
    if lookup_key not in PLAGE_HOURS:  # "toute_journee" ou valeur inconnue -> tout garder
        return True
    start_h, end_h = PLAGE_HOURS[lookup_key]
    inside = start_h <= hour < end_h
    return not inside if plage == "hors_ouvrable" else inside


def filter_by_plage(rows: list[dict], plage: str) -> list[dict]:
    """Filtre par HEURE de la journée seulement (voir note de module).
    'hors_ouvrable' réutilise les bornes de 'ouvrable' mais inversées
    (complément) -- une seule source de vérité pour "8h-19h", jamais deux
    plages indépendantes qui pourraient diverger."""
    filtered = []
    for row in rows:
        dt = parse_ts(row.get("ts"))
        if dt is None:
            continue
        if _in_plage_hour(dt.hour, plage):
            filtered.append(row)
    return filtered


def window_duration_hours(plage: str) -> int:
    """Nombre d'heures/jour couvertes par cette plage -- voir
    PLAGE_WINDOW_HOURS. Une plage inconnue retombe sur 24h (comportement de
    'toute_journee'), jamais une exception pour un paramètre de requête
    invalide."""
    return PLAGE_WINDOW_HOURS.get(plage, 24)


def bucket_daily_by_subcategory(
    rows: list[dict], subcategory_by_gid: dict[str, str], *, plage: str
) -> dict[str, dict[str, float]]:
    """Un point par jour calendaire (UTC) et par sous-catégorie :
    kWh = moyenne des échantillons power_instant_kw de ce jour/cette
    sous-catégorie * window_duration_hours(plage) -- PAS une somme brute des
    échantillons (une puissance instantanée n'est pas un compteur cumulatif,
    même distinction que _aggregation_for_metric dans api_server.py). Un
    global_id absent de subcategory_by_gid (métrique orpheline,
    incohérence graphe/timeseries) est ignoré, jamais une KeyError.

    Retourne {jour_iso: {sous_categorie: kwh}} -- une sous-catégorie sans
    aucun échantillon un jour donné est simplement absente de ce jour ;
    zéro-remplissage laissé à densify_daily_subcategories."""
    duration_hours = window_duration_hours(plage)
    raw: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        subcat = subcategory_by_gid.get(row.get("global_id"))
        dt = parse_ts(row.get("ts"))
        value = row.get("value")
        if subcat is None or dt is None or value is None:
            continue
        day_label = dt.strftime("%Y-%m-%d")
        raw.setdefault(day_label, {}).setdefault(subcat, []).append(float(value))

    result: dict[str, dict[str, float]] = {}
    for day_label, by_subcat in raw.items():
        result[day_label] = {
            subcat: round((sum(values) / len(values)) * duration_hours, 2)
            for subcat, values in by_subcat.items()
        }
    return result


def densify_daily_subcategories(
    daily: dict[str, dict[str, float]], subcategories: list[str], day_labels: list[str]
) -> dict[str, dict[str, float]]:
    """Complète `daily` (voir bucket_daily_by_subcategory) avec 0.0 pour
    toute (jour, sous-catégorie) absente -- une grille dense est nécessaire
    pour un graphique en barres empilées Chart.js (un dataset par
    sous-catégorie doit avoir EXACTEMENT une valeur par jour affiché, sinon
    les barres se désalignent silencieusement)."""
    return {
        day: {subcat: daily.get(day, {}).get(subcat, 0.0) for subcat in subcategories}
        for day in day_labels
    }


def month_total_by_subcategory(daily: dict[str, dict[str, float]]) -> dict[str, float]:
    """Effondre une grille jour x sous-catégorie (voir
    bucket_daily_by_subcategory) en un seul total par sous-catégorie, pour
    les KPI "Mois actuel"/"Mois n-1" -- une simple somme, l'énergie (kWh)
    est additive contrairement à une puissance instantanée."""
    totals: dict[str, float] = {}
    for by_subcat in daily.values():
        for subcat, kwh in by_subcat.items():
            totals[subcat] = round(totals.get(subcat, 0.0) + kwh, 2)
    return totals
