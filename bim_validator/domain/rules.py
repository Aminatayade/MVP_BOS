"""
Couche MÉTIER (Rules)
----------------------
Chaque règle est une fonction pure : elle prend des objets du domaine en entrée
et retourne une liste de ValidationIssue. Aucune dépendance à ifcopenshell.
Ça les rend testables unitairement sans jamais charger un vrai fichier IFC
(on peut construire des BimElement à la main dans les tests).
"""

from .entities import BimElement, SpatialStructureSummary, ModelUnits, ValidationIssue, Severity

GUID_EXPECTED_LENGTH = 22
ACCEPTED_LENGTH_UNITS = {"METRE", "MILLIMETRE", "CENTIMETRE"}


def check_valid_guid_format(elements: list[BimElement]) -> list[ValidationIssue]:
    issues = []
    for el in elements:
        if not el.global_id or len(el.global_id) != GUID_EXPECTED_LENGTH:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="INVALID_GUID",
                message=f"Élément [{el.ifc_type}] #{el.express_id} : "
                        f"GlobalId manquant ou invalide (doit faire {GUID_EXPECTED_LENGTH} caractères)."
            ))
    return issues


def check_duplicate_guids(all_root_elements: list[BimElement]) -> list[ValidationIssue]:
    seen: dict[str, BimElement] = {}
    issues = []
    for el in all_root_elements:
        if el.global_id in seen:
            other = seen[el.global_id]
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="DUPLICATE_GUID",
                message=f"GlobalId dupliqué '{el.global_id}' : "
                        f"présent sur [{other.ifc_type}] #{other.express_id} "
                        f"et [{el.ifc_type}] #{el.express_id}."
            ))
        else:
            seen[el.global_id] = el
    return issues


def check_orphan_elements(elements: list[BimElement]) -> list[ValidationIssue]:
    issues = []
    for el in elements:
        if not el.is_linked_to_spatial_structure:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="ORPHAN_ELEMENT",
                message=f"Élément orphelin : [{el.ifc_type}] {el.global_id} "
                        f"n'est rattaché à aucune structure spatiale (étage/pièce)."
            ))
    return issues


def check_spatial_structure(
    summary: SpatialStructureSummary,
    required_elements: list[str],
) -> list[ValidationIssue]:
    issues = []
    if "IfcSite" in required_elements and not summary.has_site:
        issues.append(ValidationIssue(
            severity=Severity.ERROR, code="MISSING_SITE",
            message="Prérequis métier manquant : aucun IfcSite défini.",
        ))
    if "IfcBuilding" in required_elements and not summary.has_building:
        issues.append(ValidationIssue(
            severity=Severity.ERROR, code="MISSING_BUILDING",
            message="Prérequis métier manquant : aucun IfcBuilding défini.",
        ))
    if "IfcBuildingStorey" in required_elements and not summary.has_storey:
        issues.append(ValidationIssue(
            severity=Severity.ERROR, code="MISSING_STOREY",
            message="Prérequis métier manquant : aucun IfcBuildingStorey défini.",
        ))
    return issues

def check_geospatial_origin(summary: SpatialStructureSummary) -> list[ValidationIssue]:
    """Non bloquant : une maquette peut être exploitée sans géoréférencement,
    mais ça dégrade la précision de recalage cartographique côté BOS."""
    issues = []
    if not summary.site_ref_latitude or not summary.site_ref_longitude:
        issues.append(ValidationIssue(
            severity=Severity.WARNING,
            code="MISSING_GEOREFERENCE",
            message="Origine géospatiale (IfcSite Lat/Long) non renseignée."
        ))
    return issues


def check_units(units: ModelUnits) -> list[ValidationIssue]:
    issues = []
    if units.length_unit is None:
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="MISSING_UNITS",
            message="Aucune unité de longueur définie (IfcUnitAssignment)."
        ))
    elif units.length_unit not in ACCEPTED_LENGTH_UNITS:
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="UNSUPPORTED_UNIT",
            message=f"Unité de longueur non supportée : '{units.length_unit}'. "
                    f"Attendu parmi {sorted(ACCEPTED_LENGTH_UNITS)}."
        ))
    return issues


