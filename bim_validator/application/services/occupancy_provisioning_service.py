"""
Service applicatif : provisionne, une fois par zone, un capteur de présence
SYNTHÉTIQUE (Equipment) par Room réelle du graphe -- nécessaire pour le
cockpit "Compréhension du taux d'usage des espaces", puisqu'aucune maquette
IFC actuelle ne modélise de vrais capteurs d'occupation (IfcSensor
MOVEMENTSENSOR/OCCUPANCYSENSOR).

Réutilise volontairement les mêmes helpers Cypher que l'ingestion réelle
(`_merge_equipment_node_query`, `_merge_edge_query`, `_cypher_escape` de
bim_graph_ingestion_service.py) plutôt que de réimplémenter du MERGE/
échappement -- même convention que EquipmentSimulationService, qui importe
déjà `EquipmentNode` depuis ce module en TYPE_CHECKING.

Idempotent : un second appel sur une zone déjà provisionnée ne recrée ni ne
double aucun capteur (détecté via la propriété `simulated = true`).

Zone sans Room (ex: Batipompe, bâtiment technique de pompage sans espace
occupé par des humains) : `has_rooms=False`, AUCUNE écriture tentée -- ce
n'est pas un trou de données à combler, un tel bâtiment n'a réellement aucun
espace de ce type.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from application.equipment_simulation_service import EquipmentSimulationService
from application.services.bim_graph_ingestion_service import (
    EquipmentNode,
    _cypher_escape,
    _merge_edge_query,
    _merge_equipment_node_query,
)

if TYPE_CHECKING:
    from domain.ports import BimGraphRepositoryPort

SENSOR_EQUIPMENT_TYPE = "detecteur_presence"
SPACE_TYPES = ["salle_reunion", "poste_travail", "bulle"]
SPACE_TYPE_WEIGHTS = [0.35, 0.45, 0.20]


def _sensor_global_id(room_global_id: str) -> str:
    return f"synthetic-presence::{room_global_id}"


def _assign_space_type(room_global_id: str) -> str:
    # Seedé par le global_id de la Room (pas un rng partagé) : stable d'un
    # (re)provisionnement à l'autre, indépendant de l'ordre de traitement
    # des pièces.
    rng = random.Random(f"space-type::{room_global_id}")
    return rng.choices(SPACE_TYPES, weights=SPACE_TYPE_WEIGHTS, k=1)[0]


class OccupancyProvisioningService:
    def __init__(
        self,
        *,
        graph_repository: "BimGraphRepositoryPort",
        simulation_service: EquipmentSimulationService,
        zone: str,
    ) -> None:
        self._graph_repository = graph_repository
        self._simulation_service = simulation_service
        self._zone = zone

    def provision_zone_if_needed(self) -> dict:
        escaped_zone = _cypher_escape(self._zone)
        rooms = (
            self._graph_repository.query_cypher(
                f"MATCH (r:Room {{zone: '{escaped_zone}'}}) RETURN r.global_id AS global_id"
            ).get("result")
            or []
        )
        if not rooms:
            return {"has_rooms": False, "room_count": 0, "sensor_count": 0}

        existing_rows = (
            self._graph_repository.query_cypher(
                f"MATCH (e:Equipment {{zone: '{escaped_zone}'}}) "
                f"WHERE e.equipment_type = '{SENSOR_EQUIPMENT_TYPE}' AND e.simulated = true "
                "RETURN e.global_id AS global_id"
            ).get("result")
            or []
        )
        existing_sensor_ids = {row["global_id"] for row in existing_rows if row.get("global_id")}

        missing_rooms = [
            room for room in rooms if _sensor_global_id(room["global_id"]) not in existing_sensor_ids
        ]
        if not missing_rooms:
            return {"has_rooms": True, "room_count": len(rooms), "sensor_count": len(existing_sensor_ids)}

        commands: list[str] = []
        new_equipment: list[EquipmentNode] = []
        for room in missing_rooms:
            room_gid = room["global_id"]
            sensor_gid = _sensor_global_id(room_gid)
            space_type = _assign_space_type(room_gid)

            node = EquipmentNode(
                global_id=sensor_gid,
                ifc_type="IfcSensor",
                name="Détecteur de présence (simulé)",
                raw_name="SYNTH-PRESENCE",
                category="security",
                is_read_only=True,
                discipline="simulation",
                storage_url="",
                zone=self._zone,
                equipment_type=SENSOR_EQUIPMENT_TYPE,
            )
            new_equipment.append(node)

            commands.append(_merge_equipment_node_query(node))
            # `_merge_equipment_node_query` ne connaît pas `simulated`/
            # `space_type` (pas des champs d'EquipmentNode, partagé avec
            # l'ingestion réelle -- ne pas y toucher) : posés séparément.
            escaped_sensor_gid = _cypher_escape(sensor_gid)
            escaped_space_type = _cypher_escape(space_type)
            commands.append(
                f"MATCH (e:Equipment {{global_id: '{escaped_sensor_gid}', zone: '{escaped_zone}'}}) "
                f"SET e.simulated = true, e.space_type = '{escaped_space_type}'"
            )
            commands.append(
                f"MATCH (r:Room {{global_id: '{_cypher_escape(room_gid)}', zone: '{escaped_zone}'}}) "
                f"SET r.space_type = '{escaped_space_type}'"
            )
            commands.append(_merge_edge_query("LOCATED_IN", sensor_gid, room_gid, zone=self._zone))

        self._graph_repository.execute_cypher_batch(commands, batch_size=500)

        # Synchrone (pas backfill_for_equipment_async) : le dashboard n'a
        # rien à agréger tant que l'historique n'existe pas -- coût borné au
        # premier appel par zone (idempotent ensuite via existing_sensor_ids
        # ci-dessus).
        self._simulation_service.backfill_for_equipment(
            new_equipment, seed=abs(hash(self._zone)) % (2**32)
        )

        return {
            "has_rooms": True,
            "room_count": len(rooms),
            "sensor_count": len(existing_sensor_ids) + len(new_equipment),
        }
