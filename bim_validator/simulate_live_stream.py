"""
Simulateur de flux LIVE : publie en continu (une vague toutes les N secondes)
une nouvelle mesure par (Equipment, métrique) avec l'horodatage courant, en
respectant les métriques du catalogue de simulation par type d'équipement
(equipment_simulation_catalog.json, même catalogue que celui utilisé
automatiquement à l'ingestion).

Contrairement à publish_demo_stream.py (un lot historique envoyé en une fois),
ce script tourne indéfiniment — c'est la démonstration de l'injection "live"
(jour 4) : lance-le dans un terminal, laisse tourner
`docker logs -f bos_timeseries_api` dans un autre, et regarde les points
arriver en temps réel.

Chaque point "courant" est généré via le même générateur que l'historique,
appelé avec history_days=0 (un seul point, exactement à `now`) -- même forme
physique (oscillation jour/nuit, corrélation occupation...), pas un second
modèle de génération à maintenir en parallèle. Limite connue : un compteur
"cumulatif" (ex: compteur électrique) repart d'une valeur proche de zéro à
chaque vague plutôt que de continuer à croître -- suivre un cumul dans la
durée nécessiterait un état persistant entre vagues, hors scope de ce script
de démo (l'historique généré automatiquement à l'ingestion, lui, est correct
sur toute sa fenêtre car généré en un seul passage continu).

Usage :
    python simulate_live_stream.py
    python simulate_live_stream.py --interval 3 --limit 10
    (Ctrl+C pour arrêter)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from adapters.nats_adapter import NatsJetStreamAdapter
from application.simulation_generators import generate_metric_history
from publish_demo_stream import DEFAULT_SUBJECT, _build_catalog, fetch_real_equipment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0, help="Secondes entre deux vagues de publication.")
    parser.add_argument("--limit", type=int, default=5, help="Nombre d'Equipment.global_id à utiliser.")
    args = parser.parse_args()

    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    database = os.environ.get("ARCADEDB_DATABASE", "bim")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
    nats_servers = os.environ.get("NATS_URL", "nats://localhost:4222")
    nats_user = os.environ.get("NATS_USER", "bos_admin")
    nats_password = os.environ.get("NATS_PASSWORD", "testBIM123")
    subject = os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)

    equipment = fetch_real_equipment(
        base_url=base_url, database=database, username=username, password=password, limit=args.limit
    )
    print(f"📦 {len(equipment)} Equipment réels : {equipment}")

    catalog = _build_catalog()
    equipment_specs = []
    for global_id, equipment_type in equipment:
        try:
            spec = catalog.get_generator_spec(equipment_type)
        except KeyError:
            continue
        if spec.metrics:
            equipment_specs.append((global_id, spec))

    adapter = NatsJetStreamAdapter(servers=nats_servers, user=nats_user, password=nats_password)
    rng = random.Random()

    print(f"🔴 Flux live démarré (une vague toutes les {args.interval:g}s). Ctrl+C pour arrêter.")
    try:
        while True:
            now = datetime.now(timezone.utc)
            messages = [
                message
                for global_id, spec in equipment_specs
                for metric in spec.metrics
                for message in generate_metric_history(
                    global_id=global_id, metric=metric, history_days=0, points_per_day=1,
                    now=now, rng=rng, source="live-simulator",
                )
            ]
            payloads = [
                {
                    "global_id": m.global_id,
                    "timestamp": m.timestamp.isoformat(),
                    "metric": m.metric,
                    "value": m.value,
                    "unit": m.unit,
                    "quality": m.quality,
                    "source": m.source,
                }
                for m in messages
            ]
            adapter.publish_many(subject, payloads)
            print(f"📤 {len(payloads)} point(s) publié(s) à {now.strftime('%H:%M:%S')}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n🛑 Arrêt du flux live.")


if __name__ == "__main__":
    main()
