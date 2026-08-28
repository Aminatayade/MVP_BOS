from __future__ import annotations

import os
import sys
import re
from collections.abc import Iterable
from dataclasses import dataclass

from application.bim_validator_service import BimValidatorService
from application.equipment_simulation_service import EquipmentSimulationService
from domain.entities import ValidationReport
from domain.ports import (
    BimGraphRepositoryPort,
    ComplianceRulesRepositoryPort,
    EquipmentCatalogPort,
    IfcModelReaderPort,
    SeaweedStoragePort,
)


IGNORED_DISCIPLINES = {
    "GENIE_CIVIL",
    "GENIECIVIL",
    "CIVIL",
    "STRUCTURE",
    "PERCEMENTS",
    "ARMATURES",
}

ROOM_TYPLOGY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "meeting_room": ("meeting", "reunion", "conference", "salle projet"),
    "office": ("bureau", "office", "workspace"),
    "sanitary": ("wc", "toilet", "sanitaire", "lavabo", "douche"),
    "technical": ("local technique", "gtb", "it", "serveur", "tgbt"),
    "circulation": ("couloir", "circulation", "hall", "lobby", "degagement"),
    "storage": ("reserve", "stock", "archive", "magasin"),
}

EQUIPMENT_CATEGORIES: dict[str, set[str]] = {
    "cvc": {
        "IfcFlowTerminal",
        "IfcFlowMovingDevice",
        "IfcFlowController",
        "IfcFlowSegment",
        "IfcFlowFitting",
        "IfcAirTerminal",
        "IfcUnitaryEquipment",
    },
    "electricity": {
        "IfcElectricAppliance",
        "IfcLightFixture",
        "IfcSwitchingDevice",
        "IfcDistributionBoard",
        "IfcElectricDistributionPoint",
        "IfcCableCarrierSegment",
        "IfcCableSegment",
    },
    "security": {
        "IfcAlarm",
        "IfcSensor",
        "IfcCommunicationsAppliance",
        "IfcAudioVisualAppliance",
    },
    "sanitary": {
        "IfcSanitaryTerminal",
        "IfcPipeSegment",
        "IfcPipeFitting",
        "IfcValve",
    },
    "furniture": {
        "IfcFurniture",
        "IfcSystemFurnitureElement",
    },
}

FIRE_SECURITY_TOKENS = (
    "ssi",
    "fire",
    "incendie",
    "smoke",
    "sprinkler",
    "extinct",
    "alarme",
    "detector",
)


@dataclass(frozen=True)
class SpatialNode:
    label: str
    global_id: str
    name: str
    raw_name: str = ""
    discipline: str = ""
    ifc_type: str = ""
    storage_url: str = ""
    zone: str = ""


@dataclass(frozen=True)
class EquipmentNode:
    global_id: str
    ifc_type: str
    name: str
    category: str
    is_read_only: bool
    raw_name: str = ""
    discipline: str = ""
    storage_url: str = ""
    zone: str = ""
    equipment_type: str = ""


@dataclass(frozen=True)
class BimGraphIngestionResult:
    status: str
    message: str
    source_key: str
    validation: ValidationReport | None
    commands_count: int = 0
    nodes_count: int = 0
    edges_count: int = 0
    equipment_count: int = 0
    unmatched_equipment_count: int = 0


