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

    def sync_variables(self, *, zone: str = "") -> None:
        location_options = self._build_location_options(zone=zone)
        if not location_options:
            # Aucun Room/Floor en base -- rien de pertinent à synchroniser
            # (ex: maquette architecture pas encore ingérée).
            return
        metric_options = self._build_metric_options(zone=zone)

        get_resp = requests.get(
            f"{self._grafana_url}/api/dashboards/uid/{self._dashboard_uid}",
            auth=self._auth, timeout=self._timeout_seconds,
        )
        get_resp.raise_for_status()
        payload = get_resp.json()
        dashboard = payload["dashboard"]

        variables = dashboard.setdefault("templating", {}).setdefault("list", [])
        for variable in variables:
            if variable.get("name") == "location":
                self._set_variable(variable, location_options)
            elif variable.get("name") == "metric":
                self._set_variable(variable, metric_options)

        # Variable `zone` (constante, masquée) + `&zone=${zone}` ajouté aux
        # requêtes des panneaux : depuis l'isolation par zone, les routes
        # /graph/location-readings* exigent `zone` (400 sinon) -- sans ça,
        # chaque panneau restait en erreur "No data" (400 Bad Request côté
        # datasource Infinity) quelle que soit la plage de temps choisie.
        if zone:
            zone_variable = next((v for v in variables if v.get("name") == "zone"), None)
            if zone_variable is None:
                zone_variable = {"name": "zone", "type": "constant", "hide": 2}
                variables.append(zone_variable)
            zone_variable["query"] = zone
            zone_variable["current"] = {"text": zone, "value": zone}
            zone_variable["options"] = [{"selected": True, "text": zone, "value": zone}]

            for panel in dashboard.get("panels", []):
                for target in panel.get("targets", []):
                    url = target.get("url") or ""
                    if "/graph/location-readings" in url and "zone=" not in url:
                        target["url"] = f"{url}&zone=${{zone:percentencode}}"

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

    def _select(self, sql: str) -> list[dict]:
        """query_sql tolérant : un type de sommet jamais créé dans cette base
        (ex: aucun `Room` dans un bâtiment technique sans pièces, comme
        Batipompe) fait échouer ArcadeDB avec "Type ... was not found" -- ce
        n'est pas une erreur ici, juste "aucune ligne"."""
        try:
            return self._graph_repository.query_sql(sql).get("result", [])
        except Exception as exc:
            if "was not found" in str(exc):
                return []
            raise

    @staticmethod
    def _set_variable(variable: dict, options: str) -> None:
        variable["query"] = options
        label, value = options.split(",")[0].split(" : ")
        variable["current"] = {"text": label, "value": value}

    def _build_location_options(self, *, zone: str = "") -> str:
        # SANS filtre de zone (comportement d'origine), cette liste
        # accumule TOUS les Room/Floor jamais ingérés, toutes zones
        # confondues -- plusieurs maquettes résidentielles de test avec des
        # noms de pièces identiques ("Bedroom 2", "Foyer"...) finissaient
        # dupliquées dans le menu déroulant Grafana, dont certaines
        # pointant vers un global_id d'une zone qui n'a plus cours (d'où
        # les "No data" observés). Scopé à la zone qui vient d'être
        # ingérée : le dashboard reste cohérent avec LA zone active,
        # comme le reste du produit (voir /graph/buildings, /graph/floors).
        escaped_zone = zone.replace("'", "\\'")
        zone_filter = f" WHERE zone = '{escaped_zone}'" if zone else ""
        rooms = self._select(f"SELECT global_id, name, in('HAS_CHILD').name as floor FROM Room{zone_filter}")
        floors = self._select(f"SELECT global_id, name FROM Floor{zone_filter}")

        # Deux pièces de même nom (ex: un duplex, deux appartements avec
        # chacun "Bedroom 2") étaient indiscernables dans le menu : on ajoute
        # l'étage, puis un numéro si le doublon subsiste sur le même étage.
        labelled: list[tuple[str, str]] = []
        for row in rooms:
            if not (row.get("global_id") and row.get("name")):
                continue
            floor_names = row.get("floor") or []
            floor_name = floor_names[0] if isinstance(floor_names, list) and floor_names else ""
            labelled.append((f"{row['name']} ({floor_name})" if floor_name else row["name"], row["global_id"]))
        counts: dict[str, int] = {}
        for label, _ in labelled:
            counts[label] = counts.get(label, 0) + 1
        seen: dict[str, int] = {}
        entries: list[tuple[str, str]] = []
        for label, gid in labelled:
            if counts[label] > 1:
                seen[label] = seen.get(label, 0) + 1
                label = f"{label} #{seen[label]}"
            entries.append((label, gid))
        entries += [(row["name"], row["global_id"]) for row in floors if row.get("global_id") and row.get("name")]
        return ",".join(f"{label} : {gid}" for label, gid in entries)

    def _build_metric_options(self, *, zone: str = "") -> str:
        # Uniquement les métriques RÉELLEMENT produites par les équipements
        # de la zone (type d'équipement -> catalogue), pas les ~50 métriques
        # de tout le catalogue : une maquette résidentielle n'a ni débit
        # hydraulique ni CO2, les proposer donnait des "No data" à coup sûr.
        names = self._equipment_catalog.all_metric_names()
        if zone:
            escaped_zone = zone.replace("'", "\\'")
            rows = self._select(f"SELECT DISTINCT(equipment_type) as t FROM Equipment WHERE zone = '{escaped_zone}'")
            zone_names: list[str] = []
            for row in rows:
                equipment_type = row.get("t")
                if not equipment_type:
                    continue
                try:
                    spec = self._equipment_catalog.get_generator_spec(equipment_type)
                except KeyError:
                    continue
                for metric in spec.metrics:
                    if metric.name not in zone_names:
                        zone_names.append(metric.name)
            if zone_names:
                names = zone_names
        # "temperature" en tête (métrique la plus universelle, meilleur
        # défaut pour la sélection initiale du dashboard).
        ordered = sorted(names, key=lambda name: (name != "temperature", name))
        return ",".join(f"{_metric_label(name)} : {name}" for name in ordered)
