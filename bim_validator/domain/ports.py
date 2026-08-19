"""
Couche REPOSITORY (Ports)
--------------------------
Interfaces abstraites. Le métier programme CONTRE ces interfaces, jamais contre
ifcopenshell directement. N'importe quel Adapter (ifcopenshell aujourd'hui,
web-ifc demain) peut les implémenter.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from .entities import BimElement, SpatialStructureSummary, ModelUnits, UnifiedMessage


class IfcModelReaderPort(ABC):
    """Contrat que doit respecter tout adapter capable de lire un fichier IFC."""

    @abstractmethod
    def load(self, file_path: str) -> None:
        """Charge le fichier. Doit lever une IfcLoadError si le fichier est invalide."""
        raise NotImplementedError

    @abstractmethod
    def get_schema_version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_elements(self, ifc_types: list[str]) -> list[BimElement]:
        """Retourne les éléments demandés, déjà traduits en objets du domaine."""
        raise NotImplementedError

    @abstractmethod
    def get_all_root_elements(self) -> list[BimElement]:
        """Retourne tous les éléments possédant un GlobalId (pour le contrôle de doublons)."""
        raise NotImplementedError

    @abstractmethod
    def get_spatial_structure_summary(self) -> SpatialStructureSummary:
        raise NotImplementedError

    @abstractmethod
    def get_units(self) -> ModelUnits:
        raise NotImplementedError


class IfcLoadError(Exception):
    """Levée par un adapter quand le fichier est introuvable, corrompu ou illisible."""
    pass


class StorageRepositoryPort(ABC):
    """Contrat pour archiver un fichier validé. SeaweedFS aujourd'hui,
    n'importe quel autre object storage (MinIO, S3...) demain sans changer
    le reste du pipeline."""

    @abstractmethod
    def store(self, local_file_path: str, remote_name: str) -> str:
        """Stocke le fichier et retourne son URL/référence publique."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, reference: str) -> None:
        """Supprime un fichier précédemment stocké (référence = URL/fid retourné par store).
        Utilisé pour purger un upload temporaire quand la maquette est rejetée."""
        raise NotImplementedError


class ComplianceRulesRepositoryPort(ABC):
    """Contrat pour charger les règles de la charte BIM, PAR DISCIPLINE
    (ARCHI, CVC, PLOMBERIE, ELEC...). Une maquette CVC n'a pas les mêmes
    règles de conformité qu'une maquette ARCHI — ce port permet à chaque
    discipline d'avoir son propre jeu de règles sans dupliquer de code."""

    @abstractmethod
    def list_disciplines(self) -> list[str]:
        """Disciplines connues de la charte (ex: ["ARCHI", "CVC", "PLOMBERIE", "ELEC"])."""
        raise NotImplementedError

    @abstractmethod
    def get_business_critical_types(self, discipline: str) -> list[str]:
        """Types IFC à inspecter pour cette discipline (orphelins + Psets requis)."""
        raise NotImplementedError

    @abstractmethod
    def get_required_property_sets(self, discipline: str) -> dict:
        """
        Retourne un dict du type :
        {
            "IfcDistributionFlowElement": ["Pset_EquipmentCommon.Manufacturer", ...]
        }
        propre à cette discipline.
        """
        raise NotImplementedError

    @abstractmethod
    def get_supported_schemas(self, discipline: str) -> list[str]:
        raise NotImplementedError
    @abstractmethod
    def get_required_spatial_elements(self, discipline: str) -> list[str]:
        """Types spatiaux (IfcBuilding, IfcBuildingStorey...) exigés pour cette
        discipline. Une maquette PLOMBERIE d'infrastructure peut légitimement
        n'avoir aucun étage, contrairement à une maquette ARCHI."""
        raise NotImplementedError

    @abstractmethod
    def canonical_discipline(self, discipline: str) -> str:
        """Résout un libellé brut (ex: 'Architecture', 'archi') vers sa forme
        canonique normalisée (ex: 'ARCHI'), via les alias de la charte. Seul
        point de résolution des alias — l'application ne doit jamais lire
        discipline_aliases directement."""
        raise NotImplementedError

    @abstractmethod
    def is_spatial_backbone_discipline(self, discipline: str) -> bool:
        """True si cette discipline DÉFINIT le squelette spatial (Site/Building/
        Floor/Room) ; False si elle ne fait qu'ATTACHER des équipements à un
        squelette existant. Lève KeyError si la discipline est inconnue de la
        charte (échec explicite plutôt que repli silencieux)."""
        raise NotImplementedError


class SeaweedStoragePort(ABC):
    """Contrat applicatif pour telecharger une maquette IFC depuis SeaweedFS."""

    @abstractmethod
    def download_ifc(
        self,
        object_key: str,
        *,
        bucket: str | None = None,
        target_path: str | None = None,
    ) -> str:
        """Telecharge une maquette IFC et retourne son chemin local."""
        raise NotImplementedError


class BimGraphRepositoryPort(ABC):
    """Contrat applicatif pour persister le graphe BIM dans ArcadeDB."""

    @abstractmethod
    def execute_cypher_batch(self, commands: list[str], batch_size: int = 500) -> None:
        """Execute des commandes OpenCypher en lots."""
        raise NotImplementedError

    @abstractmethod
    def query_sql(self, command: str) -> dict:
        """Execute a SQL command against ArcadeDB and return the parsed response."""
        raise NotImplementedError


class MessageQueuePort(ABC):
    """Contrat pour le canal d'injection live/historique (NATS JetStream
    aujourd'hui). Le métier ne connaît que publish/subscribe, jamais NATS
    directement."""

    @abstractmethod
    def publish(self, subject: str, message: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, subject: str, handler: Callable[[dict], None]) -> None:
        """Bloquant : consomme indéfiniment jusqu'à interruption du process."""
        raise NotImplementedError


class TimeseriesRepositoryPort(ABC):
    """Contrat pour la persistance des mesures dans le moteur time-series
    natif d'ArcadeDB (TAGS/FIELDS), distinct du graphe BIM (Equipment reste
    un vertex classique du graphe, interrogé via equipment_exists)."""

    @abstractmethod
    def ensure_schema(self) -> None:
        """Crée le type TIMESERIES s'il n'existe pas déjà (idempotent)."""
        raise NotImplementedError

    @abstractmethod
    def equipment_exists(self, global_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def write_point(self, message: UnifiedMessage) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_points_batch(self, messages: list[UnifiedMessage]) -> None:
        raise NotImplementedError

    @abstractmethod
    def query_readings(
        self,
        global_id: str,
        *,
        metric: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Lecture pour affichage (ex: courbes frontend). Filtre optionnel par
        métrique et fenêtre temporelle, triée par ts croissant."""
        raise NotImplementedError