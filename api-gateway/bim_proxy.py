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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
import httpx

from security import verify_allowed_roles

BIM_API_INTERNAL_URL = os.getenv("BIM_API_INTERNAL_URL", "http://bim_api:8000").rstrip("/")
TIMESERIES_API_INTERNAL_URL = os.getenv("TIMESERIES_API_INTERNAL_URL", "http://timeseries_api:8001").rstrip("/")

# Fichiers IFC encodés en base64 (+33% de volume), lot pouvant contenir
# plusieurs maquettes : plafond généreux mais fini, pour éviter qu'une requête
# ne sature la mémoire de la gateway.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))

SHORT_TIMEOUT = httpx.Timeout(10.0)
LONG_TIMEOUT = httpx.Timeout(120.0)

router = APIRouter(prefix="/api/bim", tags=["bim-proxy"], dependencies=[Depends(verify_allowed_roles)])


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


# --- Graphe BIM (ArcadeDB, via bim_api) -------------------------------------

@router.get("/graph/buildings")
async def graph_buildings(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/buildings", request, SHORT_TIMEOUT)


@router.get("/graph/floors")
async def graph_floors(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/floors", request, SHORT_TIMEOUT)


@router.get("/graph/equipment")
async def graph_equipment(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/equipment", request, SHORT_TIMEOUT)


@router.get("/graph/neighbors")
async def graph_neighbors(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/neighbors", request, SHORT_TIMEOUT)


@router.get("/graph/room-equipment")
async def graph_room_equipment(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/room-equipment", request, SHORT_TIMEOUT)


@router.get("/graph/view")
async def graph_view(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/view", request, SHORT_TIMEOUT)


@router.get("/graph/sources")
async def graph_sources(request: Request):
    return await _forward_get(BIM_API_INTERNAL_URL, "/graph/sources", request, SHORT_TIMEOUT)


# --- Jumeau numérique (point 6 de la roadmap) --------------------------------

@router.get("/models/download")
async def models_download(request: Request):
    return await _forward_get_binary(BIM_API_INTERNAL_URL, "/models/download", request, LONG_TIMEOUT)


# --- Validation / ingestion IFC (bim_api) -----------------------------------

@router.post("/validate")
async def validate_batch(request: Request):
    return await _forward_post_json(BIM_API_INTERNAL_URL, "/validate", request, LONG_TIMEOUT)


@router.post("/ingest-batch")
async def ingest_batch(request: Request):
    return await _forward_post_json(BIM_API_INTERNAL_URL, "/ingest-batch", request, LONG_TIMEOUT)


# --- Mesures temps réel (timeseries_api) ------------------------------------

@router.get("/timeseries/readings")
async def timeseries_readings(request: Request):
    return await _forward_get(TIMESERIES_API_INTERNAL_URL, "/timeseries/readings", request, SHORT_TIMEOUT)
