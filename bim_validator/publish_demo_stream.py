"""
Script utilitaire : publie un lot de mesures de démo sur NATS JetStream.

Récupère les VRAIS Equipment.global_id depuis ArcadeDB (requête SQL directe,
pas besoin de passer par un port pour ce script utilitaire), génère des
UnifiedMessage plausibles, puis les publie sur NATS.

Ce script ne touche JAMAIS ArcadeDB pour l'écriture des points : l'écriture
passe TOUJOURS par consume_timeseries.py. C'est le point qui justifie la
queue au lieu d'une écriture directe.

Usage :
    python publish_demo_stream.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import requests

from adapters.nats_adapter import NatsJetStreamAdapter
from application.demo_data_generator import generate_demo_messages

DEFAULT_SUBJECT = "bos.timeseries.demo"


def fetch_real_global_ids(
    *, base_url: str, database: str, username: str, password: str, limit: int = 5
) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/api/v1/command/{database}"
    payload = {"language": "sql", "command": f"SELECT global_id FROM Equipment LIMIT {int(limit)}"}
    response = requests.post(endpoint, json=payload, auth=(username, password), timeout=30)
    response.raise_for_status()
    rows = response.json().get("result", [])
    global_ids = [row["global_id"] for row in rows if row.get("global_id")]
    if not global_ids:
        raise RuntimeError(
            "Aucun Equipment.global_id trouvé dans ArcadeDB. Ingérez d'abord une maquette IFC "
            "(voir main.py) avant de lancer ce script de démo."
        )
    return global_ids


def main() -> None:
    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    database = os.environ.get("ARCADEDB_DATABASE", "bim")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
    nats_servers = os.environ.get("NATS_URL", "nats://localhost:4222")
    nats_user = os.environ.get("NATS_USER", "bos_admin")
    nats_password = os.environ.get("NATS_PASSWORD", "testBIM123")
    subject = os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)

    global_ids = fetch_real_global_ids(
        base_url=base_url, database=database, username=username, password=password
    )
    print(f"📦 {len(global_ids)} Equipment.global_id réels récupérés depuis ArcadeDB : {global_ids}")

    messages = generate_demo_messages(global_ids)
    print(f"🧪 {len(messages)} messages de démo générés (température/co2/occupancy).")

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
