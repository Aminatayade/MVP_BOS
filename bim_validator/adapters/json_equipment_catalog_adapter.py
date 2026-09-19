"""
Couche ADAPTER
---------------
Charge le catalogue de simulation par type d'équipement depuis un fichier
JSON versionné (bim_validator/config/equipment_simulation_catalog.json),
même convention que JsonRulesRepositoryAdapter/charte_bim_rules.json --
deux fichiers de config distincts car deux responsabilités distinctes :
la charte gère la CONFORMITÉ (par discipline), ce catalogue gère la
SIMULATION (par type d'équipement).

Cascade de classification (voir classify()) :
    1. PredefinedType IFC (signal structuré, le plus fiable)
    2. Mots-clés sur le nom/tag (repli quand PredefinedType est absent ou
       NOTDEFINED, cas fréquent sur des exports réels)
    3. Repli sur la catégorie large déjà connue du pipeline
       (EQUIPMENT_CATEGORIES) -- garantit qu'aucun équipement ne reste sans
       classification et qu'aucune ingestion ne bloque dessus.
"""

import json

from domain.entities import EquipmentSimulationSpec, MetricSpec
from domain.ports import EquipmentCatalogPort

# PredefinedType IFC "vides" : un exporteur qui n'a pas renseigné le champ
# retombe sur l'une de ces valeurs, à traiter comme une absence de signal.
_UNSET_PREDEFINED_TYPES = {"", "NOTDEFINED", "USERDEFINED"}


class JsonEquipmentCatalogAdapter(EquipmentCatalogPort):
    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = json.load(f)
        self._equipment_types: dict = self._config.get("equipment_types", {})
        self._category_fallbacks: dict = self._config.get("category_fallbacks", {})

    def classify(
        self,
        *,
        ifc_type: str,
        predefined_type: str | None,
        category: str,
        name: str,
        raw_name: str,
        object_type: str,
    ) -> str:
        # 1. PredefinedType IFC.
        if predefined_type and predefined_type.upper() not in _UNSET_PREDEFINED_TYPES:
            predefined_upper = predefined_type.upper()
            for key, spec in self._equipment_types.items():
                allowed = spec.get("predefined_types", {}).get(ifc_type, [])
                if predefined_upper in [v.upper() for v in allowed]:
                    return key

        # 2. Mots-clés sur le nom/tag brut.
        haystack = " ".join(filter(None, [name, raw_name, object_type])).lower()
        if haystack.strip():
            for key, spec in self._equipment_types.items():
                for keyword in spec.get("keywords", []):
                    if keyword and keyword.lower() in haystack:
                        return key

        # 3. Repli sur la catégorie large -- toujours résolu si `category`
        # est l'une des clés connues d'EQUIPMENT_CATEGORIES.
        fallback = self._category_fallbacks.get(category)
        if fallback and fallback in self._equipment_types:
            return fallback

        # Filet ultime (catégorie inconnue du catalogue) : aucune métrique,
        # équivalent à "pas de télémétrie simulée" plutôt qu'une exception.
        return "_unknown"

    def get_generator_spec(self, equipment_type: str) -> EquipmentSimulationSpec:
        if equipment_type == "_unknown":
            return EquipmentSimulationSpec(equipment_type="_unknown", label="Non classifié", metrics=[])

        spec = self._equipment_types[equipment_type]
        metrics = [
            MetricSpec(
                name=m["name"],
                unit=m.get("unit", ""),
                generator=m["generator"],
                params=m.get("params", {}),
                value_labels=m.get("value_labels"),
            )
            for m in spec.get("metrics", [])
        ]
        history = spec.get("history", {})
        # Quand un type d'équipement définit son propre pas de détection
        # (ex: détecteur de présence), c'est LUI qui fixe points_per_day --
        # jamais les deux valeurs renseignées indépendamment, ce qui
        # permettrait de les faire diverger silencieusement.
        detection_interval_seconds = spec.get("detection_interval_seconds")
        points_per_day = (
            max(1, round(86400 / detection_interval_seconds))
            if detection_interval_seconds
            else history.get("points_per_day", 24)
        )
        return EquipmentSimulationSpec(
            equipment_type=equipment_type,
            label=spec.get("label", equipment_type),
            metrics=metrics,
            history_days=history.get("days", 7),
            points_per_day=points_per_day,
            detection_interval_seconds=detection_interval_seconds,
        )

    def all_metric_names(self) -> list[str]:
        names: list[str] = []
        for spec in self._equipment_types.values():
            for metric in spec.get("metrics", []):
                if metric["name"] not in names:
                    names.append(metric["name"])
        return names
