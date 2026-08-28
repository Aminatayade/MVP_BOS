"""
Service applicatif : génère et écrit l'historique simulé (+ valeur
courante, qui n'est que le dernier point de cet historique) d'un lot
d'équipements fraîchement ingérés, selon le catalogue de simulation par
type d'équipement.

Volontairement séparé de BimGraphIngestionService : ce dernier ne connaît
que BimGraphRepositoryPort (le graphe), jamais TimeseriesRepositoryPort --
respecte la même séparation domain/application/adapters déjà en place pour
le reste du pipeline. Ce service est appelé DEPUIS l'ingestion (voir
api_server.py) mais reste testable/remplaçable indépendamment.

Le TYPE_CHECKING plus bas évite un import circulaire : EquipmentNode vit
dans bim_graph_ingestion_service.py, qui a besoin d'appeler ce service.
"""

from __future__ import annotations

import random
import sys
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from application.simulation_generators import generate_metric_history
from domain.entities import UnifiedMessage
from domain.ports import EquipmentCatalogPort, TimeseriesRepositoryPort

if TYPE_CHECKING:
    from application.services.bim_graph_ingestion_service import EquipmentNode


def _iter_chunks(items: Sequence[UnifiedMessage], size: int) -> Iterable[list[UnifiedMessage]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


class EquipmentSimulationService:
    def __init__(
        self,
        *,
        catalog: EquipmentCatalogPort,
        timeseries_repository: TimeseriesRepositoryPort,
        write_batch_size: int = 500,
        max_workers: int = 2,
    ) -> None:
        self._catalog = catalog
        self._timeseries_repository = timeseries_repository
        self._write_batch_size = write_batch_size
        # Pool BORNÉ (pas un Thread() nu par fichier ingéré) : un batch
        # d'ingestion peut contenir plusieurs maquettes à la suite, chacune
        # avec des centaines d'équipements -- sans plafond, générer
        # l'historique de toutes en parallèle pourrait saturer ArcadeDB au
        # moment même où l'ingestion graphe (synchrone) de la maquette
        # suivante y écrit aussi.
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="equip-sim-backfill")

    def backfill_for_equipment_async(
        self,
        equipment: Sequence["EquipmentNode"],
        *,
        seed: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Soumet le backfill au pool et retourne immédiatement -- ne bloque
        jamais l'appelant (la réponse HTTP d'ingestion notamment)."""
        self._executor.submit(self.backfill_for_equipment, list(equipment), seed=seed, now=now)

    def backfill_for_equipment(
        self,
        equipment: Sequence["EquipmentNode"],
        *,
        seed: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Best-effort : une erreur sur UN équipement (générateur mal
        configuré, écriture ArcadeDB en échec) ne doit jamais empêcher la
        génération des autres. Appelé en tâche de fond (voir
        api_server.py) -- une exception ici ne remonte à personne, donc
        chaque échec est loggé plutôt que silencieusement avalé."""
        reference = now or datetime.now(timezone.utc)
        rng = random.Random(seed)

        for node in equipment:
            try:
                spec = self._catalog.get_generator_spec(node.equipment_type)
            except KeyError:
                print(
                    f"⚠️  Type d'équipement '{node.equipment_type}' absent du catalogue "
                    f"pour {node.global_id} -- aucune simulation générée.",
                    file=sys.stderr,
                )
                continue

            if not spec.metrics:
                continue

            messages: list[UnifiedMessage] = []
            for metric in spec.metrics:
                messages.extend(
                    generate_metric_history(
                        global_id=node.global_id,
                        metric=metric,
                        history_days=spec.history_days,
                        points_per_day=spec.points_per_day,
                        now=reference,
                        rng=rng,
                    )
                )

            for chunk in _iter_chunks(messages, self._write_batch_size):
                try:
                    self._timeseries_repository.write_points_batch(chunk)
                except Exception as exc:
                    print(
                        f"⚠️  Échec écriture backfill timeseries pour {node.global_id} "
                        f"({spec.equipment_type}): {exc}",
                        file=sys.stderr,
                    )
                    break