class BimGraphIngestionService:
    """Use case that ingests a validated IFC into ArcadeDB as a graph."""

    def __init__(
        self,
        *,
        seaweed_storage: SeaweedStoragePort,
        validator: BimValidatorService | None,
        graph_repository: BimGraphRepositoryPort,
        ifc_reader: IfcModelReaderPort,
        rules_repository: ComplianceRulesRepositoryPort,
        equipment_catalog: EquipmentCatalogPort,
        equipment_simulation_service: EquipmentSimulationService | None = None,
        arcade_batch_size: int = 500,
    ) -> None:
        self._seaweed_storage = seaweed_storage
        self._validator = validator
        self._graph_repository = graph_repository
        self._ifc_reader = ifc_reader
        self._rules_repository = rules_repository
        self._equipment_catalog = equipment_catalog
        # Optionnel : certains appelants (tests, outils CLI) n'ont pas besoin
        # de simulation -- ingestion graphe et simulation restent découplées.
        self._equipment_simulation_service = equipment_simulation_service
        self._arcade_batch_size = arcade_batch_size

    def ingest_from_storage(
        self,
        *,
        object_key: str,
        discipline: str,
        zone: str,
        bucket: str | None = None,
        run_validation: bool = False,
    ) -> BimGraphIngestionResult:
        normalized_discipline = self._rules_repository.canonical_discipline(discipline)
        if normalized_discipline in IGNORED_DISCIPLINES:
            return BimGraphIngestionResult(
                status="skipped",
                message=f"Discipline ignored for graph ingestion: {discipline}",
                source_key=object_key,
                validation=None,
            )

        local_ifc_path = self._seaweed_storage.download_ifc(object_key, bucket=bucket)
        try:
            validation: ValidationReport | None = None
            if run_validation:
                if self._validator is None:
                    raise RuntimeError(
                        "run_validation=True requires a BimValidatorService instance."
                    )

                validation = self._validator.validate(local_ifc_path, discipline)
                if not validation.success:
                    return BimGraphIngestionResult(
                        status="validation_failed",
                        message="IFC validation rejected the model.",
                        source_key=object_key,
                        validation=validation,
                    )

            self._ifc_reader.load(local_ifc_path)
            raw_model = self._get_raw_model()

            # is_spatial_backbone_discipline lève KeyError si la discipline n'est
            # pas configurée dans la charte : échec explicite plutôt que le
            # repli silencieux "non-backbone" qui masquait les fautes de frappe.
            build_backbone = self._rules_repository.is_spatial_backbone_discipline(normalized_discipline)
            (
                graph_commands,
                node_count,
                edge_count,
                equipment_count,
                unmatched_equipment_count,
                equipment_nodes,
            ) = self._build_graph_commands(
                raw_model,
                build_spatial_backbone=build_backbone,
                discipline=normalized_discipline,
                zone=zone,
                storage_url=object_key,
            )
            if unmatched_equipment_count:
                print(
                    f"⚠️  {unmatched_equipment_count}/{equipment_count} équipement(s) non rattaché(s) "
                    f"à un Room/Floor (discipline={normalized_discipline}, zone='{zone}').",
                    file=sys.stderr,
                )
            self._graph_repository.execute_cypher_batch(
                graph_commands,
                batch_size=self._arcade_batch_size,
            )

            # Post-ingestion: run deduplication and cleanup to ensure no duplicate global_id
            try:
                self._run_post_ingest_deduplication()
            except Exception:
                # Don't fail the whole ingestion if cleanup fails; log by printing.
                print("Warning: post-ingestion deduplication failed", file=sys.stderr)

            # Simulation de télémétrie : best-effort, en tâche de fond -- ne
            # doit jamais retarder ni faire échouer la réponse d'ingestion.
            # Les nœuds Equipment existent déjà en base à ce stade (batch
            # ci-dessus déjà exécuté), donc aucune dépendance d'ordre avec
            # le backfill timeseries (deux magasins indépendants).
            if self._equipment_simulation_service is not None and equipment_nodes:
                self._equipment_simulation_service.backfill_for_equipment_async(equipment_nodes)

            return BimGraphIngestionResult(
                status="ingested",
                message="Graph successfully ingested into ArcadeDB.",
                source_key=object_key,
                validation=validation,
                commands_count=len(graph_commands),
                nodes_count=node_count,
                edges_count=edge_count,
                equipment_count=equipment_count,
                unmatched_equipment_count=unmatched_equipment_count,
            )
        finally:
            try:
                if os.path.exists(local_ifc_path):
                    os.unlink(local_ifc_path)
            except OSError:
                # A temporary file cleanup issue should not hide ingestion result.
                pass

    def _get_raw_model(self):
        raw_model = getattr(self._ifc_reader, "_model", None)
        if raw_model is None:
            raise RuntimeError(
                "IfcModelReaderPort implementation must expose the parsed IFC model "
                "for graph extraction (expected attribute: _model)."
            )
        return raw_model

    def _build_graph_commands(
        self,
        model,
        build_spatial_backbone: bool = True,
        *,
        discipline: str = "",
        zone: str = "",
        storage_url: str = "",
    ) -> tuple[list[str], int, int, int, int, list[EquipmentNode]]:
        all_nodes_commands: list[str] = []
        edge_commands: list[str] = []

        room_to_floor = {}
        room_nodes: list[SpatialNode] = []

        if build_spatial_backbone:
            raw_rooms = list(model.by_type("IfcSpace"))
            raw_room_by_global_id = {getattr(r, "GlobalId", None): r for r in raw_rooms}

            site_nodes = [_to_spatial_node(site, "Site", discipline=discipline, storage_url=storage_url, zone=zone) for site in model.by_type("IfcSite")]
            building_nodes = [_to_spatial_node(building, "Building", discipline=discipline, storage_url=storage_url, zone=zone) for building in model.by_type("IfcBuilding")]
            floor_nodes = [_to_spatial_node(floor, "Floor", discipline=discipline, storage_url=storage_url, zone=zone) for floor in model.by_type("IfcBuildingStorey")]
            room_nodes = [_to_spatial_node(room, "Room", discipline=discipline, storage_url=storage_url, zone=zone) for room in raw_rooms]

            for node in (*site_nodes, *building_nodes, *floor_nodes, *room_nodes):
                all_nodes_commands.append(_merge_spatial_node_query(node))

            parent_child_edges = _extract_has_child_edges(model)
            edge_commands.extend(_merge_edge_query("HAS_CHILD", src, dst) for src, dst in parent_child_edges)

            room_to_floor = _map_room_to_floor(model)

            typology_nodes_by_id: dict[str, str] = {}
            typology_edges: set[tuple[str, str]] = set()
            membership_edges: set[tuple[str, str]] = set()

            for room in room_nodes:
                floor_id = room_to_floor.get(room.global_id)
                if not floor_id:
                    continue

                raw_room = raw_room_by_global_id.get(room.global_id)
                typology_name = _resolve_room_typology(raw_room, name=room.name, raw_name=room.raw_name)
                typology_global_id = f"typology::{floor_id}::{_clean_typology_name(typology_name)}"

                typology_nodes_by_id.setdefault(typology_global_id, typology_name)
                typology_edges.add((floor_id, typology_global_id))
                membership_edges.add((typology_global_id, room.global_id))

            for typology_global_id, typology_name in typology_nodes_by_id.items():
                all_nodes_commands.append(_merge_typology_node_query(typology_global_id, typology_name, zone=zone))

            edge_commands.extend(
                _merge_edge_query("HAS_GROUP", floor_id, typology_id)
                for floor_id, typology_id in sorted(typology_edges)
            )
            edge_commands.extend(
                _merge_edge_query("HAS_MEMBER", typology_id, room_id)
                for typology_id, room_id in sorted(membership_edges)
            )

        # Always extract equipment; equipment location resolution can work without building backbone
        equipment_nodes, equipment_edges = self._extract_equipment_nodes_and_edges(
            model, room_to_floor, discipline=discipline, storage_url=storage_url, zone=zone
        )
        for equipment in equipment_nodes:
            all_nodes_commands.append(_merge_equipment_node_query(equipment))
        edge_commands.extend(
            _merge_edge_query("LOCATED_IN", equipment_id, location_id)
            for equipment_id, location_id in sorted(equipment_edges)
        )

        commands = all_nodes_commands + edge_commands
        node_count = len(all_nodes_commands)
        edge_count = len(edge_commands)

        equipment_count = len(equipment_nodes)
        matched_equipment_ids = {equipment_id for equipment_id, _location_id in equipment_edges}
        unmatched_equipment_count = equipment_count - len(matched_equipment_ids)

        return commands, node_count, edge_count, equipment_count, unmatched_equipment_count, equipment_nodes

    def _extract_equipment_nodes_and_edges(
        self,
        model,
        room_to_floor: dict[str, str],
        *,
        discipline: str = "",
        storage_url: str = "",
        zone: str = "",
    ) -> tuple[list[EquipmentNode], set[tuple[str, str]]]:
        equipment_nodes_by_id: dict[str, EquipmentNode] = {}
        located_in_edges: set[tuple[str, str]] = set()

        room_ids = set(room_to_floor)
        floor_ids = set(room_to_floor.values())

        for category, ifc_types in EQUIPMENT_CATEGORIES.items():
            for ifc_type in ifc_types:
                for element in _safe_by_type(model, ifc_type):
                    equipment_id = getattr(element, "GlobalId", None)
                    if not equipment_id:
                        continue

                    name = _cascade_display_name(element, fallback=ifc_type or "Equipment")
                    raw_name = _raw_name(element)
                    object_type = getattr(element, "ObjectType", "") or ""
                    is_read_only = _is_fire_or_ssi_equipment(
                        ifc_type=ifc_type,
                        name=name,
                        object_type=object_type,
                    )
                    equipment_type = self._equipment_catalog.classify(
                        ifc_type=ifc_type,
                        predefined_type=_predefined_type(element),
                        category=category,
                        name=name,
                        raw_name=raw_name,
                        object_type=object_type,
                    )

                    equipment_nodes_by_id[equipment_id] = EquipmentNode(
                        global_id=equipment_id,
                        ifc_type=ifc_type,
                        name=name,
                        raw_name=raw_name,
                        category=category,
                        is_read_only=is_read_only,
                        discipline=discipline,
                        storage_url=storage_url,
                        zone=zone,
                        equipment_type=equipment_type,
                    )

                    location_id = self._resolve_equipment_location(
                        element, room_ids=room_ids, floor_ids=floor_ids, zone=zone,
                    )
                    if location_id:
                        located_in_edges.add((equipment_id, location_id))

        return list(equipment_nodes_by_id.values()), located_in_edges

    def _resolve_equipment_location(
        self,
        element,
        *,
        room_ids: set[str],
        floor_ids: set[str],
        zone: str,
    ) -> str | None:
        """Rattache un équipement à son Room/Floor canonique.

        1. Correspondance LOCALE par GlobalId : fonctionne quand l'équipement
           et la structure spatiale sont dans le MÊME fichier IFC (ex: une
           maquette backbone qui contient elle-même des équipements).
        2. Repli CROSS-MAQUETTE par nom, scopé à la zone : c'est le cas normal
           pour une discipline équipement (CVC, ELEC...) ingérée séparément —
           le GlobalId local ne peut structurellement correspondre à rien de
           connu dans CE fichier, donc on cherche par nom un Room/Floor déjà
           créé par l'ingestion backbone de la MÊME zone.

        Le matching utilise raw_name (le code brut, ex: "101"), PAS name (le
        nom affiché après cascade, ex: "Bureau") — une maquette équipement
        référence en général le code de la pièce, pas son libellé descriptif.
        """
        for rel in getattr(element, "ContainedInStructure", None) or []:
            spatial = getattr(rel, "RelatingStructure", None)
            if not spatial:
                continue

            spatial_id = getattr(spatial, "GlobalId", None)
            if spatial_id and (spatial_id in room_ids or spatial_id in floor_ids):
                return spatial_id

            local_raw_name = _raw_name(spatial)
            if local_raw_name:
                canonical_id = self._lookup_canonical_location_by_name(zone=zone, raw_name=local_raw_name)
                if canonical_id:
                    return canonical_id
        return None

    def _lookup_canonical_location_by_name(self, *, zone: str, raw_name: str) -> str | None:
        """Cherche un Room, puis à défaut un Floor, déjà ingéré dans ArcadeDB
        pour cette zone avec ce raw_name exact. C'est le mécanisme qui remplace
        la correspondance par GlobalId quand équipement et backbone viennent de
        deux maquettes distinctes."""
        if not zone or not raw_name:
            return None

        escaped_zone = _cypher_escape(zone)
        escaped_raw_name = _cypher_escape(raw_name)
        for vertex_type in ("Room", "Floor"):
            sql = (
                f"SELECT global_id FROM {vertex_type} "
                f"WHERE zone = '{escaped_zone}' AND raw_name = '{escaped_raw_name}' LIMIT 1"
            )
            try:
                response = self._graph_repository.query_sql(sql)
            except Exception as exc:
                print(f"Warning: zone-scoped location lookup failed on {vertex_type}: {exc}", file=sys.stderr)
                continue

            rows = response.get("result") if isinstance(response, dict) else None
            if rows:
                global_id = rows[0].get("global_id")
                if global_id:
                    return global_id
        return None

    def _run_post_ingest_deduplication(self) -> None:
        """Detecte les global_id dupliqués dans ArcadeDB, fusionne conservativement
        les propriétés et ré-attache les arêtes vers un seul noeud maître.

        ArcadeDB ne définit PAS de supertype générique V/E interrogeable en SQL
        (chaque label Cypher devient son propre type racine : "FROM V"/"FROM E"
        échouent avec SchemaException "Type with name 'V' was not found") : il
        faut énumérer les types concrets via schema:types. Le dialecte SQL
        d'ArcadeDB ne supporte pas non plus HAVING après GROUP BY (erreur de
        parsing) : on filtre via une sous-requête.
        """
        try:
            vertex_types = self._list_schema_type_names("vertex")
            edge_types = self._list_schema_type_names("edge")
        except Exception as exc:
            print(f"Dedup: schema introspection failed: {exc}", file=sys.stderr)
            return

        for vertex_type in vertex_types:
            self._dedup_vertex_type(vertex_type, edge_types)

    def _list_schema_type_names(self, kind: str) -> list[str]:
        resp = self._graph_repository.query_sql(f"SELECT name FROM schema:types WHERE type = '{kind}'")
        rows = resp.get("result") if isinstance(resp, dict) else None
        return [r["name"] for r in (rows or []) if r.get("name")]

    def _dedup_vertex_type(self, vertex_type: str, edge_types: list[str]) -> None:
        # 1) trouver les global_id dupliqués DANS CE TYPE (sous-requête : pas de HAVING)
        sql_find = (
            f"SELECT * FROM (SELECT global_id, count(*) AS c FROM {vertex_type} "
            f"GROUP BY global_id) WHERE c > 1"
        )
        try:
            resp = self._graph_repository.query_sql(sql_find)
        except Exception as exc:
            print(f"Dedup: query failed on {vertex_type}: {exc}", file=sys.stderr)
            return

        rows = resp.get("result") if isinstance(resp, dict) else None
        if not rows:
            return

        duplicated_gids = [r.get("global_id") for r in rows if r.get("global_id")]
        if not duplicated_gids:
            return

        for gid in duplicated_gids:
            # fetch vertices with this global_id
            # ArcadeDB rejette "SELECT @rid AS rid, *" (mélange alias + étoile) ;
            # "SELECT *" expose déjà @rid nativement dans chaque ligne.
            sql_list = (
                f"SELECT * FROM {vertex_type} "
                f"WHERE global_id = '{_cypher_escape(gid)}' ORDER BY @rid"
            )
            try:
                listing = self._graph_repository.query_sql(sql_list).get("result", [])
            except Exception as exc:
                print(f"Dedup: failed listing for {gid} ({vertex_type}): {exc}", file=sys.stderr)
                continue

            if len(listing) < 2:
                continue

            keeper = listing[0]
            keeper_rid = keeper.get("@rid")
            donors = listing[1:]

            # For each donor: reattach edges and merge simple properties, then delete donor
            for donor in donors:
                donor_rid = donor.get("@rid")
                # reattach edges using SQL updates, un type d'arête à la fois
                for edge_type in edge_types:
                    try:
                        self._graph_repository.query_sql(
                            f"UPDATE {edge_type} SET out = {keeper_rid} WHERE out = {donor_rid}"
                        )
                        self._graph_repository.query_sql(
                            f"UPDATE {edge_type} SET in = {keeper_rid} WHERE in = {donor_rid}"
                        )
                    except Exception as exc:
                        print(
                            f"Dedup: edge reattach failed on {edge_type} for {donor_rid}: {exc}",
                            file=sys.stderr,
                        )

                # conservative property merge: set keeper props when they are empty
                set_parts = []
                for k, v in donor.items():
                    # ArcadeDB préfixe ses champs système avec "@" (@rid, @type, @cat...)
                    if k.startswith("@") or k == "global_id":
                        continue
                    if v in (None, "", "null", "default", "none"):
                        continue
                    # only set if keeper has no value for that key
                    # we check keeper's value
                    if not keeper.get(k):
                        if isinstance(v, str):
                            val = _cypher_escape(str(v))
                            set_parts.append(f"{k} = '{val}'")
                        else:
                            set_parts.append(f"{k} = {v}")

                if set_parts:
                    sql_update = f"UPDATE {keeper_rid} SET {', '.join(set_parts)}"
                    try:
                        self._graph_repository.query_sql(sql_update)
                    except Exception as exc:
                        print(f"Dedup: property merge failed for {keeper_rid}: {exc}", file=sys.stderr)

                # finally delete donor
                try:
                    sql_del = f"DELETE FROM {vertex_type} WHERE @rid = {donor_rid}"
                    self._graph_repository.query_sql(sql_del)
                except Exception as exc:
                    print(f"Dedup: delete failed for {donor_rid}: {exc}", file=sys.stderr)


