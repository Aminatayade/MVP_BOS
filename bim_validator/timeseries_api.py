"""
Container API « TimeSeries » (FastAPI).

Expose la lecture des mesures ArcadeDB pour le frontend, ET fait tourner le
consumer NATS JetStream dans SON PROPRE process (lifespan startup), pour que
l'ingestion live ne dépende pas d'un script séparé lancé à la main.

run_consumer() (consume_timeseries.py) est bloquant et fait son propre
asyncio.run() interne : l'appeler directement dans la boucle asyncio d'uvicorn
la gèlerait. Un thread daemon dédié l'isole sans toucher à l'adapter NATS.

Usage :
    uvicorn timeseries_api:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapters.arcadedb_ts_adapter import ArcadeDbTimeseriesAdapter
from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from application.live_simulation_service import DEFAULT_SUBJECT, LivePublisher, run_zone
from consume_timeseries import run_consumer
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter
from infrastructure.secrets import read_secret


def _build_repository() -> ArcadeDbTimeseriesAdapter:
    return ArcadeDbTimeseriesAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=read_secret("ARCADEDB_PASSWORD", "testBIM123"),
    )


def _build_graph_repository() -> ArcadeDbRepositoryAdapter:
    return ArcadeDbRepositoryAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=read_secret("ARCADEDB_PASSWORD", "testBIM123"),
    )


def _build_catalog() -> JsonEquipmentCatalogAdapter:
    config_path = os.path.join(os.path.dirname(__file__), "config", "equipment_simulation_catalog.json")
    return JsonEquipmentCatalogAdapter(config_path)


def _ensure_arcadedb_ready(attempts: int = 10, delay_seconds: float = 3.0) -> None:
    """Cree la base ArcadeDB 'bim' si besoin, avec re-essais (meme logique
    que api_server.py -- voir sa docstring pour le pourquoi)."""
    repo = ArcadeDbRepositoryAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=read_secret("ARCADEDB_PASSWORD", "testBIM123"),
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            repo.ensure_database()
            print("ArcadeDB: base 'bim' prete.")
            return
        except Exception as exc:
            last_error = exc
            print(f"ArcadeDB pas encore pret (essai {attempt}/{attempts}) : {exc}", file=sys.stderr)
            time.sleep(delay_seconds)
    print(f"ArcadeDB toujours indisponible apres {attempts} essais, demarrage quand meme : {last_error}", file=sys.stderr)


# zone -> {"task": asyncio.Task, "stop_event": asyncio.Event,
#          "equipment_count": int, "started_at": str, "tick_seconds": float,
#          "cycle_minutes": float}
# Désactivé par défaut : rien ne démarre automatiquement, uniquement
# accessible via /timeseries/live-simulation/* -- une simulation continue a
# un coût de charge soutenu (voir live_simulation_service.py), donc une
# action explicite est requise plutôt qu'un démarrage implicite à l'ingestion.
_running_simulations: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_arcadedb_ready()
    thread = threading.Thread(target=run_consumer, name="nats-timeseries-consumer", daemon=True)
    thread.start()
    yield
    # Arrêt propre des simulations live encore actives : lève tous les
    # stop_event et attend leurs tâches, plutôt que de les laisser mourir
    # brutalement avec le process (la connexion NATS de LivePublisher ne
    # serait alors jamais fermée proprement).
    for zone, entry in list(_running_simulations.items()):
        entry["stop_event"].set()
    for zone, entry in list(_running_simulations.items()):
        try:
            await entry["task"]
        except Exception:
            pass
    _running_simulations.clear()


app = FastAPI(title="BOS TimeSeries API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/timeseries/readings")
def get_readings(
    global_id: str,
    metric: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = 1000,
) -> dict:
    """Lecture pour affichage (courbes frontend) des points EquipmentReading
    déjà ingérés via le pipeline NATS -> ArcadeDB. Même contrat que l'ancienne
    route équivalente de api_server.py, pour ne pas casser le frontend."""
    try:
        start = datetime.fromisoformat(from_) if from_ else None
        end = datetime.fromisoformat(to) if to else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid query parameter: {exc}") from exc

    repository = _build_repository()
    try:
        readings = repository.query_readings(global_id, metric=metric, start=start, end=end, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "global_id": global_id,
        "metric": metric,
        "count": len(readings),
        "readings": readings,
    }


@app.get("/timeseries/equipment-summary")
def get_equipment_summary(global_id: str, history_limit: int = 50) -> dict:
    """Valeur courante + historique récent, groupés par métrique, pour le
    popup graphe (nœud Equipment). Les métriques attendues viennent du
    catalogue de simulation, résolu via `equipment_type` déjà stocké sur le
    nœud graphe à l'ingestion -- pas une découverte a posteriori dans
    EquipmentReading, pour pouvoir répondre `metrics: []` correctement même
    si le backfill (best-effort, en tâche de fond) n'est pas encore passé."""
    graph_repo = _build_graph_repository()
    escaped_gid = global_id.replace("\\", "\\\\").replace("'", "\\'")
    try:
        graph_resp = graph_repo.query_sql(
            f"SELECT equipment_type FROM Equipment WHERE global_id = '{escaped_gid}'"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    rows = graph_resp.get("result", [])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Equipment '{global_id}' introuvable dans le graphe.")

    equipment_type = rows[0].get("equipment_type") or "_unknown"
    catalog = _build_catalog()
    try:
        spec = catalog.get_generator_spec(equipment_type)
    except KeyError:
        # equipment_type inconnu du catalogue (ex: équipement ingéré avant
        # l'ajout de cette fonctionnalité) -- repli plutôt qu'une 500.
        spec = catalog.get_generator_spec("_unknown")

    timeseries_repo = _build_repository()
    metrics_out = []
    for metric_spec in spec.metrics:
        try:
            latest_rows = timeseries_repo.query_latest(global_id, metric=metric_spec.name, limit=1)
            history_rows = timeseries_repo.query_readings(global_id, metric=metric_spec.name, limit=history_limit)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        current = None
        if latest_rows:
            latest = latest_rows[0]
            current = {
                "value": latest.get("value"),
                "timestamp": latest.get("ts"),
                "quality": latest.get("quality"),
            }

        metrics_out.append({
            "metric": metric_spec.name,
            "unit": metric_spec.unit,
            "value_labels": metric_spec.value_labels,
            "current": current,
            "history": [{"value": r.get("value"), "timestamp": r.get("ts")} for r in history_rows],
        })

    return {
        "ok": True,
        "global_id": global_id,
        "equipment_type": equipment_type,
        "metrics": metrics_out,
    }


def _fetch_zone_equipment(zone: str) -> list[tuple[str, str]]:
    """(global_id, equipment_type) réels pour TOUTE une zone -- `zone` est
    déjà une propriété directe sur chaque nœud Equipment (voir
    bim_graph_ingestion_service.py), donc une simple égalité suffit : pas
    besoin de résoudre un Building/traverser HAS_CHILD*0.. comme le fait
    /graph/equipment-list (qui, lui, part d'un périmètre spatial arbitraire,
    pas d'une zone entière connue à l'avance)."""
    repo = _build_graph_repository()
    escaped_zone = zone.replace("\\", "\\\\").replace("'", "\\'")
    response = repo.query_sql(
        f"SELECT global_id, equipment_type FROM Equipment "
        f"WHERE zone = '{escaped_zone}' AND equipment_type IS NOT NULL"
    )
    rows = response.get("result", []) if isinstance(response, dict) else []
    return [(row["global_id"], row["equipment_type"]) for row in rows if row.get("global_id") and row.get("equipment_type")]


async def _run_zone_simulation(zone: str, tick_seconds: float, cycle_minutes: float) -> None:
    """Tâche de fond complète pour une zone : ouvre sa propre connexion
    NATS, tourne jusqu'à stop_event, puis nettoie -- y compris si run_zone
    lève une exception inattendue, pour ne jamais laisser une entrée
    "zombie" dans _running_simulations qui bloquerait un futur /start."""
    entry = _running_simulations[zone]
    publisher = entry["publisher"]
    try:
        await run_zone(
            zone=zone,
            equipment_specs=entry["equipment_specs"],
            publisher=publisher,
            subject=os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT),
            tick_seconds=tick_seconds,
            cycle_minutes=cycle_minutes,
            stop_event=entry["stop_event"],
        )
    except Exception as exc:
        print(f"⚠️  Simulation live de la zone '{zone}' interrompue par une erreur : {exc}", file=sys.stderr)
    finally:
        await publisher.close()
        _running_simulations.pop(zone, None)


