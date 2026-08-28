"""
Générateurs de séries simulées pour le catalogue de simulation par type
d'équipement (equipment_simulation_catalog.json). Chaque fonction est PURE
et seedée via un `random.Random` passé en argument (jamais le module
`random` global) -- indispensable pour rester déterministe/testable et pour
ne pas polluer un état aléatoire partagé entre ingestions concurrentes
(voir EquipmentSimulationService, appelé depuis un pool de threads).

Une forme de générateur par famille de comportement physique, plutôt qu'un
seul modèle générique à base+bruit : un compteur cumulatif ne peut pas
redescendre, un capteur corrélé à l'occupation ne peut pas être plat, etc.
Mélanger ces formes dans un seul générateur "bruit autour d'une base"
produirait des courbes physiquement incohérentes.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from datetime import datetime, timedelta

from domain.entities import MetricSpec, UnifiedMessage


def _iter_timestamps(
    history_days: int, points_per_day: int, now: datetime
) -> Iterator[tuple[int, datetime]]:
    total_points = max(history_days * points_per_day, 1)
    interval = timedelta(days=history_days) / total_points
    start = now - timedelta(days=history_days)
    for i in range(total_points):
        yield i, start + interval * i


def _quality(rng: random.Random) -> str:
    return "good" if rng.random() > 0.02 else "uncertain"


def _is_occupied(ts: datetime, occupied_hours: list[int]) -> bool:
    if ts.weekday() >= 5:  # samedi/dimanche : jamais occupé (bureau type)
        return False
    start_h, end_h = occupied_hours
    return start_h <= ts.hour < end_h


def day_night_cycle(
    *, global_id: str, metric: MetricSpec, history_days: int, points_per_day: int,
    now: datetime, rng: random.Random, source: str,
) -> list[UnifiedMessage]:
    """Oscillation jour/nuit + bruit -- température, CVC en général. Pic en
    milieu d'après-midi, creux au petit matin."""
    base = float(metric.params.get("base", 20.0))
    amplitude = float(metric.params.get("amplitude", 2.0))
    noise = float(metric.params.get("noise", 0.5))

    messages = []
    for _, ts in _iter_timestamps(history_days, points_per_day, now):
        hour_fraction = (ts.hour + ts.minute / 60.0 - 6) / 24.0
        value = base + amplitude * math.sin(2 * math.pi * hour_fraction) + rng.uniform(-noise, noise)
        messages.append(UnifiedMessage(
            global_id=global_id, timestamp=ts, metric=metric.name,
            value=round(value, 2), unit=metric.unit, quality=_quality(rng), source=source,
        ))
    return messages


def occupancy_correlated(
    *, global_id: str, metric: MetricSpec, history_days: int, points_per_day: int,
    now: datetime, rng: random.Random, source: str,
) -> list[UnifiedMessage]:
    """Corrélé à l'occupation type bureau : proche de `peak` en semaine sur
    les heures ouvrées, proche de `base` sinon (nuit, week-end). Utilisé pour
    CO2, occupation, éclairage."""
    base = float(metric.params.get("base", 0.0))
    peak = float(metric.params.get("peak", 1.0))
    occupied_hours = metric.params.get("occupied_hours", [9, 18])
    spread = abs(peak - base) * 0.1

    messages = []
    for _, ts in _iter_timestamps(history_days, points_per_day, now):
        target = peak if _is_occupied(ts, occupied_hours) else base
        value = target + rng.uniform(-spread, spread)
        messages.append(UnifiedMessage(
            global_id=global_id, timestamp=ts, metric=metric.name,
            value=round(value, 2), unit=metric.unit, quality=_quality(rng), source=source,
        ))
    return messages


def cumulative_daily_profile(
    *, global_id: str, metric: MetricSpec, history_days: int, points_per_day: int,
    now: datetime, rng: random.Random, source: str,
) -> list[UnifiedMessage]:
    """Série CROISSANTE (compteurs kWh/m³/...) : chaque point ajoute un
    incrément >= 0 au cumul précédent, jamais de retour en arrière.
    L'incrément est pondéré (plus élevé pendant `peak_hours`) mais reste une
    approximation simple -- pas une simulation de charge réelle."""
    daily_growth = float(metric.params.get("daily_growth", 10.0))
    peak_hours = metric.params.get("peak_hours", [8, 19])

    total_points = max(history_days * points_per_day, 1)
    per_point_base = daily_growth / points_per_day

    messages = []
    total = 0.0
    for _, ts in _iter_timestamps(history_days, points_per_day, now):
        is_peak = peak_hours[0] <= ts.hour < peak_hours[1]
        multiplier = 1.4 if is_peak else 0.6
        increment = max(per_point_base * multiplier * rng.uniform(0.85, 1.15), 0.0)
        total += increment
        messages.append(UnifiedMessage(
            global_id=global_id, timestamp=ts, metric=metric.name,
            value=round(total, 3), unit=metric.unit, quality=_quality(rng), source=source,
        ))
    return messages


def sparse_event_log(
    *, global_id: str, metric: MetricSpec, history_days: int, points_per_day: int,
    now: datetime, rng: random.Random, source: str,
) -> list[UnifiedMessage]:
    """État quasi-constant (`default_value`) avec de rares transitions vers
    `event_value` -- alarmes, disjoncteurs, automates GTB. Probabilité par
    point calibrée pour obtenir en moyenne `events_per_week` événements sur
    toute la fenêtre d'historique."""
    default_value = float(metric.params.get("default_value", 0.0))
    event_value = float(metric.params.get("event_value", 1.0))
    events_per_week = float(metric.params.get("events_per_week", 1.0))

    total_points = max(history_days * points_per_day, 1)
    expected_events = events_per_week * (history_days / 7.0)
    p_event = min(expected_events / total_points, 1.0)

    messages = []
    for _, ts in _iter_timestamps(history_days, points_per_day, now):
        value = event_value if rng.random() < p_event else default_value
        messages.append(UnifiedMessage(
            global_id=global_id, timestamp=ts, metric=metric.name,
            value=value, unit=metric.unit, quality="good", source=source,
        ))
    return messages


GENERATORS = {
    "day_night_cycle": day_night_cycle,
    "occupancy_correlated": occupancy_correlated,
    "cumulative_daily_profile": cumulative_daily_profile,
    "sparse_event_log": sparse_event_log,
}


def generate_metric_history(
    *, global_id: str, metric: MetricSpec, history_days: int, points_per_day: int,
    now: datetime, rng: random.Random, source: str = "ingestion-simulator",
) -> list[UnifiedMessage]:
    """Point d'entrée unique : résout le générateur par son nom (catalogue
    JSON) et lui délègue la génération. Un nom de générateur inconnu ne
    lève pas d'exception -- retourne une liste vide plutôt que de faire
    échouer tout le backfill d'un équipement pour une métrique mal
    configurée dans le catalogue."""
    generator = GENERATORS.get(metric.generator)
    if generator is None:
        return []
    return generator(
        global_id=global_id, metric=metric, history_days=history_days,
        points_per_day=points_per_day, now=now, rng=rng, source=source,
    )