def _clean_name(raw: str | None) -> str:
    """Normalise les noms et filtre les placeholders fréquents (null, none, default, n/a).
    Retourne une chaîne vide si le nom n'est pas exploitable.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    lowered = s.lower()
    if lowered in ("null", "none", "default", "n/a", "na"):
        return ""
    if lowered in ("-", "?"):
        return ""
    return s


def _non_empty_name(raw: str | None, *, fallback: str) -> str:
    cleaned = _clean_name(raw)
    if cleaned:
        return cleaned

    fallback_cleaned = _clean_name(fallback)
    if fallback_cleaned:
        return fallback_cleaned

    return "unnamed"


def _cascade_display_name(ifc_entity, *, fallback: str) -> str:
    """Nom affiché : LongName > Name > ObjectType > Tag > repli (label/ifc_type).

    LongName est spécifique aux éléments spatiaux (Site/Building/Storey/Space) ;
    Tag est spécifique aux IfcElement (équipements). getattr(..., None) renvoie
    silencieusement None quand l'attribut n'existe pas sur ce type d'entité, donc
    une seule cascade sert tous les types de nœuds (Building, Site, Room, Equipment).
    """
    for candidate in (
        getattr(ifc_entity, "LongName", None),
        getattr(ifc_entity, "Name", None),
        getattr(ifc_entity, "ObjectType", None),
        getattr(ifc_entity, "Tag", None),
    ):
        cleaned = _clean_name(candidate)
        if cleaned:
            return cleaned
    return _clean_name(fallback) or "unnamed"


def _raw_name(ifc_entity) -> str:
    """Code brut original (attribut Name), distinct du nom affiché — utilisé
    pour la logique de correspondance cross-maquette (Objectif 1), jamais pour
    l'affichage."""
    return _clean_name(getattr(ifc_entity, "Name", None))


