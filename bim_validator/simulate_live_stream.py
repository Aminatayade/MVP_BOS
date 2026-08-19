"""
Simulateur de flux LIVE : publie en continu (une vague toutes les N secondes)
une nouvelle mesure par (Equipment, métrique) avec l'horodatage courant.

Contrairement à publish_demo_stream.py (un lot historique envoyé en une fois),
ce script tourne indéfiniment — c'est la démonstration de l'injection "live"
(jour 4) : lance-le dans un terminal, laisse tourner
`docker logs -f bos_timeseries_api` dans un autre, et regarde les points
arriver en temps réel.

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
from application.demo_data_generator import METRIC_PROFILES
from publish_demo_stream import DEFAULT_SUBJECT, fetch_real_global_ids


def _generate_point(global_id: str, metric: str, source: str) -> dict:
    profile = METRIC_PROFILES[metric]
    base = float(profile["base"])
    spread = float(profile["spread"])
    value = round(base + random.uniform(-spread, spread), 2)
    return {
        "global_id": global_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metric": metric,
        "value": value,
        "unit": str(profile["unit"]),
        "quality": "good" if random.random() > 0.02 else "uncertain",
        "source": source,
    }


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

    global_ids = fetch_real_global_ids(
        base_url=base_url, database=database, username=username, password=password, limit=args.limit
    )
    print(f"📦 {len(global_ids)} Equipment.global_id réels : {global_ids}")

    adapter = NatsJetStreamAdapter(servers=nats_servers, user=nats_user, password=nats_password)
    source = "live-simulator"

    print(f"🔴 Flux live démarré (une vague toutes les {args.interval:g}s). Ctrl+C pour arrêter.")
    try:
        while True:
            payloads = [
                _generate_point(global_id, metric, source)
                for global_id in global_ids
                for metric in METRIC_PROFILES
            ]
            adapter.publish_many(subject, payloads)
            print(f"📤 {len(payloads)} point(s) publié(s) à {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n🛑 Arrêt du flux live.")


if __name__ == "__main__":
    main()
