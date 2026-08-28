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

import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapters.arcadedb_ts_adapter import ArcadeDbTimeseriesAdapter
from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from consume_timeseries import run_consumer
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter


def _build_repository() -> ArcadeDbTimeseriesAdapter:
    return ArcadeDbTimeseriesAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=os.environ.get("ARCADEDB_PASSWORD", "testBIM123"),
    )


def _build_graph_repository() -> ArcadeDbRepositoryAdapter:
    return ArcadeDbRepositoryAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=os.environ.get("ARCADEDB_PASSWORD", "testBIM123"),
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
        password=os.environ.get("ARCADEDB_PASSWORD", "testBIM123"),
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_arcadedb_ready()
    thread = threading.Thread(target=run_consumer, name="nats-timeseries-consumer", daemon=True)
    thread.start()
    yield


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