def _predefined_type(ifc_entity) -> str:
    """PredefinedType IFC (ex: 'TEMPERATURESENSOR' sur un IfcSensor) --
    signal structuré pour le catalogue de simulation par type d'équipement,
    plus fiable qu'un mot-clé dans le nom mais souvent absent des exports
    réels. Retourne toujours une chaîne (jamais None) : le catalogue traite
    lui-même les valeurs vides/NOTDEFINED/USERDEFINED comme "pas de signal"."""
    value = getattr(ifc_entity, "PredefinedType", None)
    return str(value) if value else ""


def _to_spatial_node(ifc_entity, label: str, *, discipline: str = "", storage_url: str = "", zone: str = "") -> SpatialNode:
    global_id = getattr(ifc_entity, "GlobalId", None)
    if not global_id:
        raise ValueError(f"{label} entity without GlobalId cannot be ingested")
    ifc_type = ifc_entity.is_a() if hasattr(ifc_entity, "is_a") else ""
    return SpatialNode(
        label=label,
        global_id=global_id,
        name=_cascade_display_name(ifc_entity, fallback=label),
        raw_name=_raw_name(ifc_entity),
        discipline=discipline,
        ifc_type=ifc_type,
        storage_url=storage_url,
        zone=zone,
    )


def _extract_has_child_edges(model) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    allowed_parents = {"IfcSite", "IfcBuilding", "IfcBuildingStorey"}
    allowed_children = {"IfcBuilding", "IfcBuildingStorey", "IfcSpace"}

    for rel in model.by_type("IfcRelAggregates"):
        parent = getattr(rel, "RelatingObject", None)
        children = getattr(rel, "RelatedObjects", None) or []
        parent_id = getattr(parent, "GlobalId", None)
        if not parent_id or parent.is_a() not in allowed_parents:
            continue

        for child in children:
            child_id = getattr(child, "GlobalId", None)
            if child_id and child.is_a() in allowed_children:
                edges.add((parent_id, child_id))

    return edges


