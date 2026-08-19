"""
Générateur de données de démo pour le canal time-series.

Ne génère JAMAIS de global_id inventé : la liste passée en argument doit
provenir d'une requête réelle contre les Equipment déjà ingérés dans
ArcadeDB (voir publish_demo_stream.py). Produit des UnifiedMessage — des
objets du domaine, pas des dicts JSON — la sérialisation reste la
responsabilité de l'appelant (le script qui publie sur NATS).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from domain.entities import UnifiedMessage

METRIC_PROFILES: dict[str, dict[str, float | str]] = {
    "temperature": {"unit": "°C", "base": 21.0, "spread": 3.0},
    "co2": {"unit": "ppm", "base": 500.0, "spread": 150.0},
    "occupancy": {"unit": "count", "base": 3.0, "spread": 3.0},
}


def generate_demo_messages(
    global_ids: list[str],
    *,
    metrics: list[str] | None = None,
    days: int = 3,
    points_per_day: int = 24,
    source: str = "demo-generator",
    now: datetime | None = None,
) -> list[UnifiedMessage]:
    """Génère `days * points_per_day` points par (global_id, metric),
    étalés régulièrement sur les `days` derniers jours jusqu'à `now`."""
    if not global_ids:
        raise ValueError("global_ids must not be empty")

    metrics = metrics or list(METRIC_PROFILES)
    reference = now or datetime.now(timezone.utc)
    total_points = days * points_per_day
    interval = timedelta(days=days) / total_points
    start = reference - timedelta(days=days)

    messages: list[UnifiedMessage] = []
    for global_id in global_ids:
        for metric in metrics:
            profile = METRIC_PROFILES.get(metric, {"unit": "", "base": 0.0, "spread": 1.0})
            base = float(profile["base"])
            spread = float(profile["spread"])
            unit = str(profile["unit"])

            for i in range(total_points):
                timestamp = start + interval * i
                value = round(base + random.uniform(-spread, spread), 2)
                quality = "good" if random.random() > 0.02 else "uncertain"
                messages.append(
                    UnifiedMessage(
                        global_id=global_id,
                        timestamp=timestamp,
                        metric=metric,
                        value=value,
                        unit=unit,
                        quality=quality,
                        source=source,
                    )
                )

    return messages
