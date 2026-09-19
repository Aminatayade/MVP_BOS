"""
FILE: bim_proxy.py
DESCRIPTION: Routes de proxy authentifié vers l'API BIM (MVP-bim-integration :
             bim_api + timeseries_api). bim_api/timeseries_api ne sont plus
             exposés sur l'hôte (voir fusion/docker-compose.yml) : cette
             gateway est leur SEUL point d'accès, et applique le même RBAC
             (Administrateur/Configurateur) que le reste de l'API BOS Light.

INPUTS:
  - Requêtes HTTP du frontend vers /api/bim/*
  - Variables d'environnement BIM_API_INTERNAL_URL / TIMESERIES_API_INTERNAL_URL

OUTPUTS:
  - Réponses forwardées depuis bim_api / timeseries_api (JSON), ou erreurs
    502/504 génériques en cas d'échec de l'appel amont (jamais de détails
    internes -- hostname Docker, stack trace -- exposés au client).

DEPENDENCIES:
  - httpx (client HTTP asynchrone -- contrairement à `requests`, ne bloque pas
    la boucle d'événements FastAPI le temps qu'un gros fichier IFC transite)

NOTES:
  - Allow-list explicite de routes (pas de proxy générique /{path:path}) :
    la gateway ne doit exposer que les endpoints BIM réellement utilisés par
    le frontend, jamais devenir un proxy ouvert vers le réseau Docker interne.
"""

import json
import os
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
import httpx

import aggregation_bridge
import nats_bridge
import widget_aggregate
from security import EXECUTION_ALLOWED_ROLES, ALLOWED_ROLES, decode_roles_from_token, verify_allowed_roles, verify_execution_allowed_roles

BIM_API_INTERNAL_URL = os.getenv("BIM_API_INTERNAL_URL", "http://bim_api:8000").rstrip("/")
TIMESERIES_API_INTERNAL_URL = os.getenv("TIMESERIES_API_INTERNAL_URL", "http://timeseries_api:8001").rstrip("/")

# Fichiers IFC encodés en base64 (+33% de volume), lot pouvant contenir
# plusieurs maquettes : plafond généreux mais fini, pour éviter qu'une requête
# ne sature la mémoire de la gateway.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))

SHORT_TIMEOUT = httpx.Timeout(10.0)
# 120s suffisait pour des maquettes de test, mais une maquette réelle de
# plusieurs dizaines de Mo (des dizaines de milliers d'éléments IFC validés un
# par un) peut légitimement prendre plusieurs minutes -- observé en pratique
# avec bim_api qui finissait par répondre "200" bien après ce délai, laissant
# le client avec une 502/504 alors que l'ingestion avait réussi.
LONG_TIMEOUT = httpx.Timeout(600.0)

router = APIRouter(prefix="/api/bim", tags=["bim-proxy"])


def _upstream_error_message(payload: dict) -> str:
    # bim_api (http.server maison) renvoie {"error": ...} ; timeseries_api
    # (FastAPI) renvoie {"detail": ...} sur ses HTTPException — on gère les deux.
    return payload.get("error") or payload.get("detail") or "Requête invalide."


async def _relay_response(response: httpx.Response) -> dict:
    if response.status_code >= 500:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM a renvoyé une erreur.")

    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Réponse invalide du service BIM.")

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_message(payload))

    return payload


async def _forward_get(base_url: str, path: str, request: Request, timeout: httpx.Timeout) -> dict:
    upstream_url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(upstream_url, params=dict(request.query_params))
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Le service BIM n'a pas répondu à temps.")
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM est indisponible.")

    return await _relay_response(response)