def _map_room_to_floor(model) -> dict[str, str]:
    room_to_floor: dict[str, str] = {}

    for rel in model.by_type("IfcRelAggregates"):
        parent = getattr(rel, "RelatingObject", None)
        children = getattr(rel, "RelatedObjects", None) or []
        if not parent or not parent.is_a("IfcBuildingStorey"):
            continue

        floor_id = getattr(parent, "GlobalId", None)
        if not floor_id:
            continue

        for child in children:
            if child.is_a("IfcSpace"):
                room_id = getattr(child, "GlobalId", None)
                if room_id:
                    room_to_floor[room_id] = floor_id

    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        container = getattr(rel, "RelatingStructure", None)
        if not container or not container.is_a("IfcBuildingStorey"):
            continue

        floor_id = getattr(container, "GlobalId", None)
        if not floor_id:
            continue

        for element in getattr(rel, "RelatedElements", None) or []:
            if element.is_a("IfcSpace"):
                room_id = getattr(element, "GlobalId", None)
                if room_id and room_id not in room_to_floor:
                    room_to_floor[room_id] = floor_id

    return room_to_floor


GENERIC_OCCUPANCY_VALUES = {"other", "notdefined", "not defined", "unset", "unknown", "n/a", "na", ""}


def _get_occupancy_type(raw_room) -> str:
    """Lit Pset_SpaceCommon.OccupancyType sur l'entité IFC brute. Isolé dans une
    fonction dédiée (import local d'ifcopenshell.util.element) pour ne pas
    étaler cette dépendance dans tout le fichier."""
    if raw_room is None:
        return ""
    try:
        import ifcopenshell.util.element as ifc_util
        psets = ifc_util.get_psets(raw_room)
    except Exception:
        return ""
    value = psets.get("Pset_SpaceCommon", {}).get("OccupancyType")
    return str(value).strip() if value else ""


