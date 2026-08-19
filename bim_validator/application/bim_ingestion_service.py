"""
Couche APPLICATION
--------------------
Traduit exactement le losange "Maquette conforme ?" du flowchart :
  - NON -> on ne stocke rien, on retourne le rapport de non-conformité
  - OUI -> on archive le fichier dans SeaweedFS et on retourne l'URL de stockage

La validation tourne D'ABORD, sur le fichier local uniquement (aucune
interaction avec SeaweedFS à ce stade). SeaweedFS n'est touché que si la
maquette est conforme : pas de fichier temporaire, pas de purge à gérer,
pas de fenêtre où une maquette non conforme serait visible dans le stockage.

Ce service ne connaît ni ifcopenshell ni SeaweedFS directement : il compose
BimValidatorService (déjà écrit) et un StorageRepositoryPort injecté.
"""

import os
from dataclasses import dataclass

from domain.entities import ValidationReport
from domain.ports import StorageRepositoryPort
from application.bim_validator_service import BimValidatorService


@dataclass
class IngestionResult:
    validation: ValidationReport
    stored: bool
    storage_url: str | None = None
    storage_error: str | None = None

    def to_dict(self) -> dict:
        return {
            **self.validation.to_dict(),
            "stored_in_seaweedfs": self.stored,
            "storage_url": self.storage_url,
            "storage_error": self.storage_error,
        }


class BimIngestionService:

    def __init__(self, validator: BimValidatorService, storage: StorageRepositoryPort):
        self._validator = validator
        self._storage = storage

    def ingest(self, file_path: str, discipline: str) -> IngestionResult:
        # --- Étape 2 : contrôle de conformité, résolu pour cette discipline ---
        report = self._validator.validate(file_path, discipline)

        if not report.success:
            # Losange -> NON : on rejette, rien ne part vers SeaweedFS
            return IngestionResult(validation=report, stored=False)

        # Losange -> OUI : archivage dans SeaweedFS, préfixé par discipline
        # (ex: "cvc/maquette_ventilation.ifc") pour garder les lots séparés
        # côté stockage et faciliter le tri lors du mapping ArcadeDB (Étape 4).
        remote_name = f"{discipline.lower()}/{os.path.basename(file_path)}"
        try:
            storage_url = self._storage.store(file_path, remote_name)
        except Exception as e:
            return IngestionResult(
                validation=report,
                stored=False,
                storage_error=f"Maquette conforme mais échec du stockage SeaweedFS : {e}",
            )

        return IngestionResult(validation=report, stored=True, storage_url=storage_url)
