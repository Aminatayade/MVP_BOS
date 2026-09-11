"""
Simulateur de flux LIVE continu, par zone ENTIÈRE -- généralisation de
simulate_live_stream.py (script de démo, 5 équipements par défaut, à lancer
à la main) en un service tournant en tâche de fond dans timeseries_api.py,
démarré/arrêté via API pour une zone donnée (voir /timeseries/live-simulation/*).

Étalement volontaire de la charge : TimeseriesIngestionService._handle_message
écrit UN point par appel HTTP (aucun batching, vérifié directement dans le
code) -- publier tout un bâtiment d'un coup à chaque tick reproduirait la
surcharge des plusieurs millions de lignes rencontrée lors du backfill
initial (cause de l'instabilité ArcadeDB corrigée plus tôt cette session).
`compute_slice` fait tourner un SOUS-ENSEMBLE d'équipements à chaque tick
(round-robin), de sorte que la zone entière soit couverte sur un cycle de
plusieurs minutes plutôt que d'un coup.

Connexion NATS PERSISTANTE (`_LivePublisher`) : contrairement à
NatsJetStreamAdapter.publish_many (synchrone, une connexion par appel via
asyncio.run interne) -- incompatible ici puisque run_zone tourne DÉJÀ dans
la boucle asyncio de FastAPI (asyncio.run ne peut pas être appelé depuis un
contexte asyncio déjà actif).
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from application.simulation_generators import generate_metric_history

if TYPE_CHECKING:
    from domain.entities import EquipmentSimulationSpec

STREAM_NAME = "BOS_TIMESERIES"
DEFAULT_SUBJECT = "bos.timeseries.live"


def compute_slice(equipment_ids: list[str], tick_index: int, ticks_per_cycle: int) -> list[str]:
    """Round-robin pur : sur `ticks_per_cycle` appels successifs
    (tick_index de 0 à ticks_per_cycle-1), chaque élément de `equipment_ids`
    apparaît dans EXACTEMENT une tranche -- jamais deux fois, jamais
    aucune. Fonction pure, testable sans aucun service live (voir
    tests/test_live_simulation_service.py)."""
    if ticks_per_cycle <= 0:
        return list(equipment_ids)
    return equipment_ids[tick_index % ticks_per_cycle :: ticks_per_cycle]


class LivePublisher:
    """Connexion JetStream ouverte une fois et gardée pour toute la durée
    de la simulation d'une zone."""

    def __init__(self, *, servers: str, user: str | None, password: str | None, subjects: list[str]):
        self._servers = servers
        self._user = user
        self._password = password
        self._subjects = subjects
        self._nc = None
        self._js = None

    async def connect(self) -> None:
        # Import paresseux : garde compute_slice() (fonction pure) testable
        # sur la venv hôte, où nats-py n'est installé que dans les images
        # Docker bim_api/timeseries_api, pas nécessaire pour les tests purs.
        import nats

        self._nc = await nats.connect(servers=[self._servers], user=self._user, password=self._password)
        self._js = self._nc.jetstream()
        try:
            await self._js.add_stream(name=STREAM_NAME, subjects=self._subjects)
        except Exception as exc:
            description = str(exc).lower()
            if not any(token in description for token in ("already", "in use", "exists")):
                raise

    async def publish(self, subject: str, payload: dict) -> None:
        await self._js.publish(subject, json.dumps(payload).encode("utf-8"))

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()


async def run_zone(
    *,
    zone: str,
    equipment_specs: list[tuple[str, "EquipmentSimulationSpec"]],
    publisher: LivePublisher,
    subject: str,
    tick_seconds: float,
    cycle_minutes: float,
    stop_event: asyncio.Event,
) -> None:
    """Boucle infinie (jusqu'à stop_event) : à chaque tick, publie un point
    courant par (équipement, métrique) pour la tranche du cycle en cours.
    `equipment_specs` : paires (global_id, EquipmentSimulationSpec) déjà
    résolues via le catalogue -- voir l'appelant dans timeseries_api.py."""
    equipment_ids = [global_id for global_id, _ in equipment_specs]
    specs_by_id = dict(equipment_specs)
    ticks_per_cycle = max(1, round((cycle_minutes * 60) / tick_seconds))
    rng = random.Random()
    tick_index = 0

    print(
        f"🔴 Simulation live démarrée pour la zone '{zone}' "
        f"({len(equipment_ids)} équipements, {ticks_per_cycle} ticks/cycle, "
        f"tick={tick_seconds}s).",
        file=sys.stderr,
    )
    try:
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)
            slice_ids = compute_slice(equipment_ids, tick_index % ticks_per_cycle, ticks_per_cycle)
            published = 0
            for global_id in slice_ids:
                spec = specs_by_id[global_id]
                for metric in spec.metrics:
                    for message in generate_metric_history(
                        global_id=global_id, metric=metric, history_days=0, points_per_day=1,
                        now=now, rng=rng, source="live-simulator",
                    ):
                        await publisher.publish(subject, {
                            "global_id": message.global_id,
                            "timestamp": message.timestamp.isoformat(),
                            "metric": message.metric,
                            "value": message.value,
                            "unit": message.unit,
                            "quality": message.quality,
                            "source": message.source,
                        })
                        published += 1
            tick_index += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        print(f"🛑 Simulation live arrêtée pour la zone '{zone}'.", file=sys.stderr)
