"""
Couche ADAPTER
---------------
Seul fichier du projet qui importe ifcopenshell. Si demain tu migres vers
web-ifc (Node) ou un microservice séparé, tu écris un
`WebIfcAdapter(IfcModelReaderPort)` à côté de celui-ci, tu changes une ligne
dans main.py, et TOUT le reste (rules, service, entities) reste identique.
"""

import os
import ifcopenshell

from domain.entities import BimElement, SpatialStructureSummary, ModelUnits
from domain.ports import IfcModelReaderPort, IfcLoadError


class IfcOpenShellAdapter(IfcModelReaderPort):

    def __init__(self):
        self._model = None

    def load(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            raise IfcLoadError(f"Le fichier IFC spécifié est introuvable : {file_path}")
        try:
            self._model = ifcopenshell.open(file_path)
        except Exception as e:
            raise IfcLoadError(
                f"Structure IFC corrompue ou invalide. Impossible de parser le fichier. "
                f"Détails : {str(e)}"
            )

    def get_schema_version(self) -> str:
        return self._model.schema

    def _to_domain_element(self, el, linked_ids: set[str]) -> BimElement:
        psets = {}
        try:
            # ifcopenshell.util.element donne un accès pratique aux Psets,
            # mais on isole cet import ici : le domaine n'en a jamais connaissance.
            import ifcopenshell.util.element as ifc_util
            psets = ifc_util.get_psets(el)
        except Exception:
            psets = {}

        has_geometry = False
        has_valid_placement = False
        absolute_position = None
        has_relationships = False
        name = None
        try:
            representation = getattr(el, "Representation", None)
            has_geometry = representation is not None
        except Exception:
            has_geometry = False

        try:
            placement = getattr(el, "ObjectPlacement", None)
            if placement is not None:
                relative_placement = getattr(placement, "RelativePlacement", None)
                location = getattr(relative_placement, "Location", None)
                coordinates = getattr(location, "Coordinates", None)
                has_valid_placement = coordinates is not None and len(coordinates) >= 3
                if coordinates is not None:
                    absolute_position = tuple(float(c) for c in coordinates[:3])
        except Exception:
            has_valid_placement = False
            absolute_position = None

        try:
            relationships = getattr(el, "HasAssignments", None)
            has_relationships = relationships is not None and len(list(relationships)) > 0
        except Exception:
            has_relationships = False

        try:
            name = getattr(el, "Name", None)
        except Exception:
            name = None

        return BimElement(
            global_id=getattr(el, "GlobalId", None),
            ifc_type=el.is_a(),
            express_id=el.id(),
            is_linked_to_spatial_structure=getattr(el, "GlobalId", None) in linked_ids,
            property_sets=psets,
            has_geometry=has_geometry,
            has_valid_placement=has_valid_placement,
            absolute_position=absolute_position,
            has_relationships=has_relationships,
            name=name,
        )

    def _get_linked_ids(self) -> set[str]:
        linked = set()

        # Éléments PHYSIQUES (murs, équipements...) contenus DANS un espace/étage
        contained = self._model.by_type("IfcRelContainedInSpatialStructure")
        for rel in contained:
            for e in rel.RelatedElements:
                linked.add(e.GlobalId)

        # Éléments de STRUCTURE SPATIALE (IfcSpace, IfcBuildingStorey, IfcBuilding...)
        # rattachés à leur parent par DÉCOMPOSITION, pas par "contained in".
        # Ex: un étage se décompose en pièces via IfcRelAggregates.
        aggregates = self._model.by_type("IfcRelAggregates")
        for rel in aggregates:
            for e in rel.RelatedObjects:
                linked.add(e.GlobalId)

        return linked

    def get_elements(self, ifc_types: list[str]) -> list[BimElement]:
        """
        Retourne les éléments demandés en filtrant par id unique (el.id())
        pour éviter la duplication causée par le chaînage d'héritage en IFC.
        """
        linked_ids = self._get_linked_ids()
        elements_by_id = {}
        for ifc_type in ifc_types:
            try:
                candidates = self._model.by_type(ifc_type)
            except Exception:
                # Type non défini dans le schéma (ex: IfcElectricAppliance absent en IFC2X3)
                candidates = []

            for el in candidates:
                # express_id (el.id()) garantit l'unicité de chaque entité
                elements_by_id[el.id()] = self._to_domain_element(el, linked_ids)
                
        return list(elements_by_id.values())

    def _safe_by_type(self, ifc_type: str):
        """Retourne [] si le type n'existe pas dans le schéma chargé."""
        try:
            return self._model.by_type(ifc_type)
        except Exception:
            return []
    
    def get_all_root_elements(self) -> list[BimElement]:
        linked_ids = self._get_linked_ids()
        return [self._to_domain_element(el, linked_ids) for el in self._model.by_type("IfcRoot")]

    def get_spatial_structure_summary(self) -> SpatialStructureSummary:
        buildings = self._model.by_type("IfcBuilding")
        storeys = self._model.by_type("IfcBuildingStorey")
        sites = self._model.by_type("IfcSite")

        ref_lat, ref_long = None, None
        if sites:
            ref_lat = getattr(sites[0], "RefLatitude", None)
            ref_long = getattr(sites[0], "RefLongitude", None)

        return SpatialStructureSummary(
            has_site=bool(sites),
            has_building=bool(buildings),
            has_storey=bool(storeys),
            site_ref_latitude=ref_lat,
            site_ref_longitude=ref_long,
        )

    def get_units(self) -> ModelUnits:
        length_unit = None
        try:
            units_assignment = self._model.by_type("IfcUnitAssignment")
            if units_assignment:
                for unit in units_assignment[0].Units:
                    if getattr(unit, "UnitType", None) == "LENGTHUNIT":
                        length_unit = getattr(unit, "Name", None)
                        break
        except Exception:
            pass
        return ModelUnits(length_unit=length_unit)