def check_schema_supported(schema_version: str, supported_schemas: list[str]) -> list[ValidationIssue]:
    issues = []
    if schema_version not in supported_schemas:
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="UNSUPPORTED_SCHEMA",
            message=f"Schéma IFC '{schema_version}' non supporté par le pipeline. "
                    f"Schémas acceptés : {supported_schemas}."
        ))
    return issues


def check_required_property_sets(
    elements: list[BimElement],
    required_psets_by_type: dict[str, list[str]],
) -> list[ValidationIssue]:
    """
    required_psets_by_type ex:
        {"IfcDistributionFlowElement": ["Pset_EquipmentCommon.Manufacturer"]}
    Chaque entrée est "NomPset.NomAttribut".
    """
    issues = []
    for el in elements:
        required = required_psets_by_type.get(el.ifc_type, [])
        for requirement in required:
            pset_name, _, attr_name = requirement.partition(".")
            pset = el.property_sets.get(pset_name)
            value = pset.get(attr_name) if pset else None
            if value in (None, ""):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="MISSING_PROPERTY",
                    message=f"Élément [{el.ifc_type}] {el.global_id} : "
                            f"propriété requise '{requirement}' absente ou vide "
                            f"(charte BIM)."
                ))
    return issues


def check_geometry_presence(elements: list[BimElement]) -> list[ValidationIssue]:
    """Vérifie que les éléments métier ont au moins une géométrie exploitable pour le rendu 3D."""
    issues = []
    for el in elements:
        if not getattr(el, "has_geometry", False):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="MISSING_GEOMETRY",
                message=f"Élément [{el.ifc_type}] {el.global_id} : aucune géométrie exploitable détectée, "
                        f"ce qui empêche son rendu 3D dans un moteur IFC."
            ))
    return issues


def check_placement_consistency(elements: list[BimElement]) -> list[ValidationIssue]:
    """Vérifie que les éléments métier ont un placement IFC valide dans le référentiel du projet."""
    issues = []
    for el in elements:
        if not getattr(el, "has_valid_placement", False):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="MISSING_PLACEMENT",
                message=f"Élément [{el.ifc_type}] {el.global_id} : aucun placement IFC valide détecté, "
                        f"ce qui risque de provoquer un rendu 3D incohérent ou hors référentiel."
            ))
    return issues


def check_absolute_position(elements: list[BimElement], max_distance: float = 100000.0) -> list[ValidationIssue]:
    """Vérifie que les éléments ne sont pas trop éloignés du point d’origine du modèle."""
    issues = []
    for el in elements:
        position = getattr(el, "absolute_position", None)
        if not position:
            continue
        distance = sum(abs(coord) for coord in position)
        if distance > max_distance:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="OUT_OF_ORIGIN",
                message=f"Élément [{el.ifc_type}] {el.global_id} : position absolue trop éloignée de l'origine du modèle "
                        f"({position}), risque de rendu 3D incohérent."
            ))
    return issues


def check_relationship_presence(elements: list[BimElement]) -> list[ValidationIssue]:
    """Vérifie que les éléments métier ont au moins une relation IFC utile pour l'exploitation du modèle."""
    issues = []
    for el in elements:
        if not getattr(el, "has_relationships", False):
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                code="MISSING_RELATIONSHIP",
                message=f"Élément [{el.ifc_type}] {el.global_id} : aucune relation IFC utile détectée, "
                        f"ce qui peut limiter la compréhension du réseau ou de l’assemblage."
            ))
    return issues


def check_required_name(elements: list[BimElement]) -> list[ValidationIssue]:
    """Vérifie que les éléments métier ont un nom exploitable pour la navigation et l'identification."""
    issues = []
    for el in elements:
        if not getattr(el, "name", None):
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                code="MISSING_NAME",
                message=f"Élément [{el.ifc_type}] {el.global_id} : nom IFC absent, ce qui réduit l’identification et la traçabilité."
            ))
    return issues
