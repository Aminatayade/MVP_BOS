"""
Couche MÉTIER (Domain)
----------------------
Ce module ne connaît RIEN d'ifcopenshell, de web-ifc ou de tout autre moteur.
Il ne parle que le langage fonctionnel : éléments, hiérarchie, rapport de conformité.

C'est ce qui garantit que si demain on remplace ifcopenshell par web-ifc (ou un
autre moteur), ce fichier ne bouge pas d'une ligne.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"       # bloquant : la maquette n'entre pas dans le pipeline
    WARNING = "warning"    # non bloquant : la maquette entre, mais on garde une trace


@dataclass(frozen=True)
class BimElement:
    """Représentation neutre d'un élément IFC, peu importe le moteur qui l'a lu."""
    global_id: str
    ifc_type: str          # ex: "IfcSpace", "IfcDistributionFlowElement"
    express_id: int         # identifiant interne au fichier (pour les messages d'erreur)
    is_linked_to_spatial_structure: bool
    property_sets: dict     # ex: {"Pset_EquipmentCommon": {"Manufacturer": "...", ...}}
    has_geometry: bool = False
    has_valid_placement: bool = False
    absolute_position: tuple[float, float, float] | None = None
    has_relationships: bool = False
    name: str | None = None



@dataclass(frozen=True)
class SpatialStructureSummary:
    """Résumé de la hiérarchie spatiale du bâtiment."""
    has_site: bool
    has_building: bool
    has_storey: bool
    site_ref_latitude: float | None
    site_ref_longitude: float | None

@dataclass(frozen=True)
class ModelUnits:
    length_unit: str | None   # ex: "METRE", "MILLIMETRE"


@dataclass
class ValidationIssue:
    severity: Severity
    code: str                # ex: "DUPLICATE_GUID", "MISSING_PSET"
    message: str


@dataclass
class ValidationReport:
    success: bool
    status: int
    schema_version: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "success": self.success,
            "schema_version": self.schema_version,
            "errors": [i.message for i in self.errors()],
            "warnings": [i.message for i in self.warnings()],
        }

    def to_human_text(self) -> str:
        lines = []
        if self.success:
            lines.append("✅ Validation réussie.")
        else:
            lines.append("❌ Validation rejetée.")

        if self.errors():
            lines.append("\nErreurs bloquantes :")
            for issue in self.errors():
                lines.append(f"- {issue.message}")
        else:
            lines.append("\nErreurs bloquantes : aucune")

        if self.warnings():
            lines.append("\nAvertissements :")
            for issue in self.warnings():
                lines.append(f"- {issue.message}")
        else:
            lines.append("\nAvertissements : aucun")

        lines.append("\nConseil : corrigez d’abord les erreurs bloquantes avant de relancer l’ingestion.")
        return "\n".join(lines)


@dataclass(frozen=True)
class MetricSpec:
    """Une métrique simulée pour un type d'équipement (catalogue de
    simulation) : quel générateur produit ses valeurs, avec quels
    paramètres. `value_labels` documente l'encodage numérique d'un état
    catégoriel (ex: {"0": "OK", "1": "Défaut"}) quand applicable."""
    name: str
    unit: str
    generator: str
    params: dict = field(default_factory=dict)
    value_labels: dict[str, str] | None = None


@dataclass(frozen=True)
class EquipmentSimulationSpec:
    """Spécification de simulation pour un type d'équipement du catalogue
    (bim_validator/config/equipment_simulation_catalog.json)."""
    equipment_type: str
    label: str
    metrics: list[MetricSpec]
    history_days: int = 7
    points_per_day: int = 24


@dataclass(frozen=True)
class UnifiedMessage:
    """Point de mesure normalisé reçu sur le canal live/historique (NATS),
    avant écriture dans le moteur time-series natif d'ArcadeDB. Le global_id
    doit correspondre à un Equipment déjà présent dans le graphe BIM."""
    global_id: str
    timestamp: datetime
    metric: str
    value: float
    unit: str = ""
    quality: str = "good"
    source: str = "unknown"