def _resolve_room_typology(raw_room, *, name: str, raw_name: str = "") -> str:
    """1. Si Pset_SpaceCommon.OccupancyType est renseigné avec une valeur
    SPÉCIFIQUE (pas "other"/"notdefined"/vide...), on la prend telle quelle :
    c'est la source la plus fiable, une déclaration explicite du BIM manager.
    2. Sinon (valeur générique ou absente), on déduit par mots-clés bilingues
    FR/EN, cherchés dans le nom brut ET le nom affiché (une info utile peut
    être dans le code IFC plutôt que dans le LongName, ou l'inverse)."""
    occupancy_type = _get_occupancy_type(raw_room)
    if occupancy_type and occupancy_type.lower() not in GENERIC_OCCUPANCY_VALUES:
        return occupancy_type

    combined = f"{raw_name} {name}".lower()
    for typology, keywords in ROOM_TYPLOGY_KEYWORDS.items():
        if any(token in combined for token in keywords):
            return typology
    return "other"


def _clean_typology_name(typology_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", typology_name.lower()).strip("_")
    return cleaned or "other"


def _safe_by_type(model, ifc_type: str):
    """Return entities for a type, or an empty list if the schema does not define it."""
    try:
        return model.by_type(ifc_type)
    except Exception:
        return []


def _is_fire_or_ssi_equipment(*, ifc_type: str, name: str, object_type: str) -> bool:
    combined = f"{ifc_type} {name} {object_type}".lower()
    return any(token in combined for token in FIRE_SECURITY_TOKENS)


def _merge_spatial_node_query(node: SpatialNode) -> str:
    """MERGE avec ON CREATE SET / ON MATCH SET via map de propriétés.

    En Cypher, MERGE ne pose QUE les propriétés présentes dans le pattern de
    correspondance (ici global_id). Toute propriété supplémentaire doit être
    ajoutée explicitement avec SET sur une map, sinon elle est silencieusement
    perdue à la création.
    """
    return _upsert_node_query(
        node.label,
        node.global_id,
        {
            "name": node.name,
            "raw_name": node.raw_name,
            "discipline": node.discipline,
            "zone": node.zone,
            "ifc_type": node.ifc_type,
            "storage_url": node.storage_url,
        },
    )


def _upsert_node_query(label: str, global_id: str, properties: dict[str, str | bool]) -> str:
    property_map = _build_cypher_property_map(properties)
    return (
        f"MERGE (n:{label} {{global_id: '{_cypher_escape(global_id)}'}})"
        f" ON CREATE SET n += {property_map}"
        f" ON MATCH SET n += {property_map}"
    )


def _build_cypher_property_map(properties: dict[str, str | bool]) -> str:
    parts: list[str] = []
    for key, value in properties.items():
        if isinstance(value, bool):
            rendered_value = str(value).lower()
        else:
            rendered_value = f"'{_cypher_escape(str(value))}'"
        parts.append(f"{key}: {rendered_value}")
    return "{" + ", ".join(parts) + "}"


def _merge_typology_node_query(global_id: str, name: str, *, zone: str = "") -> str:
    """MERGE avec ON CREATE SET / ON MATCH SET pour garantir la propriété name."""
    cleaned = _non_empty_name(name, fallback="Typology")
    return _upsert_node_query("Typology", global_id, {"name": cleaned, "zone": zone})


def _merge_equipment_node_query(node: EquipmentNode) -> str:
    """MERGE avec ON CREATE SET / ON MATCH SET via map de propriétés."""
    return _upsert_node_query(
        "Equipment",
        node.global_id,
        {
            "name": node.name,
            "raw_name": node.raw_name,
            "ifc_type": node.ifc_type,
            "category": node.category,
            "equipment_type": node.equipment_type,
            "is_read_only": node.is_read_only,
            "discipline": node.discipline,
            "zone": node.zone,
            "storage_url": node.storage_url,
        },
    )


def _merge_edge_query(relation: str, source_global_id: str, target_global_id: str) -> str:
    return (
        f"MATCH (a {{global_id: '{_cypher_escape(source_global_id)}'}}), "
        f"(b {{global_id: '{_cypher_escape(target_global_id)}'}}) "
        f"MERGE (a)-[:{relation}]->(b)"
    )


def _cypher_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def iter_chunks(items: Iterable[str], size: int) -> Iterable[list[str]]:
    """Utility kept for future unit tests and command-level observability hooks."""
    if size <= 0:
        raise ValueError("size must be greater than 0")

    chunk: list[str] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
