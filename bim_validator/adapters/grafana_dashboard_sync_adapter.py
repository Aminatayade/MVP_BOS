"""
Couche ADAPTER
---------------
Resynchronise les variables du dashboard Grafana "cockpit équipements" avec
l'état courant du système -- voir scripts/sync_grafana_dashboard_variables.py
pour le contexte complet (pourquoi ces variables sont statiques et pas des
variables Grafana dynamiques).

Réutilise BimGraphRepositoryPort.query_sql (déjà injecté partout ailleurs
dans le pipeline d'ingestion) plutôt que de parler à ArcadeDB directement --
cet adapter n'a besoin d'aucune connaissance du transport ArcadeDB.
"""

from __future__ import annotations

import requests

from domain.ports import BimGraphRepositoryPort, EquipmentCatalogPort, GrafanaDashboardSyncPort

# Libellés FR pour les métriques du catalogue -- une métrique absente de
# cette table retombe sur son nom brut (snake_case -> "Title Case").
_METRIC_LABELS = {
    "temperature": "Température",
    "temperature_depart": "Température de départ",
    "setpoint_temperature": "Consigne de température",
    "co2": "CO2",
    "occupancy": "Occupation",
    "humidity": "Humidité",
    "luminosity": "Luminosité",
    "state": "État",
    "position_percent": "Position (%)",
    "energy_cumulative_kwh": "Énergie cumulée (kWh)",
    "power_instant_kw": "Puissance instantanée (kW)",
    "volume_cumulative_m3": "Volume cumulé (m³)",
    "battery_charge_percent": "Charge batterie (%)",
    "badge_count": "Badgeages",
    "connected_clients": "Clients connectés",
}


def _metric_label(name: str) -> str:
    return _METRIC_LABELS.get(name, name.replace("_", " ").title())


class GrafanaDashboardSyncAdapter(GrafanaDashboardSyncPort):
    def __init__(
        self,
        *,
        grafana_url: str,
        grafana_user: str,
        grafana_password: str,
        dashboard_uid: str,
        graph_repository: BimGraphRepositoryPort,
        equipment_catalog: EquipmentCatalogPort,
        timeout_seconds: int = 30,
    ) -> None:
        self._grafana_url = grafana_url.rstrip("/")
        self._auth = (grafana_user, grafana_password)
        self._dashboard_uid = dashboard_uid
        self._graph_repository = graph_repository
        self._equipment_catalog = equipment_catalog
        self._timeout_seconds = timeout_seconds

    def sync_variables(self) -> None:
        location_options = self._build_location_options()
        if not location_options:
            # Aucun Room/Floor en base -- rien de pertinent à synchroniser
            # (ex: maquette architecture pas encore ingérée).
            return
        metric_options = self._build_metric_options()

        get_resp = requests.get(
            f"{self._grafana_url}/api/dashboards/uid/{self._dashboard_uid}",
            auth=self._auth, timeout=self._timeout_seconds,
        )
        get_resp.raise_for_status()
        payload = get_resp.json()
        dashboard = payload["dashboard"]

        for variable in dashboard.get("templating", {}).get("list", []):
            if variable.get("name") == "location":
                self._set_variable(variable, location_options)
            elif variable.get("name") == "metric":
                self._set_variable(variable, metric_options)

        save_resp = requests.post(
            f"{self._grafana_url}/api/dashboards/db",
            json={
                "dashboard": dashboard,
                "overwrite": True,
                "folderUid": payload.get("meta", {}).get("folderUid", ""),
            },
            auth=self._auth, timeout=self._timeout_seconds,
        )
        save_resp.raise_for_status()

    @staticmethod
    def _set_variable(variable: dict, options: str) -> None:
        variable["query"] = options
        label, value = options.split(",")[0].split(" : ")
        variable["current"] = {"text": label, "value": value}

    def _build_location_options(self) -> str:
        rooms = self._graph_repository.query_sql("SELECT global_id, name FROM Room").get("result", [])
        floors = self._graph_repository.query_sql("SELECT global_id, name FROM Floor").get("result", [])
        entries = rooms + floors
        return ",".join(
            f"{row['name']} : {row['global_id']}" for row in entries if row.get("global_id") and row.get("name")
        )

    def _build_metric_options(self) -> str:
        names = self._equipment_catalog.all_metric_names()
        # "temperature" en tête (métrique la plus universelle, meilleur
        # défaut pour la sélection initiale du dashboard).
        ordered = sorted(names, key=lambda name: (name != "temperature", name))
        return ",".join(f"{_metric_label(name)} : {name}" for name in ordered)
