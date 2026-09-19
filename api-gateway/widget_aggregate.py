"""
FILE: widget_aggregate.py
DESCRIPTION: Construit le payload agrégé d'UN widget de cockpit en rappelant
             EXACTEMENT les mêmes endpoints REST que WidgetBlock.tsx
             (/graph/location-readings, /graph/location-readings-summary,
             /graph/building-consumption -- bim_api, via BIM_API_INTERNAL_URL)
             -- jamais de recalcul indépendant, pour garantir qu'un widget vu
             par websocket et par REST affiche toujours la même valeur.

             Les payloads renvoyés portent des CODES bruts (catégorie, "_all"),
             pas des libellés humains -- categoryLabel() reste une table de
             correspondance frontend (types.ts), appliquée par le même code
             de mapping que le premier rendu REST (voir
             useWidgetAggregateSocket.ts / WidgetBlock.tsx), pour ne jamais
             dupliquer cette table côté serveur.

INPUTS:
  - Paramètres de widget déjà résolus côté frontend à la connexion websocket
    (bucket_seconds, start_ms) -- jamais recalculés ici, mêmes valeurs que
    l'appel REST initial de WidgetBlock.tsx.

OUTPUTS:
  - Utilisé par la route /ws/widget-aggregate (bim_proxy.py) comme "on_hit"
    enregistré auprès de aggregation_bridge.
"""

from __future__ import annotations

import os

import httpx

BIM_API_INTERNAL_URL = os.getenv("BIM_API_INTERNAL_URL", "http://bim_api:8000").rstrip("/")
SHORT_TIMEOUT = httpx.Timeout(10.0)


def sum_indicator_results(results: list[dict]) -> tuple[float, str]:
    """Combine les résultats /graph/location-readings-summary d'un widget
    Indicateur multi-data_types -- même logique que le reduce
    `results.reduce((sum, r) => sum + (r.value ?? 0), 0)` de WidgetBlock.tsx,
    extraite ici pour être testable indépendamment de tout appel réseau."""
    total = sum((r.get("value") or 0) for r in results)
    unit = next((r.get("unit") for r in results if r.get("unit")), "")
    return round(total * 100) / 100, unit


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    response = await client.get(f"{BIM_API_INTERNAL_URL}{path}", params=params)
    response.raise_for_status()
    return response.json()


async def fetch_widget_payload(
    *,
    widget_type: str,
    space_ids: list[str],
    space_level: str,
    metric: str,
    calcul: str,
    data_types: list[str],
    bucket_seconds: int,
    start_ms: int | None,
    zone: str,
    end_ms: int | None = None,
) -> dict:
    gid = ",".join(space_ids)
    async with httpx.AsyncClient(timeout=SHORT_TIMEOUT) as client:
        if widget_type == "indicateur":
            types = data_types or [None]
            results = []
            for dt in types:
                params = {"global_id": gid, "metric": metric, "aggregation": calcul, "zone": zone}
                if dt:
                    params["category"] = dt
                if start_ms:
                    params["start"] = start_ms
                if end_ms:
                    params["end"] = end_ms
                results.append(await _get(client, "/graph/location-readings-summary", params))
            value, unit = sum_indicator_results(results)
            return {"type": "indicateur", "value": value, "unit": unit}

        if widget_type in ("barres", "camembert"):
            if space_level == "batiment":
                params = {"global_id": space_ids[0], "metric": metric, "zone": zone}
                if len(data_types) == 1:
                    params["category"] = data_types[0]
                payload = await _get(client, "/graph/building-consumption", params)
                bars = [{"label": f.get("floor_name"), "value": f.get("value")} for f in payload.get("by_floor", [])]
                return {"type": widget_type, "building": True, "bars": bars, "unit": payload.get("unit", "")}

            types = data_types or ["_all"]
            entries = []
            for dt in types:
                params = {"global_id": gid, "metric": metric, "aggregation": calcul, "zone": zone}
                if dt != "_all":
                    params["category"] = dt
                if start_ms:
                    params["start"] = start_ms
                if end_ms:
                    params["end"] = end_ms
                summary = await _get(client, "/graph/location-readings-summary", params)
                entries.append({"code": dt, "value": summary.get("value") or 0, "unit": summary.get("unit") or ""})
            return {"type": widget_type, "building": False, "entries": entries}

        if widget_type == "courbe":
            types = data_types or ["_all"]
            datasets = []
            for dt in types:
                params = {"global_id": gid, "metric": metric, "bucket_seconds": bucket_seconds, "aggregation": calcul, "zone": zone}
                if dt != "_all":
                    params["category"] = dt
                if start_ms:
                    params["start"] = start_ms
                if end_ms:
                    params["end"] = end_ms
                payload = await _get(client, "/graph/location-readings", params)
                datasets.append({"code": dt, "points": payload.get("readings", [])})
            return {"type": "courbe", "datasets": datasets}

        if widget_type == "semaine_type":
            params = {
                "global_id": gid, "metric": metric, "bucket_seconds": 3600,
                "aggregation": calcul, "raw_limit": 20000, "zone": zone,
            }
            if len(data_types) == 1:
                params["category"] = data_types[0]
            payload = await _get(client, "/graph/location-readings", params)
            return {"type": "semaine_type", "points": payload.get("readings", [])}

        raise ValueError(f"widget_type inconnu : {widget_type}")


async def resolve_equipment_ids(space_ids: list[str], zone: str) -> set[str]:
    """Résout l'ensemble d'équipements couvert par `space_ids` via
    /graph/equipment-list (même endpoint que le sélecteur "cibler un
    équipement précis"), en relevant sa limite par défaut (500) pour cet
    usage interne -- voir le commentaire ajouté sur cette route dans
    api_server.py. Utilisé UNE FOIS à la connexion websocket pour enregistrer
    l'ensemble d'équipements à surveiller auprès de aggregation_bridge, pas
    rappelé à chaque tick. `zone` requis depuis l'isolation par zone du
    graphe (Phase 1) -- sans elle, bim_api ne sait plus quelle base
    interroger."""
    async with httpx.AsyncClient(timeout=SHORT_TIMEOUT) as client:
        response = await client.get(
            f"{BIM_API_INTERNAL_URL}/graph/equipment-list",
            params={"global_id": ",".join(space_ids), "limit": 20000, "zone": zone},
        )
        response.raise_for_status()
        payload = response.json()
    return {e["global_id"] for e in payload.get("equipment", []) if e.get("global_id")}
