"""
Couche ADAPTER
---------------
Charge la charte BIM MULTI-DISCIPLINE depuis un fichier JSON versionné.
Structure attendue :
    {
      "common": { "supported_schemas": [...] },
      "disciplines": {
        "ARCHI": { "business_critical_types": [...], "required_property_sets": {...} },
        "CVC":   { ... },
        ...
      }
    }

Pour ajouter une discipline (ex: "SECURITE_INCENDIE") ou changer une règle,
on édite le JSON — pas ce code, ni le domaine.
"""

import json
import re
from domain.ports import ComplianceRulesRepositoryPort


class JsonRulesRepositoryAdapter(ComplianceRulesRepositoryPort):

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = json.load(f)
        self._disciplines = self._config.get("disciplines", {})
        self._common = self._config.get("common", {})
        self._aliases = {
            self._normalize(raw): self._normalize(target)
            for raw, target in self._config.get("discipline_aliases", {}).items()
        }

    @staticmethod
    def _normalize(discipline: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", discipline or "").upper()

    def canonical_discipline(self, discipline: str) -> str:
        normalized = self._normalize(discipline)
        return self._aliases.get(normalized, normalized)

    def list_disciplines(self) -> list[str]:
        return list(self._disciplines.keys())

    def _discipline_config(self, discipline: str) -> dict:
        key = self.canonical_discipline(discipline)
        if key not in self._disciplines:
            raise KeyError(
                f"Discipline '{discipline}' inconnue dans la charte BIM. "
                f"Disciplines disponibles : {self.list_disciplines()}"
            )
        return self._disciplines[key]

    def is_spatial_backbone_discipline(self, discipline: str) -> bool:
        return bool(self._discipline_config(discipline).get("defines_spatial_backbone", False))

    def get_business_critical_types(self, discipline: str) -> list[str]:
        return self._discipline_config(discipline).get("business_critical_types", [])

    def get_required_property_sets(self, discipline: str) -> dict:
        return self._discipline_config(discipline).get("required_property_sets", {})

    def get_supported_schemas(self, discipline: str) -> list[str]:
        # Les schémas supportés sont communs à toutes les disciplines par défaut,
        # mais une discipline peut les surcharger si besoin (ex: CVC autorise
        # un schéma que ARCHI n'autorise pas).
        discipline_cfg = self._discipline_config(discipline)
        return discipline_cfg.get("supported_schemas", self._common.get("supported_schemas", []))


    def get_required_spatial_elements(self, discipline: str) -> list[str]:
        return self._discipline_config(discipline).get(
            "required_spatial_elements", ["IfcBuilding", "IfcBuildingStorey"]
        )

    def canonical_disciplines(self, disciplines: list[str]) -> list[str]:
        """Résout chaque discipline (alias inclus), dédoublonne, trie pour la
        déterminisme de l'étiquette combinée et de l'ordre de traitement."""
        return sorted({self.canonical_discipline(d) for d in disciplines})

    def is_spatial_backbone_disciplines(self, disciplines: list[str]) -> bool:
        """Vrai si AU MOINS UNE des disciplines assignées construit le
        backbone spatial -- ex: un fichier CVC+STRUCTURE doit construire le
        backbone."""
        return any(self.is_spatial_backbone_discipline(d) for d in disciplines)

    def get_business_critical_types_union(self, disciplines: list[str]) -> list[str]:
        """Union dédoublonnée des types critiques de toutes les disciplines
        -- un fichier CVC+ELEC+PLOMBERIE est vérifié sur les 3 jeux de types
        à la fois, pas seulement un sous-ensemble arbitraire."""
        seen: list[str] = []
        for d in disciplines:
            for t in self.get_business_critical_types(d):
                if t not in seen:
                    seen.append(t)
        return seen

    def get_required_property_sets_union(self, disciplines: list[str]) -> dict:
        """Union par type IFC des Pset requis -- si deux disciplines
        exigent des Pset différents sur le MÊME type IFC, les deux jeux
        d'exigences s'appliquent (union des listes, pas écrasement)."""
        merged: dict[str, list[str]] = {}
        for d in disciplines:
            for ifc_type, psets in self.get_required_property_sets(d).items():
                bucket = merged.setdefault(ifc_type, [])
                for p in psets:
                    if p not in bucket:
                        bucket.append(p)
        return merged

    def get_required_spatial_elements_union(self, disciplines: list[str]) -> list[str]:
        """Union -- le fichier doit satisfaire l'exigence spatiale la plus
        stricte parmi ses disciplines assignées (ex: PLOMBERIE exige
        IfcSite seul, CVC exige Building+Storey -- combinés, les deux sont
        requis)."""
        seen: list[str] = []
        for d in disciplines:
            for e in self.get_required_spatial_elements(d):
                if e not in seen:
                    seen.append(e)
        return seen
