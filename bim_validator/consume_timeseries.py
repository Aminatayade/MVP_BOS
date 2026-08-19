"""
Worker EN CONTINU : consomme le canal time-series NATS JetStream et écrit
dans le moteur TIMESERIES natif d'ArcadeDB.

Peut démarrer AVANT ou APRÈS publish_demo_stream.py sans perdre de messages :
le consumer durable JetStream (bos-timeseries-consumer) mémorise côté
serveur NATS quels messages ont déjà été ACK, donc tout message publié
pendant que ce worker est éteint reste dans le stream et lui est livré dès
qu'il relance subscribe() sous le même nom de consumer.

Usage :
    python consume_timeseries.py
    (Ctrl+C pour arrêter)
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from adapters.arcadedb_ts_adapter import ArcadeDbTimeseriesAdapter
from adapters.nats_adapter import NatsJetStreamAdapter
from application.timeseries_ingestion_service import TimeseriesIngestionService

DEFAULT_SUBJECT = "bos.timeseries.>"
STARTUP_MAX_ATTEMPTS = 12
STARTUP_RETRY_DELAY_SECONDS = 5.0


def run_consumer() -> None:
    """Construit repository/queue/service depuis l'environnement et consomme
    indéfiniment (bloquant). Réutilisée telle quelle par le script standalone
    et par le thread de fond de timeseries_api.py — même bootstrap partout."""
    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    database = os.environ.get("ARCADEDB_DATABASE", "bim")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
    nats_servers = os.environ.get("NATS_URL", "nats://localhost:4222")
    nats_user = os.environ.get("NATS_USER", "bos_admin")
    nats_password = os.environ.get("NATS_PASSWORD", "testBIM123")
    subject = os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)

    repository = ArcadeDbTimeseriesAdapter(
        base_url=base_url, database=database, username=username, password=password
    )
    _wait_for_arcadedb_schema(repository, base_url=base_url, database=database)

    queue = NatsJetStreamAdapter(servers=nats_servers, user=nats_user, password=nats_password)
    service = TimeseriesIngestionService(queue, repository)

    service.start_consuming(subject)


def _wait_for_arcadedb_schema(
    repository: ArcadeDbTimeseriesAdapter, *, base_url: str, database: str
) -> None:
    """`depends_on` dans docker-compose garantit seulement l'ORDRE de démarrage
    des conteneurs, pas que le serveur HTTP d'ArcadeDB accepte déjà des
    connexions (son JVM met quelques secondes à booter). Sans retry ici, une
    ConnectionError au tout premier appel tuait ce thread de façon définitive :
    ArcadeDB revenait ensuite, mais plus personne n'écoutait NATS."""
    last_exc: Exception | None = None
    for attempt in range(1, STARTUP_MAX_ATTEMPTS + 1):
        try:
            repository.ensure_schema()
            print(f"🗄️  Schéma EquipmentReading vérifié/créé dans ArcadeDB ({base_url}/{database}).")
            return
        except Exception as exc:
            last_exc = exc
            print(
                f"⏳ ArcadeDB indisponible, nouvelle tentative dans {STARTUP_RETRY_DELAY_SECONDS:g}s "
                f"({attempt}/{STARTUP_MAX_ATTEMPTS}) : {exc}",
                file=sys.stderr,
            )
            time.sleep(STARTUP_RETRY_DELAY_SECONDS)

    raise RuntimeError(
        f"ArcadeDB injoignable après {STARTUP_MAX_ATTEMPTS} tentatives ({base_url}/{database})"
    ) from last_exc


def main() -> None:
    try:
        run_consumer()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt demandé (Ctrl+C).")


if __name__ == "__main__":
    main()
