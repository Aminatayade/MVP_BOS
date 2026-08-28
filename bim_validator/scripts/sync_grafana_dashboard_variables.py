"""
Resynchronise les variables du dashboard Grafana "BOS Light - Equipement
(demo)" avec l'état courant du système :

- `location` (Pièce/Étage) : liste des Room + Floor RÉELLEMENT présents dans
  ArcadeDB. Chaque maquette IFC réingérée crée de NOUVEAUX global_id (même si
  les noms de pièces sont identiques), donc cette variable devient obsolète
  à chaque nouvel upload -- ce script est à relancer après ingestion.

- `metric` (Type de donnée) : liste de TOUTES les métriques définies dans
  equipment_simulation_catalog.json (le catalogue de simulation), pas
  seulement celles présentes dans les données actuelles. Contrairement à
  `location`, cette liste est stable dans le temps (dérivée du code, pas
  d'une instance de maquette) -- pas besoin de la relancer à chaque upload,
  seulement quand le catalogue gagne un nouveau type de métrique.

Les variables du dashboard sont des `type: "custom"` (liste statique) et non
des variables dynamiques ("query") : une tentative précédente d'utiliser des
variables dynamiques avec le datasource Infinity a échoué silencieusement
sous Grafana 13 (aucune requête HTTP n'était même émise) -- cause jamais
identifiée avec certitude (suspicion d'incompatibilité avec l'architecture
Scenes de Grafana 13). Le contournement retenu est une liste statique
resynchronisée par ce script plutôt qu'une variable réellement dynamique.

Usage :
    python scripts/sync_grafana_dashboard_variables.py
"""

from __future__ import annotations

import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DASHBOARD_UID = "bos-equipment-demo"

# Libellés FR pour les métriques du catalogue -- une métrique absente de
# cette table retombe sur son nom brut (snake_case -> "Title Case").
METRIC_LABELS = {
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
    return METRIC_LABELS.get(name, name.replace("_", " ").title())


def _build_metric_options() -> str:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "equipment_simulation_catalog.json")
    catalog = json.load(open(config_path, encoding="utf-8"))
    metric_names: list[str] = []
    for equipment_type in catalog["equipment_types"].values():
        for metric in equipment_type["metrics"]:
            if metric["name"] not in metric_names:
                metric_names.append(metric["name"])
    # "temperature" en tête (métrique la plus universelle, meilleur défaut
    # pour la sélection initiale du dashboard), le reste par ordre alphabétique.
    ordered = sorted(metric_names, key=lambda name: (name != "temperature", name))
    return ",".join(f"{_metric_label(name)} : {name}" for name in ordered)


def _arcadedb_query(*, base_url: str, database: str, username: str, password: str, sql: str) -> list[dict]:
    endpoint = f"{base_url.rstrip('/')}/api/v1/command/{database}"
    response = requests.post(
        endpoint, json={"language": "sql", "command": sql}, auth=(username, password), timeout=30
    )
    response.raise_for_status()
    return response.json().get("result", [])


def _build_location_options(*, base_url: str, database: str, username: str, password: str) -> str:
    rooms = _arcadedb_query(
        base_url=base_url, database=database, username=username, password=password,
        sql="SELECT global_id, name FROM Room",
    )
    floors = _arcadedb_query(
        base_url=base_url, database=database, username=username, password=password,
        sql="SELECT global_id, name FROM Floor",
    )
    entries = rooms + floors
    if not entries:
        raise RuntimeError("Aucun Room/Floor trouvé dans ArcadeDB -- ingérez d'abord une maquette IFC.")
    return ",".join(f"{row['name']} : {row['global_id']}" for row in entries if row.get("global_id") and row.get("name"))


def main() -> None:
    grafana_url = os.environ.get("GRAFANA_URL", "http://localhost:3000")
    grafana_user = os.environ.get("GRAFANA_ADMIN_USER", "admin")
    grafana_password = os.environ["GRAFANA_ADMIN_PASSWORD"]

    arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
    arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
    arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")

    auth = (grafana_user, grafana_password)
    get_resp = requests.get(f"{grafana_url}/api/dashboards/uid/{DASHBOARD_UID}", auth=auth, timeout=30)
    get_resp.raise_for_status()
    payload = get_resp.json()
    dashboard = payload["dashboard"]

    location_options = _build_location_options(
        base_url=arcadedb_base_url, database=arcadedb_database,
        username=arcadedb_username, password=arcadedb_password,
    )
    metric_options = _build_metric_options()

    updated = {"location": False, "metric": False}
    for variable in dashboard.get("templating", {}).get("list", []):
        if variable.get("name") == "location":
            variable["query"] = location_options
            first_label, first_value = location_options.split(",")[0].split(" : ")
            variable["current"] = {"text": first_label, "value": first_value}
            updated["location"] = True
        elif variable.get("name") == "metric":
            variable["query"] = metric_options
            first_label, first_value = metric_options.split(",")[0].split(" : ")
            variable["current"] = {"text": first_label, "value": first_value}
            updated["metric"] = True

    missing = [name for name, ok in updated.items() if not ok]
    if missing:
        raise RuntimeError(f"Variable(s) introuvable(s) dans le dashboard : {missing}")

    save_resp = requests.post(
        f"{grafana_url}/api/dashboards/db",
        json={"dashboard": dashboard, "overwrite": True, "folderUid": payload.get("meta", {}).get("folderUid", "")},
        auth=auth, timeout=30,
    )
    save_resp.raise_for_status()

    print(f"✅ location : {len(location_options.split(','))} pièces/étages")
    print(f"✅ metric   : {len(metric_options.split(','))} types de métrique (catalogue complet)")


if __name__ == "__main__":
    main()