@app.post("/timeseries/live-simulation/start")
async def start_live_simulation(zone: str, tick_seconds: float = 10.0, cycle_minutes: float = 10.0) -> dict:
    """Démarre un flux live CONTINU pour toute une zone (jusqu'à /stop ou
    arrêt du service) -- généralisation de simulate_live_stream.py (script
    de démo, 5 équipements par défaut) en service piloté par API. Bornes
    serveur sur la cadence pour empêcher une configuration accidentelle trop
    agressive (voir live_simulation_service.py pour le calcul de charge)."""
    if zone in _running_simulations:
        raise HTTPException(status_code=409, detail=f"Une simulation live tourne déjà pour la zone '{zone}'.")

    tick_seconds = max(5.0, tick_seconds)
    cycle_minutes = max(2.0, cycle_minutes)

    equipment = _fetch_zone_equipment(zone)
    catalog = _build_catalog()
    equipment_specs = []
    for global_id, equipment_type in equipment:
        try:
            spec = catalog.get_generator_spec(equipment_type)
        except KeyError:
            continue
        if spec.metrics:
            equipment_specs.append((global_id, spec))

    if not equipment_specs:
        raise HTTPException(
            status_code=422,
            detail=f"Aucun équipement simulable trouvé pour la zone '{zone}' (zone vide, ou aucun type d'équipement avec métriques).",
        )

    publisher = LivePublisher(
        servers=os.environ.get("NATS_URL", "nats://localhost:4222"),
        user=os.environ.get("NATS_USER", "bos_admin"),
        password=read_secret("NATS_PASSWORD", "testBIM123"),
        subjects=[os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)],
    )
    try:
        await publisher.connect()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Connexion NATS impossible : {exc}") from exc

    stop_event = asyncio.Event()
    started_at = datetime.now(timezone.utc).isoformat()
    _running_simulations[zone] = {
        "stop_event": stop_event,
        "publisher": publisher,
        "equipment_specs": equipment_specs,
        "equipment_count": len(equipment_specs),
        "started_at": started_at,
        "tick_seconds": tick_seconds,
        "cycle_minutes": cycle_minutes,
    }
    task = asyncio.create_task(_run_zone_simulation(zone, tick_seconds, cycle_minutes))
    _running_simulations[zone]["task"] = task

    return {
        "ok": True,
        "zone": zone,
        "equipment_count": len(equipment_specs),
        "tick_seconds": tick_seconds,
        "cycle_minutes": cycle_minutes,
        "started_at": started_at,
    }


@app.post("/timeseries/live-simulation/stop")
async def stop_live_simulation(zone: str) -> dict:
    entry = _running_simulations.get(zone)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Aucune simulation live en cours pour la zone '{zone}'.")

    entry["stop_event"].set()
    try:
        await entry["task"]
    except Exception:
        pass
    _running_simulations.pop(zone, None)
    return {"ok": True, "zone": zone, "stopped": True}


@app.get("/timeseries/live-simulation/status")
def live_simulation_status() -> dict:
    return {
        "ok": True,
        "zones": [
            {
                "zone": zone,
                "equipment_count": entry["equipment_count"],
                "started_at": entry["started_at"],
                "tick_seconds": entry["tick_seconds"],
                "cycle_minutes": entry["cycle_minutes"],
            }
            for zone, entry in _running_simulations.items()
        ],
    }
