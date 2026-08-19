"""
Couche APPLICATION (orchestration / use case)
-----------------------------------------------
Orchestre les ports et les règles métier, MAINTENANT PAR DISCIPLINE.
La liste des types critiques et les Psets requis ne sont plus figés dans le
code : ils viennent de la charte, résolue pour la discipline de la maquette
en cours (ARCHI, CVC, PLOMBERIE, ELEC...).

Pour migrer d'ifcopenshell vers web-ifc plus tard : on change UNIQUEMENT
l'adapter injecté au moment de l'instanciation (voir main.py), ce fichier
ne bouge pas.
"""

from domain.entities import ValidationReport, ValidationIssue, Severity
from domain.ports import IfcModelReaderPort, ComplianceRulesRepositoryPort, IfcLoadError
from domain import rules


class BimValidatorService:

    def __init__(
        self,
        reader: IfcModelReaderPort,
        rules_repository: ComplianceRulesRepositoryPort,
    ):
        self._reader = reader
        self._rules_repository = rules_repository

    def validate(self, file_path: str, discipline: str) -> ValidationReport:
        # --- Discipline reconnue par la charte ? ---
        known_disciplines = self._rules_repository.list_disciplines()
        if discipline.upper() not in known_disciplines:
            return ValidationReport(
                success=False,
                status=422,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    code="UNKNOWN_DISCIPLINE",
                    message=f"Discipline '{discipline}' non reconnue. "
                            f"Disciplines supportées : {known_disciplines}.",
                )],
            )

        # --- Chargement (équivalent Étape 1 du flowchart) ---
        try:
            self._reader.load(file_path)
        except IfcLoadError as e:
            return ValidationReport(
                success=False,
                status=422,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    code="LOAD_FAILED",
                    message=str(e),
                )],
            )

        schema_version = self._reader.get_schema_version()
        issues = []

        # --- Règles résolues POUR CETTE DISCIPLINE ---
        supported_schemas = self._rules_repository.get_supported_schemas(discipline)
        business_critical_types = self._rules_repository.get_business_critical_types(discipline)
        required_psets = self._rules_repository.get_required_property_sets(discipline)

        # --- Schéma supporté ---
        issues += rules.check_schema_supported(schema_version, supported_schemas)

        # --- Unités ---
        issues += rules.check_units(self._reader.get_units())

        # --- Structure spatiale (générique, indépendante de la discipline) ---
        
        required_spatial_elements = self._rules_repository.get_required_spatial_elements(discipline)
        spatial_summary = self._reader.get_spatial_structure_summary()
        issues += rules.check_spatial_structure(spatial_summary, required_spatial_elements)
        issues += rules.check_geospatial_origin(spatial_summary)

        # --- GUID (format + doublons, sur TOUS les éléments racine) ---
        all_root_elements = self._reader.get_all_root_elements()
        issues += rules.check_valid_guid_format(all_root_elements)
        issues += rules.check_duplicate_guids(all_root_elements)

        # --- Éléments métier critiques DE CETTE DISCIPLINE : orphelins + Psets requis + géométrie ---
        business_elements = self._reader.get_elements(business_critical_types)
        issues += rules.check_orphan_elements(business_elements)
        issues += rules.check_required_property_sets(business_elements, required_psets)
        issues += rules.check_geometry_presence(business_elements)
        issues += rules.check_placement_consistency(business_elements)
        issues += rules.check_absolute_position(business_elements)
        issues += rules.check_relationship_presence(business_elements)
        issues += rules.check_required_name(business_elements)

        has_blocking_errors = any(i.severity == Severity.ERROR for i in issues)

        return ValidationReport(
            success=not has_blocking_errors,
            status=422 if has_blocking_errors else 200,
            schema_version=schema_version,
            issues=issues,
        )
