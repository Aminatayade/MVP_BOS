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
from contextlib import asynccontextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapters.arcadedb_ts_adapter import ArcadeDbTimeseriesAdapter
from consume_timeseries import run_consumer


def _build_repository() -> ArcadeDbTimeseriesAdapter:
    return ArcadeDbTimeseriesAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=os.environ.get("ARCADEDB_PASSWORD", "testBIM123"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
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