async def _forward_post_json(base_url: str, path: str, request: Request, timeout: httpx.Timeout) -> dict:
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Payload trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo).",
        )

    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Payload trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo).",
        )

    upstream_url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(upstream_url, content=body, headers={"Content-Type": "application/json"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Le service BIM n'a pas répondu à temps.")
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM est indisponible.")

    return await _relay_response(response)


async def _forward_post_with_query(base_url: str, path: str, request: Request, timeout: httpx.Timeout) -> dict:
    """POST sans corps JSON, paramètres en query string -- distinct de
    _forward_post_json (qui relaie request.body(), pas request.query_params).
    Utilisé par les routes /timeseries/live-simulation/* dont l'API amont
    (timeseries_api.py) prend zone/tick_seconds/cycle_minutes en paramètres
    de requête simples, pas un corps JSON."""
    upstream_url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(upstream_url, params=dict(request.query_params))
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Le service BIM n'a pas répondu à temps.")
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM est indisponible.")

    return await _relay_response(response)


async def _forward_delete(base_url: str, path: str, request: Request, timeout: httpx.Timeout) -> dict:
    upstream_url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.delete(upstream_url, params=dict(request.query_params))
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Le service BIM n'a pas répondu à temps.")
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM est indisponible.")

    return await _relay_response(response)


async def _forward_get_binary(base_url: str, path: str, request: Request, timeout: httpx.Timeout) -> StreamingResponse:
    # _forward_get/_relay_response appellent response.json() : inadapte a un
    # flux binaire (fichier IFC). httpx.AsyncClient.stream() relaie le corps
    # au fil de l'eau, sans le charger entierement en memoire cote gateway --
    # meme logique de streaming deja justifiee pour ingest-batch, en sens
    # inverse (telechargement plutot qu'upload).
    upstream_url = f"{base_url}{path}"
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream_request = client.build_request("GET", upstream_url, params=dict(request.query_params))
        response = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Le service BIM n'a pas répondu à temps.")
    except httpx.RequestError:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Le service BIM est indisponible.")

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        try:
            detail = _upstream_error_message(json.loads(body))
        except ValueError:
            detail = "Requête invalide."
        status_code = response.status_code if response.status_code < 500 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=detail)

    async def body_iterator():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    media_type = response.headers.get("content-type", "application/octet-stream")
    return StreamingResponse(body_iterator(), media_type=media_type)


# --- Graphe BIM (ArcadeDB, via bim_api) -- Configurateur uniquement ---------

# Ouvert au rôle Client (verify_execution_allowed_roles) : le widget
# "Consommation électrique" de BOS Exécution a besoin de résoudre le
# global_id du Building de sa zone avant de pouvoir appeler
# /graph/building-consumption. Lecture seule, aucune route d'écriture.
@router.get("/graph/buildings", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_buildings(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/buildings", request, SHORT_TIMEOUT)


# Ouvert aussi au rôle Client : le niveau "Étage" du sélecteur Espace du
# constructeur de cockpits (BOS Exécution) en a besoin. Lecture seule, même
# forme que /graph/buildings ci-dessus.
@router.get("/graph/floors", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_floors(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/floors", request, SHORT_TIMEOUT)


@router.get("/graph/equipment", dependencies=[Depends(verify_allowed_roles)])
async def graph_equipment(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/equipment", request, SHORT_TIMEOUT)


# Ouvert aussi au rôle Client : alimente le menu déroulant de zone dans les
# deux frontends (BimExplorer.tsx côté Configurateur, BimViewer.tsx côté
# Exécution) -- isolation par zone Phase 1, voir /graph/zones côté bim_api.
@router.get("/graph/zones", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_zones(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/zones", request, SHORT_TIMEOUT)


# LONG_TIMEOUT (pas SHORT) : le premier appel sur une zone avec de vraies
# Room provisionne des capteurs de présence simulés + backfill leur
# historique (voir occupancy_provisioning_service.py côté bim_api) --
# nettement plus lent qu'une simple lecture de graphe.
@router.get("/graph/occupancy-dashboard", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_occupancy_dashboard(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/occupancy-dashboard", request, LONG_TIMEOUT)


@router.get("/graph/neighbors", dependencies=[Depends(verify_allowed_roles)])
async def graph_neighbors(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/neighbors", request, SHORT_TIMEOUT)


@router.get("/graph/room-equipment", dependencies=[Depends(verify_allowed_roles)])
async def graph_room_equipment(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/room-equipment", request, SHORT_TIMEOUT)


@router.get("/graph/locations", dependencies=[Depends(verify_allowed_roles)])
async def graph_locations(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/locations", request, SHORT_TIMEOUT)


# Ouvert aussi au rôle Client : la courbe de tendance du widget "Consommation
# électrique" (BOS Exécution) l'appelle directement (pas seulement Grafana,
# qui contacte bim_api en interne sans passer par la gateway).
@router.get("/graph/location-readings", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_location_readings(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/location-readings", request, SHORT_TIMEOUT)


# Panel Grafana "Stat"/"Gauge" (une seule valeur, pas une courbe) : agrégat
# SUM/AVG/MAX/MIN calculé nativement par ArcadeDB, voir api_server.py.
# Ouvert aussi au rôle Client : le widget Indicateur (et les widgets
# Barres/Camembert hors échelle Bâtiment) du constructeur de cockpits en
# ont besoin. Lecture seule, même profil de risque que
# /graph/location-readings ci-dessus, déjà ouvert au Client.
@router.get("/graph/location-readings-summary", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_location_readings_summary(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/location-readings-summary", request, SHORT_TIMEOUT)


@router.get("/graph/view", dependencies=[Depends(verify_allowed_roles)])
async def graph_view(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/view", request, SHORT_TIMEOUT)


# Widget "Consommation électrique" (BOS Exécution) : total bâtiment + répartition
# par étage, calculé côté bim_api (somme de la dernière valeur connue par
# équipement, voir api_server.py). Ouvert au rôle Client comme /graph/buildings
# ci-dessus.
@router.get("/graph/building-consumption", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_building_consumption(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/building-consumption", request, SHORT_TIMEOUT)


# Colonne "Données disponibles" du constructeur de cockpits (BOS Exécution).
@router.get("/graph/equipment-scope", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_equipment_scope(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/equipment-scope", request, SHORT_TIMEOUT)


# Sélecteur "cibler un équipement précis" du widget Indicateur (constructeur
# de cockpits) : variante instances de /graph/equipment-scope ci-dessus.
@router.get("/graph/equipment-list", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_equipment_list(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/equipment-list", request, SHORT_TIMEOUT)


# Niveau "Type de pièce" du sélecteur Espace du constructeur de cockpits.
@router.get("/graph/typologies", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_typologies(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/typologies", request, SHORT_TIMEOUT)


@router.get("/graph/rooms-by-typology", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_rooms_by_typology(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/rooms-by-typology", request, SHORT_TIMEOUT)


# Niveau "Pièce spécifique" du sélecteur Espace du constructeur de cockpits.
@router.get("/graph/rooms-by-floor", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_rooms_by_floor(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/rooms-by-floor", request, SHORT_TIMEOUT)


# Suppression complète d'une zone (Building/Floor/Room/Equipment/Typology +
# leur historique EquipmentReading) -- Admin/Configurateur UNIQUEMENT
# (verify_allowed_roles, pas la variante Exécution) : c'est une opération
# d'écriture destructive, jamais accessible au rôle Client.
@router.delete("/graph/zone", dependencies=[Depends(verify_allowed_roles)])
async def delete_zone(request: Request):
    return await _forward_delete(BIM_API_INTERNAL_URL, "/graph/zone", request, LONG_TIMEOUT)


# --- Jumeau numérique (point 6 de la roadmap) --------------------------------
# Seules routes en lecture accessibles au rôle Client (BOS Exécution) : la
# maquette 3D est consultable une fois ingérée, mais l'ingestion elle-même
# (POST /validate, /ingest-batch ci-dessous) reste réservée à Configurateur.

@router.get("/graph/sources", dependencies=[Depends(verify_execution_allowed_roles)])
async def graph_sources(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/sources", request, SHORT_TIMEOUT)


@router.get("/models/download", dependencies=[Depends(verify_execution_allowed_roles)])
async def models_download(request: Request):
    return await _forward_get_binary(BIM_API_INTERNAL_URL, "/models/download", request, LONG_TIMEOUT)


# --- Validation / ingestion IFC (bim_api) -- Configurateur uniquement -------

@router.post("/validate", dependencies=[Depends(verify_allowed_roles)])
async def validate_batch(request: Request):
    return await _forward_post_json(BIM_API_INTERNAL_URL, "/validate", request, LONG_TIMEOUT)


@router.post("/ingest-batch", dependencies=[Depends(verify_allowed_roles)])
async def ingest_batch(request: Request):
    return await _forward_post_json(BIM_API_INTERNAL_URL, "/ingest-batch", request, LONG_TIMEOUT)


# --- Mesures temps réel (timeseries_api) -- Configurateur uniquement --------

@router.get("/timeseries/readings", dependencies=[Depends(verify_allowed_roles)])
async def timeseries_readings(request: Request):
    return await _forward_get(TIMESERIES_API_INTERNAL_URL, "/timeseries/readings", request, SHORT_TIMEOUT)


# Ouvert aussi au rôle Client : le widget Indicateur "cibler un équipement
# précis" du constructeur de cockpits (BOS Exécution) en a besoin. Lecture
# seule (valeur courante + libellés du catalogue), même profil de risque que
# /graph/equipment-scope déjà ouvert.
@router.get("/timeseries/equipment-summary", dependencies=[Depends(verify_execution_allowed_roles)])
async def timeseries_equipment_summary(request: Request):
    return await _forward_get(TIMESERIES_API_INTERNAL_URL, "/timeseries/equipment-summary", request, SHORT_TIMEOUT)


# Simulation live continue par zone -- Administrateur/Configurateur
# UNIQUEMENT (verify_allowed_roles, pas la variante Exécution) : démarrer un
# flux qui tourne en permanence a un coût de charge soutenu (voir
# live_simulation_service.py), c'est une action opérationnelle comparable à
# /ingest-batch, jamais une simple lecture accessible au rôle Client.
@router.post("/timeseries/live-simulation/start", dependencies=[Depends(verify_allowed_roles)])
async def timeseries_live_simulation_start(request: Request):
    return await _forward_post_with_query(TIMESERIES_API_INTERNAL_URL, "/timeseries/live-simulation/start", request, SHORT_TIMEOUT)


@router.post("/timeseries/live-simulation/stop", dependencies=[Depends(verify_allowed_roles)])
async def timeseries_live_simulation_stop(request: Request):
    return await _forward_post_with_query(TIMESERIES_API_INTERNAL_URL, "/timeseries/live-simulation/stop", request, SHORT_TIMEOUT)


@router.get("/timeseries/live-simulation/status", dependencies=[Depends(verify_allowed_roles)])
async def timeseries_live_simulation_status(request: Request):
    return await _forward_get(TIMESERIES_API_INTERNAL_URL, "/timeseries/live-simulation/status", request, SHORT_TIMEOUT)


@router.websocket("/ws/equipment/{global_id}")
async def ws_equipment_telemetry(websocket: WebSocket, global_id: str):
    """Pousse en direct chaque nouvelle mesure NATS pour `global_id`, tant que
    la connexion reste ouverte -- remplace le polling HTTP toutes les 10s des
    popups de télémétrie (EquipmentTelemetryPopup.tsx / BimModelViewer.tsx).

    Le navigateur ne peut pas poser de header Authorization sur le handshake
    WebSocket (contrairement à une requête fetch/axios normale) : le token
    Keycloak arrive donc en paramètre de requête (`?token=...`), jamais en
    header -- seul point où l'authentification de cette gateway diffère des
    routes REST au-dessus. Même jeu de rôles que la route REST équivalente
    (/timeseries/equipment-summary) : Administrateur/Configurateur.
    """
    token = websocket.query_params.get("token", "")
    try:
        roles = decode_roles_from_token(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not set(roles).intersection(ALLOWED_ROLES):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    nats_bridge.register(global_id, websocket)
    try:
        while True:
            # Le serveur ne fait que pousser -- cette attente sert seulement à
            # détecter la déconnexion (lève WebSocketDisconnect), le contenu
            # d'un éventuel message client n'est jamais utilisé.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        nats_bridge.unregister(global_id, websocket)


@router.websocket("/ws/widget-aggregate")
async def ws_widget_aggregate(websocket: WebSocket):
    """Pousse un recalcul agrégé de widget de cockpit dès qu'un équipement de
    son périmètre spatial est mis à jour sur le bus NATS -- remplace le
    polling REST toutes les 30s de WidgetBlock.tsx. Ne réimplémente aucune
    agrégation : chaque recalcul rappelle en interne le MÊME endpoint REST
    que WidgetBlock.tsx utilisait déjà (voir widget_aggregate.py), garantie
    de non-divergence entre le premier affichage (REST) et les mises à jour
    poussées (websocket).

    Paramètres de requête (mêmes valeurs que WidgetBlock.tsx calcule déjà
    pour son appel REST initial -- jamais recalculés côté serveur) :
      token, widget_type, space_ids (csv), space_level, metric, calcul,
      data_types (csv, facultatif), bucket_seconds, start_ms/end_ms
      (facultatifs -- bornage calendaire, pagination "Mois précédent/suivant").

    Auth identique à ws_equipment_telemetry (token en query param, le
    navigateur ne pouvant pas poser de header Authorization sur un handshake
    websocket) mais avec EXECUTION_ALLOWED_ROLES (pas ALLOWED_ROLES) : les
    cockpits sont aussi utilisés par le rôle Client, comme les routes REST
    /graph/location-readings* déjà ouvertes à ce rôle ci-dessus.
    """
    params = websocket.query_params
    token = params.get("token", "")
    try:
        roles = decode_roles_from_token(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not set(roles).intersection(EXECUTION_ALLOWED_ROLES):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    widget_type = params.get("widget_type", "")
    space_level = params.get("space_level", "")
    space_ids = [s for s in params.get("space_ids", "").split(",") if s]
    metric = params.get("metric", "")
    calcul = params.get("calcul", "sum")
    data_types = [d for d in params.get("data_types", "").split(",") if d]
    bucket_seconds = int(params.get("bucket_seconds") or 300)
    start_ms_raw = params.get("start_ms")
    start_ms = int(start_ms_raw) if start_ms_raw else None
    end_ms_raw = params.get("end_ms")
    end_ms = int(end_ms_raw) if end_ms_raw else None
    zone = params.get("zone", "")

    if not widget_type or not space_ids or not metric or not zone:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Résolu UNE FOIS à la connexion (pas à chaque tick) -- l'ensemble
    # d'équipements du périmètre du widget sert uniquement à filtrer les
    # messages NATS qui doivent déclencher un recalcul (voir
    # aggregation_bridge.py), jamais à calculer la valeur elle-même (ça reste
    # le rôle des endpoints /graph/location-readings* rappelés ci-dessous).
    try:
        equipment_ids = await widget_aggregate.resolve_equipment_ids(space_ids, zone)
    except Exception:
        equipment_ids = set()

    conn_id = f"widget-{id(websocket)}"

    async def on_hit() -> None:
        try:
            payload = await widget_aggregate.fetch_widget_payload(
                widget_type=widget_type,
                space_ids=space_ids,
                space_level=space_level,
                metric=metric,
                calcul=calcul,
                data_types=data_types,
                bucket_seconds=bucket_seconds,
                start_ms=start_ms,
                end_ms=end_ms,
                zone=zone,
            )
        except Exception as exc:
            print(f"⚠️  Recalcul widget-aggregate en échec : {exc}")
            return
        await websocket.send_json(payload)

    aggregation_bridge.register(conn_id, equipment_ids, on_hit)
    try:
        while True:
            # Le serveur ne fait que pousser -- cette attente sert seulement à
            # détecter la déconnexion, même motif que ws_equipment_telemetry.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        aggregation_bridge.unregister(conn_id)
