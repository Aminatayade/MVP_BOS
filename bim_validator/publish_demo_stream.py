"""
Script utilitaire : publie un lot de mesures de démo sur NATS JetStream.

Récupère des VRAIS (Equipment.global_id, equipment_type) depuis ArcadeDB
(requête SQL directe, pas besoin de passer par un port pour ce script
utilitaire), génère l'historique selon le catalogue de simulation par type
d'équipement (equipment_simulation_catalog.json -- même catalogue que celui
utilisé automatiquement à l'ingestion, voir EquipmentSimulationService), puis
publie les points sur NATS.

Ce script ne touche JAMAIS ArcadeDB pour l'écriture des points : l'écriture
passe TOUJOURS par consume_timeseries.py. C'est le point qui justifie la
queue au lieu d'une écriture directe.

Usage :
    python publish_demo_stream.py
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import requests

from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from adapters.nats_adapter import NatsJetStreamAdapter
from application.simulation_generators import generate_metric_history

DEFAULT_SUBJECT = "bos.timeseries.demo"


def _build_catalog() -> JsonEquipmentCatalogAdapter:
    config_path = os.path.join(os.path.dirname(__file__), "config", "equipment_simulation_catalog.json")
    return JsonEquipmentCatalogAdapter(config_path)


def fetch_real_equipment(
    *, base_url: str, database: str, username: str, password: str, limit: int = 5
) -> list[tuple[str, str]]:
    """Retourne des (global_id, equipment_type) réels depuis ArcadeDB.
    `equipment_type` est renseigné par le classifieur à l'ingestion (voir
    BimGraphIngestionService) -- absent seulement pour un Equipment ingéré
    avant l'ajout de cette fonctionnalité."""
    endpoint = f"{base_url.rstrip('/')}/api/v1/command/{database}"
    payload = {
        "language": "sql",
        "command": (
            "SELECT global_id, equipment_type FROM Equipment "
            f"WHERE equipment_type IS NOT NULL LIMIT {int(limit)}"
        ),
    }
    response = requests.post(endpoint, json=payload, auth=(username, password), timeout=30)
    response.raise_for_status()
    rows = response.json().get("result", [])
    equipment = [
        (row["global_id"], row["equipment_type"])
        for row in rows
        if row.get("global_id") and row.get("equipment_type")
    ]
    if not equipment:
        raise RuntimeError(
            "Aucun Equipment avec equipment_type trouvé dans ArcadeDB. Ingérez d'abord une "
            "maquette IFC (le classifieur renseigne ce champ automatiquement à l'ingestion)."
        )
    return equipment


def main() -> None:
    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    database = os.environ.get("ARCADEDB_DATABASE", "bim")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
    nats_servers = os.environ.get("NATS_URL", "nats://localhost:4222")
    nats_user = os.environ.get("NATS_USER", "bos_admin")
    nats_password = os.environ.get("NATS_PASSWORD", "testBIM123")
    subject = os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)

    equipment = fetch_real_equipment(
        base_url=base_url, database=database, username=username, password=password
    )
    print(f"📦 {len(equipment)} Equipment réels récupérés depuis ArcadeDB : {equipment}")

    catalog = _build_catalog()
    rng = random.Random()
    now = datetime.now(timezone.utc)

    messages = []
    for global_id, equipment_type in equipment:
        try:
            spec = catalog.get_generator_spec(equipment_type)
        except KeyError:
            continue
        for metric in spec.metrics:
            messages.extend(
                generate_metric_history(
                    global_id=global_id,
                    metric=metric,
                    history_days=spec.history_days,
                    points_per_day=spec.points_per_day,
                    now=now,
                    rng=rng,
                    source="demo-generator",
                )
            )
    print(f"🧪 {len(messages)} messages de démo générés (catalogue par type d'équipement).")

    payloads = [
        {
            "global_id": message.global_id,
            "timestamp": message.timestamp.isoformat(),
            "metric": message.metric,
            "value": message.value,
            "unit": message.unit,
            "quality": message.quality,
            "source": message.source,
        }
        for message in messages
    ]

    adapter = NatsJetStreamAdapter(servers=nats_servers, user=nats_user, password=nats_password)
    adapter.publish_many(subject, payloads)
    print(f"📤 {len(payloads)} messages publiés sur NATS (subject='{subject}').")


if __name__ == "__main__":
    main()
